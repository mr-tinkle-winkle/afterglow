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
                self._draw_segment(painter, seg, local, cw, ch, k)
        painter.end()
        return out

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
                self._draw_text(painter, part, ch)
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

    def _draw_text(self, painter, part, ch) -> None:
        st = part.text
        if st is None or not st.text:
            return
        font = QFont(st.font_family)
        font.setPixelSize(max(1, int(st.font_size * ch)))
        font.setBold(st.bold)
        font.setItalic(st.italic)
        path = QPainterPath()
        lines = st.text.split("\n")
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(font)
        total_h = fm.lineSpacing() * len(lines)
        for i, line in enumerate(lines):
            w = fm.horizontalAdvance(line)
            path.addText(QPointF(-w / 2, -total_h / 2 + fm.ascent() + i * fm.lineSpacing()), font, line)
        if st.outline_width > 0:
            pen = QPen(QColor(st.outline_color), st.outline_width * ch / 1080 * 2)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.strokePath(path, pen)
        painter.fillPath(path, QColor(st.color))

    # ---- audio -----------------------------------------------------------
    def audio(self, t0: float, n: int) -> np.ndarray:
        """Mixed timeline audio for [t0, t0 + n/RATE) as float32 (n, 2)."""
        out = np.zeros((n, 2), np.float64)
        if n <= 0:
            return out.astype(np.float32)
        t1 = t0 + n / RATE
        times = t0 + np.arange(n) / RATE
        for track in self.project.tracks:
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


def previous_on_track(track: Track, seg: Segment) -> "Segment | None":
    """The segment on `track` that ends exactly where `seg` starts (the
    outgoing side of seg's transition), if any."""
    for o in track.segments:
        if o.id != seg.id and abs(o.end - seg.start) < 1e-4:
            return o
    return None


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


def export(project: Project, out_path: str, progress: "Callable[[float], None] | None" = None,
           cancel: "threading.Event | None" = None, crf: int = 18, preset: str = "veryfast",
           width: "int | None" = None, height: "int | None" = None) -> None:
    """Render the whole project to an H.264/AAC MP4 at the canvas size and
    fps. Writes <out_path>.render_tmp.mp4 first and moves it into place only
    when complete. progress(fraction 0..1) is called as frames are encoded."""
    duration = project.duration
    if duration <= EPS:
        raise ValueError("Nothing to export: the timeline is empty.")
    width = width or project.width
    height = height or project.height
    width, height = width - width % 2, height - height % 2
    rate = media.fps_fraction(project.fps)
    n_frames = max(1, int(math.ceil(duration * rate - 1e-6)))
    tmp = out_path + ".render_tmp.mp4"
    renderer = Renderer(project)
    try:
        with av.open(tmp, "w", format="mp4") as out:
            vs = out.add_stream("libx264", rate=rate)
            vs.width, vs.height = width, height
            vs.pix_fmt = "yuv420p"
            vs.options = {"crf": str(crf), "preset": preset}
            vs.thread_type = "AUTO"
            has_audio = any(s.has_audio and not s.muted for s in project.all_segments())
            aus = None
            if has_audio:
                aus = out.add_stream("aac", rate=RATE)
                aus.layout = "stereo"
                aus.bit_rate = 192000
            samples_done = 0
            total_samples = int(round(n_frames / rate * RATE))
            for i in range(n_frames):
                if cancel is not None and cancel.is_set():
                    raise ExportCancelled()
                t = float(i / rate) + 1e-5
                img = renderer.frame(t, width, height)
                ptr = img.constBits()
                arr = np.frombuffer(ptr, np.uint8, count=img.sizeInBytes()).reshape(height, img.bytesPerLine() // 4, 4)[:, :width]
                vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(arr), format="bgra")
                vf.pts = i
                for pkt in vs.encode(vf):
                    out.mux(pkt)
                if aus is not None:
                    target = min(total_samples, int(round((i + 1) / rate * RATE)))
                    while samples_done < target:
                        n = min(1024, target - samples_done)
                        data = renderer.audio(samples_done / RATE, n)
                        af = av.AudioFrame.from_ndarray(np.ascontiguousarray(data.T), format="fltp", layout="stereo")
                        af.sample_rate = RATE
                        af.pts = samples_done
                        for pkt in aus.encode(af):
                            out.mux(pkt)
                        samples_done += n
                if progress is not None:
                    progress((i + 1) / n_frames)
            for pkt in vs.encode(None):
                out.mux(pkt)
            if aus is not None:
                for pkt in aus.encode(None):
                    out.mux(pkt)
        os.replace(tmp, out_path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    finally:
        renderer.close()
