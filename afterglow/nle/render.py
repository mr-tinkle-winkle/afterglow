"""
The Advanced Editor's renderer. ONE renderer produces both the preview
frames and the exported file, so what's seen while editing is exactly
what gets saved -- there is no second (e.g. ffmpeg filtergraph)
implementation that could drift from the preview.

- Renderer.frame(t, w, h) -> QImage of the composited canvas at time t,
  scaled to w x h (preview: small; export: canvas size).
- Renderer.audio(t0, n) -> float32 (n, 2) of the mixed timeline audio.
- export(project, out_path, ...) renders every frame + all audio and
  encodes H.264/AAC MP4 (written to a temp file, then atomically moved
  into place, so a failed/cancelled export never damages the target).

Compositing rules:
- Tracks are listed top to bottom; the TOP track draws over the rest.
- Within a segment, parts draw in list order (later over earlier).
- A picture is fitted (letterboxed) into the canvas, cropped first,
  then placed by the segment's transform (x/y offset, scale, rotation)
  times the zoom filter and any keyframes.
- Opacity = fade envelope x "opacity" keyframes. Audio gain = segment
  volume ("volume" keyframes override it) x part gain x fade envelope.
- Gaps (and time past a shorter part inside a merged segment) are black
  / silent.

Transitions (Segment.transition_in) blend from the segment that ends
exactly where this one starts on the same track. During the transition
that previous segment keeps playing past its own end (source "handle"
material, holding the last frame if the file runs out), so nothing
freezes. Kinds: crossfade, blur (blur out -> blur in), slide and fade
(directional soft wipe), each of the last two for destination /
original / both, from top / right / bottom / left. Audio crossfades
over the same span. GIF parts loop. Speed changes resample audio
(pitch follows speed, like a tape) -- pitch-preserving stretch is
planned.
"""
from __future__ import annotations

import math
import os
import queue
import threading
from typing import Callable

import av
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen

from . import media
from .model import EPS, KIND_AV, KIND_GIF, KIND_IMAGE, KIND_TEXT, Keyframe, Part, Project, Segment, Track

RATE = media.AUDIO_RATE


# =========================================================================
# animated values
# =========================================================================

def eval_keyframes(kfs: "list[Keyframe] | None", t: float, default: float) -> float:
    if not kfs:
        return default
    if t <= kfs[0].t:
        return kfs[0].value
    if t >= kfs[-1].t:
        return kfs[-1].value
    for a, b in zip(kfs, kfs[1:]):
        if a.t <= t <= b.t:
            if a.easing == "hold" or b.t - a.t < EPS:
                return a.value
            u = (t - a.t) / (b.t - a.t)
            if a.easing == "ease":
                u = u * u * (3 - 2 * u)       # smoothstep
            return a.value + (b.value - a.value) * u
    return default


def fade_envelope(seg: Segment, local: "float | np.ndarray"):
    """1.0 in the middle, ramping to 0 over fade_in/fade_out at the ends.
    Works on a float or a numpy array of segment-local times."""
    dur = seg.duration
    env = np.ones_like(local, dtype=np.float64) if isinstance(local, np.ndarray) else 1.0
    if seg.fade_in > EPS:
        env = env * np.clip(local / seg.fade_in, 0.0, 1.0)
    if seg.fade_out > EPS:
        env = env * np.clip((dur - local) / seg.fade_out, 0.0, 1.0)
    return env


def zoom_factor(seg: Segment, local: float) -> float:
    """Zoom filter: 1 -> zoom_amount over zoom_in s (eased), hold, then
    back to 1 over the last zoom_out s. Both 0 = constant zoom."""
    amt = seg.zoom_amount
    if amt <= 1.0 + EPS:
        return 1.0
    if seg.zoom_in <= EPS and seg.zoom_out <= EPS:
        return amt
    u = 1.0
    if seg.zoom_in > EPS and local < seg.zoom_in:
        u = local / seg.zoom_in
    if seg.zoom_out > EPS and local > seg.duration - seg.zoom_out:
        u = min(u, (seg.duration - local) / seg.zoom_out)
    u = max(0.0, min(1.0, u))
    u = u * u * (3 - 2 * u)
    return 1.0 + (amt - 1.0) * u


# =========================================================================
# renderer
# =========================================================================

class Renderer:
    """Holds decoders/caches for one consumer (the preview, or one export).
    Not thread-safe -- use one per thread."""

    def __init__(self, project: Project):
        self.project = project
        self._video: dict[str, media.VideoSource] = {}
        self._images: dict[str, QImage] = {}
        self._frames = media.FrameCache()

    def close(self) -> None:
        for v in self._video.values():
            v.close()
        self._video.clear()

    # ---- video -----------------------------------------------------------
    def _source(self, path: str) -> "media.VideoSource | None":
        src = self._video.get(path)
        if src is None:
            if not os.path.exists(path):
                return None
            try:
                src = media.VideoSource(path)
            except Exception:
                return None
            self._video[path] = src
        return src

    def _picture(self, part: Part, local: float, need_w: float, need_h: float) -> "QImage | None":
        """The part's source picture at segment-local time `local`, decoded
        at (about) the resolution it will be drawn at."""
        if part.kind == KIND_IMAGE:
            img = self._images.get(part.source)
            if img is None:
                img = QImage(part.source)
                self._images[part.source] = img
            return None if img.isNull() else img
        src = self._source(part.source)
        if src is None:
            return None
        st = part.source_time(local)
        if part.kind == KIND_GIF and part.source_duration > EPS:
            st = st % part.source_duration     # GIFs loop for as long as the segment lasts
        frame = src.frame_at(st)
        if frame is None:
            return None
        # Decode size: never more than the source, never much more than needed.
        scale = min(1.0, max(need_w / max(src.width, 1), need_h / max(src.height, 1)))
        dw = max(2, int(math.ceil(src.width * scale / 2) * 2))
        dh = max(2, int(math.ceil(src.height * scale / 2) * 2))
        key = (part.source, frame.pts, dw, dh)
        img = self._frames.get(key)
        if img is None:
            img = media.frame_to_qimage(frame, dw, dh)
            self._frames.put(key, img)
        return img

    def frame(self, t: float, width: "int | None" = None, height: "int | None" = None) -> QImage:
        p = self.project
        cw, ch = p.width, p.height
        width, height = width or cw, height or ch
        out = QImage(width, height, QImage.Format_RGB32)
        out.fill(QColor(0, 0, 0))
        painter = QPainter(out)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        k = width / cw                          # preview scale (canvas units -> output px)
        painter.scale(k, height / ch)
        for track in reversed(p.tracks):        # bottom first, top track drawn last (on top)
            if track.hidden:
                continue
            for seg in track.segments:
                if not seg.covers(t):
                    continue
                local = t - seg.start
                tr = seg.transition_in
                prev = previous_on_track(track, seg) if tr is not None and tr.duration > EPS else None
                if prev is not None and local < tr.duration:
                    self._draw_transition(painter, prev, seg, t, tr, width, height, cw, ch, k)
                    continue
                if not seg.visible or not seg.has_video:
                    continue
                if seg.shadow:
                    painter.save()
                    painter.resetTransform()
                    painter.drawImage(0, 0, self._layer(seg, local, width, height, cw, ch, k))
                    painter.restore()
                    continue
                self._draw_segment(painter, seg, local, cw, ch, k)
        painter.end()
        return out

    # ---- export fast path ------------------------------------------------
    def passthrough_frame(self, t: float, width: int, height: int):
        """The decoded source frame itself (as yuv420p), when the output at t
        is exactly ONE untouched full-frame video picture: canvas-sized
        source, no transform/crop/zoom/keyframes/fade/opacity/transition/
        shadow/overlays. Plain cuts and trims -- the common edit -- then skip
        converting to RGB, painting and converting back, which was most of
        the export time. None when anything is drawn differently."""
        p = self.project
        found = None
        for track in p.tracks:
            if getattr(track, "hidden", False):
                continue
            for seg in track.segments:
                if not seg.covers(t) or not seg.visible or not seg.has_video:
                    continue
                if found is not None:
                    return None
                found = (track, seg)
        if found is None:
            return None
        track, seg = found
        local = t - seg.start
        if (seg.transition_in is not None and local < seg.transition_in.duration) or seg.keyframes \
                or not seg.transform.is_identity() or zoom_factor(seg, local) != 1.0 \
                or float(fade_envelope(seg, local)) < 1.0 or getattr(seg, "shadow", False):
            return None
        parts = [pt for pt in seg.parts if pt.has_video and pt.visible and pt.active_at(local)]
        if len(parts) != 1 or parts[0].kind != KIND_AV:
            return None
        part = parts[0]
        src = self._source(part.source)
        if src is None or src.width != p.width or src.height != p.height or (width, height) != (p.width, p.height):
            return None
        frame = src.frame_at(part.source_time(local))
        if frame is None:
            return None
        return frame

    # ---- transitions -----------------------------------------------------
    def _layer(self, seg: Segment, local: float, width: int, height: int, cw: int, ch: int, k: float,
               extend: bool = False) -> QImage:
        img = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        if seg.visible and seg.has_video:
            lp = QPainter(img)
            lp.setRenderHint(QPainter.SmoothPixmapTransform)
            lp.setRenderHint(QPainter.Antialiasing)
            lp.scale(k, height / ch)
            self._draw_segment(lp, seg, local, cw, ch, k, extend=extend)
            lp.end()
            if seg.shadow:
                img = add_drop_shadow(img, seg, height)
        return img

    def _draw_transition(self, painter: QPainter, prev: Segment, seg: Segment, t: float, tr,
                         width: int, height: int, cw: int, ch: int, k: float) -> None:
        u = max(0.0, min(1.0, (t - seg.start) / tr.duration))
        a = self._layer(prev, t - prev.start, width, height, cw, ch, k, extend=True)
        b = self._layer(seg, t - seg.start, width, height, cw, ch, k)
        comp = compose_transition(a, b, tr.kind, u, tr.target, tr.direction)
        painter.save()
        painter.resetTransform()
        painter.drawImage(0, 0, comp)
        painter.restore()

    def _draw_segment(self, painter: QPainter, seg: Segment, local: float, cw: int, ch: int, k: float,
                      extend: bool = False) -> None:
        """extend=True: `local` may be past the segment's end (the outgoing
        side of a transition) -- the part(s) that reach the end keep
        playing their source past src_out, and fades/keyframes hold their
        end values."""
        kf = seg.keyframes
        env_local = min(local, seg.duration - 1e-6) if extend else local
        opacity = float(fade_envelope(seg, env_local)) * eval_keyframes(kf.get("opacity"), local, 1.0)
        if opacity <= 0.001:
            return
        tr = seg.transform
        x = eval_keyframes(kf.get("x"), local, tr.x)
        y = eval_keyframes(kf.get("y"), local, tr.y)
        scale = eval_keyframes(kf.get("scale"), local, tr.scale) * zoom_factor(seg, env_local)
        rotation = eval_keyframes(kf.get("rotation"), local, tr.rotation)
        for part in seg.parts:
            if not (part.has_video and part.visible):
                continue
            if extend and local >= seg.duration - EPS:
                if not (part.end >= seg.duration - EPS and local >= part.offset - EPS):
                    continue
            elif not part.active_at(local):
                continue
            painter.save()
            painter.setOpacity(max(0.0, min(1.0, opacity)))
            painter.translate(cw / 2 + x * cw, ch / 2 + y * ch)
            painter.rotate(rotation)
            painter.scale(scale, scale)
            if part.kind == KIND_TEXT:
                tip = None
                st = part.text
                if st is not None and st.bubble:
                    # The tail tip is an offset from the bubble's (animated)
                    # position, in canvas units; bring it into this segment's
                    # local (rotated/scaled) space.
                    dx = eval_keyframes(kf.get("tail_x"), local, st.tail_x) * cw
                    dy = eval_keyframes(kf.get("tail_y"), local, st.tail_y) * ch
                    a = -math.radians(rotation)
                    lx = (dx * math.cos(a) - dy * math.sin(a)) / max(scale, 1e-6)
                    ly = (dx * math.sin(a) + dy * math.cos(a)) / max(scale, 1e-6)
                    tip = QPointF(lx, ly)
                self._draw_text(painter, part, ch, local=max(0.0, min(local - part.offset, part.duration)),
                                duration=part.duration, tip=tip)
            else:
                self._draw_picture(painter, part, local, tr, cw, ch, scale * k)
            painter.restore()

    def _draw_picture(self, painter, part, local, tr, cw, ch, draw_scale) -> None:
        # Fitted size of the cropped picture in canvas units (before scale).
        src = self._source(part.source) if part.kind != KIND_IMAGE else None
        sw = src.width if src else None
        sh = src.height if src else None
        if src is None:
            img0 = self._picture(part, local, 1, 1)
            if img0 is None:
                return
            sw, sh = img0.width(), img0.height()
        cl, ct, cr, cb = tr.crop_left, tr.crop_top, tr.crop_right, tr.crop_bottom
        crop_w = max(1e-6, 1 - cl - cr) * sw
        crop_h = max(1e-6, 1 - ct - cb) * sh
        fit = min(cw / crop_w, ch / crop_h)
        bw, bh = crop_w * fit, crop_h * fit
        need_w = bw * draw_scale / max(1e-6, 1 - cl - cr)
        need_h = bh * draw_scale / max(1e-6, 1 - ct - cb)
        img = self._picture(part, local, need_w, need_h)
        if img is None:
            return
        iw, ih = img.width(), img.height()
        src_rect = QRectF(cl * iw, ct * ih, (1 - cl - cr) * iw, (1 - ct - cb) * ih)
        painter.drawImage(QRectF(-bw / 2, -bh / 2, bw, bh), img, src_rect)

    def _draw_text(self, painter, part, ch, local: float = 0.0, duration: float = 0.0, tip=None) -> None:
        st = part.text
        if st is None or not st.text:
            return
        lay = text_layout(st, ch)
        text_alpha = 1.0
        if st.bubble:
            g = grow_progress(st, local, duration)
            path, center, scale, text_alpha = bubble_shape(st.bubble, bubble_body(lay), tip, g,
                                                           shrinking=grow_phase_out(st, local, duration))
            # When a text transition covers this phase it decides what shows,
            # instead of the plain fade that goes with the grow.
            in_phase = local < duration / 2
            if (in_phase and has_text_in(st)) or (not in_phase and (st.type_out > EPS or st.delay_out > EPS)):
                text_alpha = 1.0 if scale > 0 else 0.0
            if not path.isEmpty():
                fill = QColor(st.bubble_fill)
                fill.setAlphaF(fill.alphaF() * (1.0 - max(0.0, min(1.0, st.bubble_fill_transparency))))
                painter.fillPath(path, fill)
                if st.bubble_outline_width > 0:
                    line = QColor(st.bubble_outline)
                    line.setAlphaF(line.alphaF() * (1.0 - max(0.0, min(1.0, st.bubble_outline_transparency))))
                    pen = QPen(line, st.bubble_outline_width * ch / 1080)
                    pen.setJoinStyle(Qt.RoundJoin)
                    painter.strokePath(path, pen)
            if text_alpha <= 0.001:
                return
            painter.save()
            painter.translate(center)
            painter.scale(scale, scale)
            painter.setOpacity(painter.opacity() * text_alpha)
            self._draw_text_body(painter, st, lay, ch, local, duration)
            painter.restore()
            return
        self._draw_text_body(painter, st, lay, ch, local, duration)

    def _draw_text_body(self, painter, st, lay, ch, local: float, duration: float) -> None:
        shown, cursor = typed_state(st, local, duration)
        alphas = delay_alphas(st, local, duration)        # None = all fully visible
        pen = None
        if st.outline_width > 0:
            pen = QPen(QColor(st.outline_color), st.outline_width * ch / 1080 * 2)
            pen.setJoinStyle(Qt.RoundJoin)
        fm = lay["fm"]
        base_opacity = painter.opacity()
        path = QPainterPath()
        remaining = shown
        cursor_at = None
        index = 0                                          # character index into st.text
        for i, line in enumerate(lay["lines"]):
            vis = line[:max(0, remaining)]
            x0 = -lay["widths"][i] / 2
            baseline = -lay["total_h"] / 2 + lay["ascent"] + i * lay["spacing"]
            if vis:
                if alphas is None:
                    path.addText(QPointF(x0, baseline), lay["font"], vis)
                else:
                    for j, chh in enumerate(vis):
                        a = alphas[index + j]
                        if a <= 0.001 or chh.isspace():
                            continue
                        cp = QPainterPath()
                        cp.addText(QPointF(x0 + fm.horizontalAdvance(line[:j]), baseline), lay["font"], chh)
                        painter.setOpacity(base_opacity * a)
                        if pen is not None:
                            painter.strokePath(cp, pen)
                        painter.fillPath(cp, QColor(st.color))
                    painter.setOpacity(base_opacity)
            if remaining >= 0:
                cursor_at = (x0 + fm.horizontalAdvance(vis), baseline)
            index += len(line) + 1
            remaining -= len(line) + 1          # +1 for the newline
            if remaining < 0:
                break
        if alphas is None and not path.isEmpty():
            if pen is not None:
                painter.strokePath(path, pen)
            painter.fillPath(path, QColor(st.color))
        if cursor and cursor_at is not None:
            w = max(1.0, fm.height() * 0.07)
            r = QRectF(cursor_at[0] + w * 0.6, cursor_at[1] - fm.ascent(), w, fm.ascent() + fm.descent())
            if pen is not None:
                cp = QPainterPath()
                cp.addRect(r)
                painter.strokePath(cp, pen)
            painter.fillRect(r, QColor(st.color))

    # ---- audio -----------------------------------------------------------
    def audio(self, t0: float, n: int) -> np.ndarray:
        """Mixed timeline audio for [t0, t0 + n/RATE) as float32 (n, 2)."""
        out = np.zeros((n, 2), np.float64)
        if n <= 0:
            return out.astype(np.float32)
        t1 = t0 + n / RATE
        times = t0 + np.arange(n) / RATE
        for track in self.project.tracks:
            if track.hidden:
                continue
            for seg in track.segments:
                tr = seg.transition_in
                prev = previous_on_track(track, seg) if tr is not None and tr.duration > EPS else None
                ramp = None
                if prev is not None:
                    # Outgoing side keeps playing under the incoming one, fading out.
                    tail0, tail1 = seg.start, seg.start + tr.duration
                    if tail1 > t0 and tail0 < t1:
                        u = np.clip((times - seg.start) / tr.duration, 0.0, 1.0)
                        mask = (times >= tail0 - EPS) & (times < tail1 - EPS)
                        self._mix(out, times, prev, mask_extra=mask, gain_extra=1.0 - u, extend=True)
                    ramp = np.clip((times - seg.start) / tr.duration, 0.0, 1.0)
                if seg.end <= t0 or seg.start >= t1:
                    continue
                self._mix(out, times, seg, gain_extra=ramp)
        np.clip(out, -1.0, 1.0, out=out)
        return out.astype(np.float32)

    def _mix(self, out, times, seg: Segment, mask_extra=None, gain_extra=None, extend: bool = False) -> None:
        if seg.muted or not seg.has_audio:
            return
        local = times - seg.start
        if extend:
            seg_mask = local >= -EPS
        else:
            seg_mask = (local >= -EPS) & (local < seg.duration - EPS)
        if mask_extra is not None:
            seg_mask = seg_mask & mask_extra
        if not seg_mask.any():
            return
        env_local = np.minimum(local, seg.duration - 1e-6) if extend else local
        if "volume" in seg.keyframes:
            vol = np.array([eval_keyframes(seg.keyframes["volume"], lt, seg.volume) for lt in env_local])
        else:
            vol = seg.volume
        env = np.asarray(fade_envelope(seg, env_local) * vol, dtype=np.float64) * np.ones_like(local)
        if gain_extra is not None:
            env = env * gain_extra
        for part in seg.parts:
            if not part.has_audio or part.gain <= 0 or part.kind not in (KIND_AV, KIND_GIF):
                continue
            if extend:
                reaches_end = part.end >= seg.duration - EPS
                pmask = seg_mask & (local >= part.offset - EPS) & (
                    (local < part.end - EPS) | (reaches_end & (local >= seg.duration - EPS)))
            else:
                pmask = seg_mask & (local >= part.offset - EPS) & (local < part.end - EPS)
            if not pmask.any():
                continue
            data = media.load_audio(part.source)
            if data.shape[0] == 0:
                continue
            src_t = part.src_in + (local[pmask] - part.offset) * part.speed
            idx = src_t * RATE
            if abs(part.speed - 1.0) < 1e-9:
                ii = np.rint(idx).astype(np.int64)
                ok = (ii >= 0) & (ii < data.shape[0])
                chunk = np.zeros((ii.shape[0], 2))
                chunk[ok] = data[ii[ok]]
            else:
                # Linear-interpolated resample (pitch follows speed).
                ok = (idx >= 0) & (idx <= data.shape[0] - 1)
                chunk = np.zeros((idx.shape[0], 2))
                base = np.arange(data.shape[0])
                for c in range(2):
                    chunk[ok, c] = np.interp(idx[ok], base, data[:, c])
            out[pmask] += chunk * (env[pmask] * part.gain)[:, None]


# ---- text layout / typing / bubbles (shared with the editor's preview) ----

def text_layout(st, ch: float) -> dict:
    """Font and line metrics for a TextStyle at canvas height ch. Text is
    centered on (0, 0): each line centered horizontally, the block
    centered vertically."""
    from PySide6.QtGui import QFontMetricsF
    font = QFont(st.font_family)
    font.setPixelSize(max(1, int(st.font_size * ch)))
    font.setBold(st.bold)
    font.setItalic(st.italic)
    fm = QFontMetricsF(font)
    lines = st.text.split("\n")
    widths = [fm.horizontalAdvance(line) for line in lines]
    total_h = fm.lineSpacing() * len(lines)
    return {"font": font, "fm": fm, "lines": lines, "widths": widths, "total_h": total_h,
            "spacing": fm.lineSpacing(), "ascent": fm.ascent(),
            "rect": QRectF(-max(widths or [0]) / 2, -total_h / 2, max(widths or [0]), total_h)}


TEXT_IN_AT = 0.55     # text transitions start when a bubble's grow-in is this far along
THOUGHT_TEXT_IN_AT = 0.75   # thought clouds: once the cloud is complete (see bubble_shape)
THOUGHT_CLOUD_START, THOUGHT_CLOUD_END = 0.3, THOUGHT_TEXT_IN_AT
TEXT_OUT_AT = 0.4     # ...and finish when its grow-out is this far along


def text_timing(st, duration: float) -> dict:
    """Every in/out effect of a text element, fitted inside its own time
    (`duration` = the element's length on the timeline): if the ins and
    outs don't fit they're scaled down together, so nothing runs past the
    element's end. Text transitions (type, delay) overlap the latter part
    of a bubble's grow-in and the early part of its grow-out."""
    duration = max(duration, 0.0)
    gi = st.grow_in if st.bubble else 0.0
    go = st.grow_out if st.bubble else 0.0
    if gi + go > duration > 0:
        k = duration / (gi + go)
        gi, go = gi * k, go * k
    ts = gi * (THOUGHT_TEXT_IN_AT if st.bubble == "thought" else TEXT_IN_AT)
    te = max(ts, duration - go * TEXT_OUT_AT)
    win = te - ts
    ti, to = st.type_in, st.type_out
    if ti + to > win > 0:
        k = win / (ti + to)
        ti, to = ti * k, to * k
    di, do = st.delay_in, st.delay_out
    if di + do > win > 0:
        k = win / (di + do)
        di, do = di * k, do * k
    return {"gi": gi, "go": go, "ts": ts, "te": te, "ti": ti, "to": to, "di": di, "do": do,
            "duration": duration}


def has_text_in(st) -> bool:
    return st.type_in > EPS or st.delay_in > EPS or st.delay_keyed


def typed_state(st, local: float, duration: float) -> "tuple[int, bool]":
    """(characters shown, whether the "|" cursor is drawn) at element-local
    time `local`. Newlines count as characters."""
    tm = text_timing(st, duration)
    local = max(0.0, min(local, tm["duration"]))
    tl = local - tm["ts"]
    tend = tm["te"] - tm["ts"]
    total = len(st.text)
    shown = total
    typing = False
    if tm["ti"] > EPS and tl < tm["ti"]:
        shown = int(total * max(0.0, tl) / tm["ti"])
        typing = True
    if tm["to"] > EPS and tl > tend - tm["to"]:
        shown = min(shown, int(math.ceil(total * max(0.0, tend - tl) / tm["to"])))
        typing = True
    cursor = st.type_cursor and (typing or int(local * 2) % 2 == 0)   # blinks once typed
    return shown, cursor


def bubble_body(lay: dict) -> QRectF:
    """The bubble's main body (an ellipse box) around the text."""
    r = lay["rect"]
    pad = lay["fm"].height() * 0.35
    w = r.width() * 1.32 + 2 * pad
    h = r.height() * 1.5 + 2 * pad
    return QRectF(-w / 2, -h / 2, w, h)


def _ease_out_cubic(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return 1 - (1 - u) ** 3


def _ease_out_back(u: float) -> float:
    u = max(0.0, min(1.0, u))
    c = 1.4
    return 1 + (c + 1) * (u - 1) ** 3 + c * (u - 1) ** 2


def _window(g: float, start: float, length: float) -> float:
    return max(0.0, min(1.0, (g - start) / max(length, 1e-6)))


def grow_phase_out(st, local: float, duration: float) -> bool:
    """True while the element is in its second half (the grow-out side)."""
    return local > max(duration, 0.0) / 2


def grow_progress(st, local: float, duration: float) -> float:
    """1.0 = fully formed; 0 = collapsed into the tail tip. Always within
    the element's own time (see text_timing)."""
    tm = text_timing(st, duration)
    local = max(0.0, min(local, tm["duration"]))
    g = 1.0
    if tm["gi"] > EPS:
        g = min(g, local / tm["gi"])
    if tm["go"] > EPS and duration > 0:
        g = min(g, (tm["duration"] - local) / tm["go"])
    return max(0.0, min(1.0, g))


def text_words(text: str) -> list:
    """[(first_char_index, length)] for every whitespace-separated word."""
    words = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        j = i
        while j < n and not text[j].isspace():
            j += 1
        words.append((i, j - i))
        i = j
    return words


WORD_FADE_MAX = 0.35


def word_start_times(st, duration: float) -> "list[tuple[float, float]]":
    """(start, fade length) per word for the Delay-in, in element-local
    seconds. Keyed words start at their keyframe; the rest are spread
    evenly between their nearest keyed neighbours -- or the start / end of
    the text's time where there's none on that side."""
    tm = text_timing(st, duration)
    words = text_words(st.text)
    W = len(words)
    if W == 0:
        return []
    ts, te, di = tm["ts"], tm["te"], tm["di"]
    keys = list(st.delay_word_times or [])[:W]
    keys += [None] * (W - len(keys))
    if st.delay_keyed:
        anchors = {}
        if keys[0] is None:
            anchors[0] = ts
        for i, k in enumerate(keys):
            if k is not None:
                anchors[i] = float(k)
        anchors.setdefault(W, te)               # virtual word after the last one
        idx = sorted(anchors)
        starts = []
        for i in range(W):
            if i in anchors:
                starts.append(anchors[i])
                continue
            lo = max(j for j in idx if j < i)
            hi = min(j for j in idx if j > i)
            starts.append(anchors[lo] + (anchors[hi] - anchors[lo]) * (i - lo) / (hi - lo))
        out = []
        for i, st_ in enumerate(starts):
            nxt = starts[i + 1] if i + 1 < W else te
            out.append((st_, max(0.05, min(WORD_FADE_MAX, nxt - st_ if nxt > st_ else WORD_FADE_MAX))))
        return out
    wd = di if W == 1 else di * 0.35
    return [(ts + (0.0 if W == 1 else (di - wd) * i / (W - 1)), wd) for i in range(W)]


def delay_alphas(st, local: float, duration: float) -> "list[float] | None":
    """Per-character opacity for the "Delay" text transition: words fade in
    one after another (evenly, or at their own keyframes) and, within a
    word, letters left to right; Delay out fades them away in reading
    order. Fitted into the element's time with the other effects (see
    text_timing)."""
    tm = text_timing(st, duration)
    din = tm["di"] if not st.delay_keyed else 1.0
    dout = tm["do"]
    if (st.delay_in <= EPS and not st.delay_keyed) and dout <= EPS:
        return None
    local = max(0.0, min(local, tm["duration"]))
    text = st.text
    n = len(text)
    words = text_words(text)
    alphas = [1.0] * n
    if not words:
        return alphas
    if st.delay_in > EPS or st.delay_keyed:
        for (first, L), (ws, wd) in zip(words, word_start_times(st, duration)):
            for k in range(L):
                ls = ws + wd * 0.6 * (k / max(L - 1, 1))
                alphas[first + k] = max(0.0, min(1.0, (local - ls) / max(wd * 0.4, 1e-6)))
    if dout > EPS and duration > 0:
        a_out = _fade_chars(list(reversed(words)), True, tm["te"] - local, dout, n)
        alphas = [min(x, y) for x, y in zip(alphas, a_out)]
    return alphas


def _fade_chars(words, letters_reversed: bool, t: float, D: float, n: int) -> "list[float]":
    out = [1.0] * n
    W = len(words)
    wd = D if W == 1 else D * 0.35                  # each word's own fade span
    for w, (first, L) in enumerate(words):
        ws = 0.0 if W == 1 else (D - wd) * w / (W - 1)
        for k in range(L):
            c = first + (L - 1 - k if letters_reversed else k)
            ls = ws + wd * 0.6 * (k / max(L - 1, 1))
            out[c] = max(0.0, min(1.0, (t - ls) / max(wd * 0.4, 1e-6)))
    return out


def bubble_path(kind: str, body: QRectF, tip: "QPointF | None") -> QPainterPath:
    """The fully formed bubble outline (see bubble_shape)."""
    return bubble_shape(kind, body, tip, 1.0)[0]


def _thought_trail(body: QRectF, tip: QPointF):
    """Circles from the cloud's edge to the tip: count follows the distance
    so their spacing (density) stays the same however far the tip is;
    they shrink toward the tip. Returns [(center, radius)] cloud-side first."""
    c = body.center()
    rx, ry = body.width() / 2, body.height() / 2
    dx, dy = tip.x() - c.x(), tip.y() - c.y()
    ang = math.atan2(dy / max(ry, 1e-6), dx / max(rx, 1e-6))
    edge = QPointF(c.x() + rx * math.cos(ang), c.y() + ry * math.sin(ang))
    dist = math.hypot(tip.x() - edge.x(), tip.y() - edge.y())
    big = max(min(rx, ry) * 0.3, (rx + ry) / 2 * 0.2)   # the circle next to the cloud
    out = []
    if dist <= 1e-6:
        return out, edge, ang
    ux, uy = (tip.x() - edge.x()) / dist, (tip.y() - edge.y()) / dist
    d = big * 1.25                            # first circle sits just off the cloud
    while True:
        f = min(1.0, d / dist)
        r = big * (1.0 - 0.7 * f)             # shrinking toward the tip
        if d + r > dist and out:
            break
        out.append((QPointF(edge.x() + ux * d, edge.y() + uy * d), r))
        if d + r >= dist:
            break
        # next circle: a gap proportional to the circles' size keeps the
        # look (density) the same however long the trail is
        r_next = big * (1.0 - 0.7 * min(1.0, (d + r * 2.3) / dist))
        d += r + r_next + big * 0.6
    return out, edge, ang


def bubble_shape(kind: str, body: QRectF, tip: "QPointF | None", g: float = 1.0, shrinking: bool = False):
    """Bubble outline at grow progress g (1 = fully formed), plus where the
    text goes: (path, text_center, text_scale, text_alpha).

    Speech grows out of the tail tip: the body flies from the tip to its
    place while scaling up (with a little overshoot) and the tail stretches
    between them. Thought sprouts its trail circles from the tip outward,
    then the cloud puffs up bump by bump starting on the trail's side.
    shrinking=True runs the motion back into the tip with its own easing:
    running the grow-in's overshoot easing backwards kept the bubble near
    full size until the last couple of frames and then it vanished, so the
    shrink uses a smooth ease that ends right at the element's end."""
    if shrinking:
        # starts gently, then collapses steadily into the tip -- reaching
        # (nearly) nothing on the last frame, never popping off at full size
        def ease_back(u):
            return math.sin(max(0.0, min(1.0, u)) * math.pi / 2)
        ease_cubic = ease_back
    else:
        ease_back, ease_cubic = _ease_out_back, _ease_out_cubic
    c_full = body.center()
    rx, ry = body.width() / 2, body.height() / 2
    tail = tip
    if tip is not None:
        dx, dy = tip.x() - c_full.x(), tip.y() - c_full.y()
        if (dx / max(rx, 1e-6)) ** 2 + (dy / max(ry, 1e-6)) ** 2 <= 1.0:
            tail = None                               # tip inside the bubble: no tail
    path = QPainterPath()
    if kind == "thought":
        trail, edge, ang = ([], c_full, 0.0) if tail is None else _thought_trail(body, tail)
        n_tr = len(trail)
        # phase 1: circles sprout from the tip toward the cloud
        for idx, (pc, r) in enumerate(trail):
            order = n_tr - 1 - idx                    # tip-most first
            k = ease_back(_window(g, 0.4 * order / max(n_tr, 1), 0.18)) if g < 1 else 1.0
            if k > 0.01:
                dot = QPainterPath()
                dot.addEllipse(pc, r * k, r * k)
                path = path.united(dot)
        # phase 2: the cloud puffs up, bump by bump, starting on the trail's
        # side. Each bump brings its own slice of the interior with it (a
        # wedge from the center out to the bump), so the middle fills in as
        # the bumps arrive -- there's no separate center oval popping in
        # ahead of them (all the wedges together ARE the middle).
        # The whole cloud is complete by THOUGHT_CLOUD_END -- the point where
        # the text starts -- so no side (the far one especially) is still
        # puffing up behind the words.
        if n_tr:
            cg = 1.0 if g >= 1 else _window(g, THOUGHT_CLOUD_START, THOUGHT_CLOUD_END - THOUGHT_CLOUD_START)
        else:
            cg = 1.0 if g >= 1 else _window(g, 0.0, THOUGHT_CLOUD_END)
        if cg > 0.001:
            c = c_full
            nb = 11
            circ = 2 * math.pi * math.sqrt((rx * rx + ry * ry) / 2)
            br = circ / nb * 0.62
            slot = 2 * math.pi / nb
            for i in range(nb):
                a = 2 * math.pi * i / nb
                d = abs(math.atan2(math.sin(a - ang), math.cos(a - ang))) / math.pi if tail is not None else i / nb
                u = _window(cg, 0.45 * d, 0.55) if cg < 1 else 1.0
                kb = ease_back(u) if u < 1 else 1.0
                reach = ease_cubic(u) if u < 1 else 1.0
                if kb <= 0.01:
                    continue
                wedge = QPainterPath()
                wedge.moveTo(c)
                for j in range(7):
                    aa = a - slot * 0.56 + slot * 1.12 * j / 6
                    wedge.lineTo(QPointF(c.x() + rx * 0.86 * math.cos(aa) * reach, c.y() + ry * 0.8 * math.sin(aa) * reach))
                wedge.closeSubpath()
                path = path.united(wedge)
                bump = QPainterPath()
                bc = QPointF(c.x() + rx * 0.86 * math.cos(a) * reach, c.y() + ry * 0.8 * math.sin(a) * reach)
                bump.addEllipse(bc, br * kb, br * 0.9 * kb)
                path = path.united(bump)
        return path, QPointF(0.0, 0.0), 1.0, (1.0 if g >= 1 else _window(cg, 0.7, 0.3))
    # speech
    e_pos = ease_cubic(g)
    e_size = max(0.0, ease_back(g)) if g < 1 else 1.0
    origin = tail if tail is not None else c_full
    cc = QPointF(origin.x() + (c_full.x() - origin.x()) * e_pos, origin.y() + (c_full.y() - origin.y()) * e_pos)
    if e_size <= 0.01:
        return path, cc, 0.0, 0.0
    rxs, rys = rx * e_size, ry * e_size
    path.addEllipse(cc, rxs, rys)
    if tail is not None:
        dx, dy = tail.x() - cc.x(), tail.y() - cc.y()
        if (dx / max(rxs, 1e-6)) ** 2 + (dy / max(rys, 1e-6)) ** 2 > 1.0:
            ang = math.atan2(dy / max(rys, 1e-6), dx / max(rxs, 1e-6))

            def on_ellipse(a):
                return QPointF(cc.x() + rxs * math.cos(a), cc.y() + rys * math.sin(a))
            spread = 0.32
            b1, b2 = on_ellipse(ang - spread), on_ellipse(ang + spread)
            mid = on_ellipse(ang)
            tp = QPainterPath()
            tp.moveTo(QPointF(cc.x() + (b1.x() - cc.x()) * 0.85, cc.y() + (b1.y() - cc.y()) * 0.85))
            tp.quadTo(QPointF((b1.x() + tail.x()) / 2 + (mid.x() - cc.x()) * 0.05,
                              (b1.y() + tail.y()) / 2 + (mid.y() - cc.y()) * 0.05), tail)
            tp.quadTo(QPointF((b2.x() + tail.x()) / 2, (b2.y() + tail.y()) / 2),
                      QPointF(cc.x() + (b2.x() - cc.x()) * 0.85, cc.y() + (b2.y() - cc.y()) * 0.85))
            tp.closeSubpath()
            path = path.united(tp)
    # text rides with the body; the painter maps local (0,0) = c_full
    text_center = QPointF(cc.x() - c_full.x() * e_size, cc.y() - c_full.y() * e_size)
    return path, text_center, e_size, (1.0 if g >= 1 else _window(g, 0.55, 0.45))


def previous_on_track(track: Track, seg: Segment) -> "Segment | None":
    """The segment on `track` that ends exactly where `seg` starts (the
    outgoing side of seg's transition), if any."""
    for o in track.segments:
        if o.id != seg.id and abs(o.end - seg.start) < 1e-4:
            return o
    return None


def add_drop_shadow(img: QImage, seg: Segment, out_h: int) -> QImage:
    """Composite a drop shadow under a transparent layer: the layer's own
    shape, tinted, softened, faded and offset."""
    w, h = img.width(), img.height()
    shadow = img.copy()
    p = QPainter(shadow)
    p.setCompositionMode(QPainter.CompositionMode_SourceIn)
    p.fillRect(shadow.rect(), QColor(seg.shadow_color))
    p.end()
    shadow = _blurred(shadow, max(0.0, min(1.0, seg.shadow_blur)) * 0.35)
    dist = seg.shadow_distance * out_h
    a = math.radians(seg.shadow_angle)
    out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setOpacity(max(0.0, min(1.0, seg.shadow_opacity)))
    p.drawImage(QPointF(dist * math.cos(a), dist * math.sin(a)), shadow)
    p.setOpacity(1.0)
    p.drawImage(0, 0, img)
    p.end()
    return out


def _smooth(u: float) -> float:
    return u * u * (3 - 2 * u)


def _blurred(img: QImage, amount: float) -> QImage:
    """Cheap blur: shrink then smooth-scale back up. amount 0..1."""
    if amount <= 0.01:
        return img
    f = 1.0 / (1.0 + amount * 30.0)
    w, h = img.width(), img.height()
    small = img.scaled(max(2, int(w * f)), max(2, int(h * f)), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


_DIRS = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}


def _wipe_mask(img: QImage, u: float, direction: str, reveal: bool) -> QImage:
    """Soft directional wipe mask applied to img (DestinationIn). The
    boundary travels from the `direction` edge across the frame.
    reveal=True keeps what the boundary has passed; False keeps the rest."""
    w, h = img.width(), img.height()
    dx, dy = _DIRS.get(direction, (-1, 0))
    feather = 0.35
    pos = u * (1 + feather)                      # boundary position, measured from the edge
    if dx:
        x0, x1 = (0.0, float(w)) if dx < 0 else (float(w), 0.0)
        g = QLinearGradient(x0, 0.0, x1, 0.0)
    else:
        y0, y1 = (0.0, float(h)) if dy < 0 else (float(h), 0.0)
        g = QLinearGradient(0.0, y0, 0.0, y1)
    steps = 24
    for i in range(steps + 1):
        d = i / steps                            # 0 = the named edge, 1 = far side
        alpha = max(0.0, min(1.0, (pos - d) / feather))
        if not reveal:
            alpha = 1.0 - alpha
        g.setColorAt(d, QColor(0, 0, 0, int(round(alpha * 255))))
    res = img.copy()
    p = QPainter(res)
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.fillRect(res.rect(), g)
    p.end()
    return res


def _as_array(img: QImage) -> np.ndarray:
    ptr = img.constBits()
    return np.frombuffer(ptr, np.uint8, count=img.sizeInBytes()).reshape(img.height(), img.bytesPerLine() // 4, 4)


def _mix_layers(a: QImage, b: QImage, u: float) -> QImage:
    """Exact premultiplied linear mix a*(1-u) + b*u (correct for layers with
    transparency too, e.g. a text element crossfading over video)."""
    a = a.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    b = b.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    mixed = _as_array(a).astype(np.float32) * (1.0 - u) + _as_array(b).astype(np.float32) * u
    arr = np.ascontiguousarray(np.clip(mixed + 0.5, 0, 255).astype(np.uint8))
    img = QImage(arr.data, a.width(), a.height(), arr.shape[1] * 4, QImage.Format_ARGB32_Premultiplied)
    return img.copy()


def compose_transition(a: QImage, b: QImage, kind: str, u: float, target: str = "both",
                       direction: str = "left") -> QImage:
    """Blend outgoing layer a into incoming layer b at progress u (0..1).
    Both are premultiplied ARGB of the same size; the result is too, and
    is drawn over whatever lower tracks show."""
    w, h = a.width(), a.height()
    out = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    if kind == "blur":
        p.drawImage(0, 0, _mix_layers(_blurred(a, min(1.0, 2 * u)), _blurred(b, min(1.0, 2 * (1 - u))), _smooth(u)))
    elif kind == "slide":
        s = _smooth(u)
        dx, dy = _DIRS.get(direction, (-1, 0))
        # Motion goes AWAY from the named side (in from the left -> moves right).
        if target == "destination":
            p.drawImage(0, 0, a)
            p.drawImage(int(round(dx * (1 - s) * w)), int(round(dy * (1 - s) * h)), b)
        elif target == "original":
            p.drawImage(0, 0, b)
            p.drawImage(int(round(-dx * s * w)), int(round(-dy * s * h)), a)
        else:  # both: push -- outgoing leaves as incoming arrives
            p.drawImage(int(round(-dx * s * w)), int(round(-dy * s * h)), a)
            p.drawImage(int(round(dx * (1 - s) * w)), int(round(dy * (1 - s) * h)), b)
    elif kind == "fade":
        if target == "destination":
            p.drawImage(0, 0, a)
            p.drawImage(0, 0, _wipe_mask(b, u, direction, reveal=True))
        elif target == "original":
            p.drawImage(0, 0, b)
            p.drawImage(0, 0, _wipe_mask(a, u, direction, reveal=False))
        else:  # both: soft wipe, the outgoing side also dims
            p.setOpacity(1.0 - 0.5 * u)
            p.drawImage(0, 0, a)
            p.setOpacity(1.0)
            p.drawImage(0, 0, _wipe_mask(b, u, direction, reveal=True))
    else:  # crossfade
        p.drawImage(0, 0, _mix_layers(a, b, u))
    p.end()
    return out


# =========================================================================
# export
# =========================================================================

class ExportCancelled(Exception):
    pass


def _auto_jobs(n_frames: int) -> int:
    """How many pieces to render in parallel. Each piece has its own
    renderer + encoder; the single-threaded Python/Qt picture work is the
    bottleneck on many-core machines, so splitting pays off there. Small
    machines/short edits use one pipeline."""
    cpus = os.cpu_count() or 2
    if n_frames < 240 or cpus < 6:
        return 1
    return max(1, min(6, cpus // 3, n_frames // 120))


def _frame_to_video_frame(renderer: "Renderer", t: float, width: int, height: int, last_src):
    """(av.VideoFrame for time t, the passthrough source frame or None)."""
    src = renderer.passthrough_frame(t, width, height)
    if src is not None and src is not last_src:
        vf = src.reformat(format="yuv420p") if src.format.name != "yuv420p" else src
        return vf, src
    img = renderer.frame(t, width, height)
    ptr = img.constBits()
    arr = np.frombuffer(ptr, np.uint8, count=img.sizeInBytes()).reshape(height, img.bytesPerLine() // 4, 4)[:, :width]
    return av.VideoFrame.from_ndarray(np.ascontiguousarray(arr), format="bgra"), None


def _new_video_stream(out, rate, width, height, crf, preset, threads=None):
    vs = out.add_stream("libx264", rate=rate)
    vs.width, vs.height = width, height
    vs.pix_fmt = "yuv420p"
    opts = {"crf": str(crf), "preset": preset}
    if threads:
        opts["threads"] = str(threads)
    vs.options = opts
    vs.thread_type = "AUTO"
    return vs


def export(project: Project, out_path: str, progress: "Callable[[float], None] | None" = None,
           cancel: "threading.Event | None" = None, crf: int = 18, preset: str = "veryfast",
           width: "int | None" = None, height: "int | None" = None, jobs: "int | None" = None) -> None:
    """Render the whole project to an H.264/AAC MP4 at the canvas size and
    fps. Writes <out_path>.render_tmp.mp4 first and moves it into place only
    when complete. progress(fraction 0..1) is called as frames are encoded.

    Speed: frames that are just one untouched full-frame clip skip the
    RGB round trip (Renderer.passthrough_frame); pictures are rendered on
    worker threads while encoding runs; and on many-core machines the
    timeline is split into `jobs` pieces rendered + encoded in parallel,
    then joined without re-encoding."""
    duration = project.duration
    if duration <= EPS:
        raise ValueError("Nothing to export: the timeline is empty.")
    width = width or project.width
    height = height or project.height
    width, height = width - width % 2, height - height % 2
    rate = media.fps_fraction(project.fps)
    n_frames = max(1, int(math.ceil(duration * rate - 1e-6)))
    jobs = _auto_jobs(n_frames) if jobs is None else max(1, min(jobs, n_frames))
    tmp = out_path + ".render_tmp.mp4"
    has_audio = any(s.has_audio and not s.muted for s in project.all_segments())
    from fractions import Fraction
    frame_tb = Fraction(rate.denominator, rate.numerator)
    done_frames = [0]
    lock = threading.Lock()

    def tick():
        with lock:
            done_frames[0] += 1
            n = done_frames[0]
        if progress is not None and jobs == 1:
            progress(n / n_frames)

    def cancelled():
        return cancel is not None and cancel.is_set()

    def encode_audio(out, aus):
        renderer = Renderer(project)
        try:
            total = int(round(n_frames / rate * RATE))
            done = 0
            while done < total:
                if cancelled():
                    raise ExportCancelled()
                n = min(1024, total - done)
                data = renderer.audio(done / RATE, n)
                af = av.AudioFrame.from_ndarray(np.ascontiguousarray(data.T), format="fltp", layout="stereo")
                af.sample_rate = RATE
                af.pts = done
                for pkt in aus.encode(af):
                    out.mux(pkt)
                done += n
            for pkt in aus.encode(None):
                out.mux(pkt)
        finally:
            renderer.close()

    def render_range(out, vs, f0, f1, threaded: bool):
        """Encode frames [f0, f1) into vs, pts starting at 0."""
        renderer = Renderer(project)
        frames: "queue.Queue" = queue.Queue(maxsize=8)
        stop = threading.Event()

        def produce():
            last = None
            try:
                for i in range(f0, f1):
                    if stop.is_set():
                        return
                    vf, last = _frame_to_video_frame(renderer, float(i / rate) + 1e-5, width, height, last)
                    frames.put(vf)
                frames.put(None)
            except BaseException as e:
                frames.put(e)
        worker = None
        if threaded:
            worker = threading.Thread(target=produce, daemon=True, name="export-render")
            worker.start()
        try:
            last = None
            for k, i in enumerate(range(f0, f1)):
                if cancelled():
                    raise ExportCancelled()
                if threaded:
                    vf = frames.get()
                    if isinstance(vf, BaseException):
                        raise vf
                else:
                    vf, last = _frame_to_video_frame(renderer, float(i / rate) + 1e-5, width, height, last)
                vf.pts = k
                vf.time_base = frame_tb
                for pkt in vs.encode(vf):
                    out.mux(pkt)
                tick()
            for pkt in vs.encode(None):
                out.mux(pkt)
        finally:
            stop.set()
            if worker is not None:
                while worker.is_alive():
                    try:
                        frames.get_nowait()
                    except queue.Empty:
                        pass
                    worker.join(timeout=0.05)
            renderer.close()

    parts_files: list = []
    try:
        if jobs == 1:
            with av.open(tmp, "w", format="mp4") as out:
                vs = _new_video_stream(out, rate, width, height, crf, preset)
                aus = None
                if has_audio:
                    aus = out.add_stream("aac", rate=RATE)
                    aus.layout = "stereo"
                    aus.bit_rate = 192000
                # Audio first is fine for mp4 (the muxer interleaves by time);
                # it's cheap next to video and keeps the video loop simple.
                if aus is not None:
                    encode_audio(out, aus)
                render_range(out, vs, 0, n_frames, threaded=True)
        else:
            cpus = os.cpu_count() or 2
            bounds = [round(n_frames * k / jobs) for k in range(jobs + 1)]
            errors: list = []
            threads = []
            for k in range(jobs):
                f0, f1 = bounds[k], bounds[k + 1]
                path = f"{out_path}.part{k}.mp4"
                parts_files.append(path)

                def job(path=path, f0=f0, f1=f1):
                    try:
                        with av.open(path, "w", format="mp4") as out:
                            vs = _new_video_stream(out, rate, width, height, crf, preset,
                                                   threads=max(1, cpus // jobs))
                            render_range(out, vs, f0, f1, threaded=False)
                    except BaseException as e:
                        errors.append(e)
                        if cancel is not None:
                            cancel.set()
                th = threading.Thread(target=job, daemon=True, name=f"export-part{k}")
                th.start()
                threads.append(th)
            audio_path = f"{out_path}.audio.m4a"
            if has_audio:
                parts_files.append(audio_path)
                with av.open(audio_path, "w", format="mp4") as aout:
                    aus = aout.add_stream("aac", rate=RATE)
                    aus.layout = "stereo"
                    aus.bit_rate = 192000
                    encode_audio(aout, aus)
            while any(th.is_alive() for th in threads):
                for th in threads:
                    th.join(timeout=0.1)
                if progress is not None:
                    progress(min(1.0, done_frames[0] / n_frames) * 0.99)
            if errors:
                raise errors[0]
            if cancelled():
                raise ExportCancelled()
            _join_parts(tmp, parts_files[:jobs], audio_path if has_audio else None, bounds, frame_tb)
            if progress is not None:
                progress(1.0)
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        for f in parts_files:
            try:
                os.remove(f)
            except OSError:
                pass


def _join_parts(out_path: str, video_parts: list, audio_path: "str | None", bounds: list, frame_tb) -> None:
    """Concatenate separately encoded video pieces (identical settings, each
    starting on a keyframe) and the audio into one MP4, copying packets."""
    with av.open(out_path, "w", format="mp4") as out:
        first = av.open(video_parts[0])
        ovs = out.add_stream_from_template(first.streams.video[0]) if hasattr(out, "add_stream_from_template") \
            else out.add_stream(template=first.streams.video[0])
        first.close()
        oas = None
        ain = None
        if audio_path:
            ain = av.open(audio_path)
            oas = out.add_stream_from_template(ain.streams.audio[0]) if hasattr(out, "add_stream_from_template") \
                else out.add_stream(template=ain.streams.audio[0])
        for k, path in enumerate(video_parts):
            offset_s = bounds[k] * frame_tb          # seconds
            with av.open(path) as inp:
                st = inp.streams.video[0]
                off = int(round(offset_s / st.time_base))
                for pkt in inp.demux(st):
                    if pkt.dts is None and pkt.pts is None:
                        continue
                    if pkt.pts is not None:
                        pkt.pts += off
                    if pkt.dts is not None:
                        pkt.dts += off
                    pkt.stream = ovs
                    out.mux(pkt)
        if ain is not None:
            for pkt in ain.demux(ain.streams.audio[0]):
                if pkt.dts is None:
                    continue
                pkt.stream = oas
                out.mux(pkt)
            ain.close()
