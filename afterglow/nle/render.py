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
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen

from . import comic, media
from .model import EPS, KIND_AV, KIND_GIF, KIND_IMAGE, KIND_TEXT, Keyframe, OverlayPiece, Part, Project, Segment, Track

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

    def __init__(self, project: Project, overlays: bool = True):
        """overlays=False: every input-overlay element -- attached pieces AND
        detached overlay segments -- is left out, exactly as if hidden (the
        export renders the video this way: nothing is ever burned in). The
        Editor's overlay toggle flips `overlays` on the preview's renderer."""
        self.project = project
        self.overlays = overlays
        self._only: "str | None" = None     # frame_piece(): draw ONLY this overlay piece's content
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
        return self._compose(t, width, height)

    def frame_piece(self, t: float, name: str, width: "int | None" = None, height: "int | None" = None) -> QImage:
        """ONLY the content of input-overlay piece `name` at time t, on a
        transparent background (ARGB32): its attached pieces (drawn inside
        their segment's transform, without the video itself) and its
        detached overlay segments with their transforms, keyframes and
        effects, stacked in track order. Used to build the output's overlay
        sidecar."""
        self._only = name
        try:
            return self._compose(t, width, height, transparent=True)
        finally:
            self._only = None

    def _compose(self, t: float, width: "int | None", height: "int | None", transparent: bool = False) -> QImage:
        p = self.project
        cw, ch = p.width, p.height
        width, height = width or cw, height or ch
        if transparent:
            out = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
            out.fill(Qt.transparent)
        else:
            out = QImage(width, height, QImage.Format_RGB32)
            out.fill(QColor(0, 0, 0))
        painter = QPainter(out)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.Antialiasing)
        k = width / cw                          # preview scale (canvas units -> output px)
        painter.scale(k, height / ch)
        only = self._only
        for track in reversed(p.tracks):        # bottom first, top track drawn last (on top)
            if track.hidden:
                continue
            for seg in track.segments:
                if not seg.covers(t):
                    continue
                if seg.overlay_piece and (not self.overlays or (only is not None and seg.overlay_piece != only)):
                    continue                    # an overlay element, left out (as if hidden) / not this piece
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
                if seg.overlay_piece and not self.overlays:
                    continue                    # left out exactly as if hidden
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
        if self.overlays and any(o.visible for o in part.overlays):
            return None                         # attached overlay pieces are drawn on top
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
            mdx = mdy = mrot = 0.0
            msx = msy = 1.0
            if part.kind == KIND_TEXT and part.text is not None and comic.has_motion(part.text):
                # Bounce / Jiggle / Animate in-out / Idle: screen-space offset, extra
                # spin, scale and opacity on top of the transform (nle/comic.py)
                mdx, mdy, mrot, msx, msy, malpha = comic.text_motion(
                    part.text, max(0.0, min(local - part.offset, part.duration)), part.duration, cw, ch)
                if malpha < 1.0:
                    painter.setOpacity(painter.opacity() * max(0.0, malpha))
                if abs(msx) < 1e-4 or abs(msy) < 1e-4 or malpha <= 0.001:
                    painter.restore()
                    continue
            painter.translate(cw / 2 + x * cw + mdx, ch / 2 + y * ch + mdy)
            painter.rotate(rotation + mrot)
            painter.scale(scale * msx, scale * msy)
            if self._only is not None and not seg.overlay_piece:
                # frame_piece(): another clip -- its picture isn't part of the
                # overlay sidecar; only its attached pieces of this name are.
                if part.kind == KIND_AV and part.overlays:
                    box = self._fit_box(part, tr, cw, ch)
                    if box is not None:
                        self._draw_overlays(painter, part, local, cw, ch, scale * k, *box)
            elif part.kind == KIND_TEXT:
                tip = None
                st = part.text
                if st is not None and st.bubble:
                    # The tail tip is an offset from the bubble's (animated)
                    # position, in canvas units; bring it into this segment's
                    # local (rotated/scaled) space.
                    dx = eval_keyframes(kf.get("tail_x"), local, st.tail_x) * cw
                    dy = eval_keyframes(kf.get("tail_y"), local, st.tail_y) * ch
                    a = -math.radians(rotation + mrot)
                    lx = (dx * math.cos(a) - dy * math.sin(a)) / max(scale * abs(msx), 1e-6)
                    ly = (dx * math.sin(a) + dy * math.cos(a)) / max(scale * abs(msy), 1e-6)
                    tip = QPointF(lx, ly)
                self._draw_text(painter, part, ch, local=max(0.0, min(local - part.offset, part.duration)),
                                duration=part.duration, tip=tip)
            else:
                box = self._draw_picture(painter, part, local, tr, cw, ch, scale * k)
                # Attached input-overlay pieces ride on the picture, inside the
                # same transform (so moving/scaling/rotating the clip carries
                # them). Preview only: the export renders with overlays=False.
                if box is not None and part.overlays and self.overlays and part.kind == KIND_AV:
                    self._draw_overlays(painter, part, local, cw, ch, scale * k, *box)
            painter.restore()

    def _fit_box(self, part, tr, cw, ch) -> "tuple[float, float] | None":
        """(bw, bh): the part's cropped picture fitted into the canvas, in
        canvas units (before the segment's scale). None if unreadable."""
        src = self._source(part.source) if part.kind != KIND_IMAGE else None
        sw = src.width if src else None
        sh = src.height if src else None
        if src is None:
            img0 = self._picture(part, 0.0, 1, 1)
            if img0 is None:
                return None
            sw, sh = img0.width(), img0.height()
        cl, ct, cr, cb = tr.crop_left, tr.crop_top, tr.crop_right, tr.crop_bottom
        crop_w = max(1e-6, 1 - cl - cr) * sw
        crop_h = max(1e-6, 1 - ct - cb) * sh
        fit = min(cw / crop_w, ch / crop_h)
        return crop_w * fit, crop_h * fit

    def _draw_overlays(self, painter, part, local, cw, ch, draw_scale, bw, bh) -> None:
        """The part's attached overlay pieces, positioned as fractions of the
        displayed picture (bw x bh, centred on the origin), rotated about
        each piece's own centre. Pieces are transparent (ARGB) videos that
        share the part's source timeline."""
        for o in part.overlays:
            if not o.visible or (self._only is not None and o.name != self._only):
                continue
            src = self._source(o.source)
            if src is None:
                continue
            pw, ph = (o.width or src.width), (o.height or src.height)
            Wd = o.w * bw
            Hd = Wd * ph / max(1, pw)
            frame = src.frame_at(part.source_time(local))
            if frame is None:
                continue
            dw = max(2, min(src.width, int(math.ceil(Wd * draw_scale))))
            dh = max(2, min(src.height, int(math.ceil(Hd * draw_scale))))
            key = (o.source, frame.pts, dw, dh)
            img = self._frames.get(key)
            if img is None:
                img = media.frame_to_qimage(frame, dw, dh)
                self._frames.put(key, img)
            painter.save()
            painter.translate((o.x - 0.5) * bw + Wd / 2, (o.y - 0.5) * bh + Hd / 2)
            painter.rotate(o.rotation)
            painter.drawImage(QRectF(-Wd / 2, -Hd / 2, Wd, Hd), img)
            painter.restore()

    def _draw_picture(self, painter, part, local, tr, cw, ch, draw_scale) -> "tuple[float, float] | None":
        # Fitted size of the cropped picture in canvas units (before scale).
        box = self._fit_box(part, tr, cw, ch)
        if box is None:
            return None
        bw, bh = box
        cl, ct, cr, cb = tr.crop_left, tr.crop_top, tr.crop_right, tr.crop_bottom
        need_w = bw * draw_scale / max(1e-6, 1 - cl - cr)
        need_h = bh * draw_scale / max(1e-6, 1 - ct - cb)
        img = self._picture(part, local, need_w, need_h)
        if img is None:
            return None
        iw, ih = img.width(), img.height()
        src_rect = QRectF(cl * iw, ct * ih, (1 - cl - cr) * iw, (1 - ct - cb) * ih)
        painter.drawImage(QRectF(-bw / 2, -bh / 2, bw, bh), img, src_rect)
        return bw, bh

    def _draw_text(self, painter, part, ch, local: float = 0.0, duration: float = 0.0, tip=None) -> None:
        st = part.text
        if st is None or not (st.text or comic.valid_effect(st.comic_effect)
                              or bubble_image(st.image_path) is not None):
            return
        lay = text_layout(st, ch)
        text_alpha = 1.0
        if st.backdrop and comic.valid_backdrop(st.backdrop) and (st.text or lay.get("effect")):
            comic.draw_backdrop(painter, st, lay["rect"], local, ch, duration)
        if st.bubble:
            g = grow_progress(st, local, duration)
            variant = valid_variant(st.bubble, st.bubble_variant)
            anim_t = local * max(0.0, st.bubble_anim_speed)
            path, center, scale, text_alpha = bubble_shape(st.bubble, bubble_body(lay), tip, g,
                                                           shrinking=grow_phase_out(st, local, duration),
                                                           variant=variant, t=anim_t,
                                                           animated=st.bubble_animated)
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
                    pen.setJoinStyle(Qt.MiterJoin if variant in ("spiky", "jagged") else Qt.RoundJoin)
                    dash = bubble_dash(variant, anim_t, st.bubble_animated)
                    if dash is not None:
                        pattern, offset, cap = dash
                        pen.setCapStyle(cap)
                        pen.setDashPattern(pattern)
                        pen.setDashOffset(offset)
                    painter.strokePath(path, pen)
            if text_alpha <= 0.001:
                return
            painter.save()
            painter.translate(center)
            painter.scale(scale, scale)
            painter.setOpacity(painter.opacity() * text_alpha)
            self._draw_content(painter, st, lay, ch, local, duration)
            painter.restore()
            return
        self._draw_content(painter, st, lay, ch, local, duration)

    def _draw_content(self, painter, st, lay, ch, local: float, duration: float) -> None:
        """The picture or comic effect (if any) and the words, laid out by text_layout."""
        if lay.get("effect"):
            a = 1.0
            if has_text_in(st):
                tm = text_timing(st, duration)
                a = max(0.0, min(1.0, (local - tm["ts"]) / 0.25))
            if a > 0.001:
                painter.save()
                painter.setOpacity(painter.opacity() * a)
                speed = max(0.0, st.effect_speed)
                g, leaving = comic.effect_progress(st, local, duration)
                comic.draw_comic_effect(painter, lay["effect"], lay["image_rect"], max(0.0, local) * speed,
                                        comic.effect_color(st), animated=speed > EPS, g=g, out=leaving,
                                        variant=st.effect_variant, color2=st.effect_color2, side=st.effect_side)
                painter.restore()
        elif lay.get("image") is not None:
            a = 1.0
            if has_text_in(st):
                # with a type/delay effect the picture pops in as the words start
                tm = text_timing(st, duration)
                a = max(0.0, min(1.0, (local - tm["ts"]) / 0.25))
            if a > 0.001:
                painter.save()
                painter.setOpacity(painter.opacity() * a)
                painter.setRenderHint(QPainter.SmoothPixmapTransform)
                pic = bubble_image_at(st.image_path, max(0.0, local) * max(0.0, st.image_speed)) or lay["image"]
                painter.drawImage(lay["image_rect"], pic)
                painter.restore()
        if not st.text:
            return
        off = lay.get("text_offset")
        if off is not None and (off.x() or off.y()):
            painter.save()
            painter.translate(off)
            self._draw_text_body(painter, st, lay, ch, local, duration)
            painter.restore()
        else:
            self._draw_text_body(painter, st, lay, ch, local, duration)

    def _draw_text_body(self, painter, st, lay, ch, local: float, duration: float) -> None:
        """The words: typewriter, Delay fades, comic lettering (gradient fill,
        3D extrude, skew) and the per-letter motions (letters in/out,
        explode, wave, jitter, jumble -- nle/comic.py)."""
        shown, cursor = typed_state(st, local, duration)
        alphas = delay_alphas(st, local, duration)        # None = all fully visible
        per_letter = alphas is not None or comic.has_letter_motion(st)
        pen = None
        if st.outline_width > 0:
            pen = QPen(QColor(st.outline_color), st.outline_width * ch / 1080 * 2)
            pen.setJoinStyle(Qt.RoundJoin)
        if st.fill2:
            grad = QLinearGradient(QPointF(0, -lay["total_h"] / 2), QPointF(0, lay["total_h"] / 2))
            grad.setColorAt(0.0, QColor(st.color))
            grad.setColorAt(1.0, QColor(st.fill2))
            fill = QBrush(grad)
        else:
            fill = QBrush(QColor(st.color))
        depth = max(0.0, st.extrude) * ch / 1080
        steps = int(min(36, math.ceil(depth))) if depth > 0.5 else 0
        ext_col = QColor(st.extrude_color)
        fm = lay["fm"]
        em = lay["font"].pixelSize()
        base_opacity = painter.opacity()
        painter.save()
        if abs(st.skew) > 0.01:
            painter.shear(-math.tan(math.radians(max(-60.0, min(60.0, st.skew)))), 0.0)

        def extrusion(path):
            if steps:
                k = depth / steps
                for j in range(steps, 0, -1):
                    painter.fillPath(path.translated(k * j * 0.75, k * j), ext_col)
                if pen is not None:
                    painter.strokePath(path.translated(depth * 0.75, depth), pen)

        def front(path):
            if pen is not None:
                painter.strokePath(path, pen)
            painter.fillPath(path, fill)

        glyphs = []                                        # (char, x, baseline, index)
        remaining = shown
        cursor_at = None
        index = 0
        for i, line in enumerate(lay["lines"]):
            vis = line[:max(0, remaining)]
            x0 = -lay["widths"][i] / 2
            baseline = -lay["total_h"] / 2 + lay["ascent"] + i * lay["spacing"]
            for j, chh in enumerate(vis):
                glyphs.append((chh, x0 + fm.horizontalAdvance(line[:j]), baseline, index + j))
            if remaining >= 0:
                cursor_at = (x0 + fm.horizontalAdvance(vis), baseline)
            index += len(line) + 1
            remaining -= len(line) + 1
            if remaining < 0:
                break
        if not per_letter:
            path = QPainterPath()
            for chh, gx, gy, _i in glyphs:
                path.addText(QPointF(gx, gy), lay["font"], chh)
            if not path.isEmpty():
                extrusion(path)
                front(path)
        else:
            n = max(1, len(st.text))
            items = []
            for chh, gx, gy, gi in glyphs:
                if chh.isspace():
                    continue
                a = 1.0 if alphas is None else alphas[gi]
                ldx, ldy, lrot, ls, la = comic.letter_motion(st, gi, n, local, duration, em)
                a *= la
                if a <= 0.001 or ls <= 0.001:
                    continue
                cp = QPainterPath()
                cp.addText(QPointF(gx, gy), lay["font"], chh)
                cx = gx + fm.horizontalAdvance(chh) / 2
                cy = gy - lay["ascent"] * 0.35
                from PySide6.QtGui import QTransform
                tr = QTransform()
                tr.translate(cx + ldx, cy + ldy)
                tr.rotate(lrot)
                tr.scale(ls, ls)
                tr.translate(-cx, -cy)
                items.append((tr.map(cp), a))
            for cp, a in items:                            # all the 3D sides first, then the faces
                painter.setOpacity(base_opacity * a)
                extrusion(cp)
            for cp, a in items:
                painter.setOpacity(base_opacity * a)
                front(cp)
            painter.setOpacity(base_opacity)
        if cursor and cursor_at is not None:
            w = max(1.0, fm.height() * 0.07)
            r = QRectF(cursor_at[0] + w * 0.6, cursor_at[1] - fm.ascent(), w, fm.ascent() + fm.descent())
            if pen is not None:
                cp = QPainterPath()
                cp.addRect(r)
                painter.strokePath(cp, pen)
            painter.fillRect(r, QColor(st.color))
        painter.restore()

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
    tw = max(widths or [0])
    th = total_h if st.text else 0.0
    lay = {"font": font, "fm": fm, "lines": lines, "widths": widths, "total_h": total_h,
           "spacing": fm.lineSpacing(), "ascent": fm.ascent(),
           "rect": QRectF(-tw / 2, -th / 2, tw, th), "text_offset": QPointF(0.0, 0.0),
           "image": None, "image_rect": None}
    effect = comic.valid_effect(getattr(st, "comic_effect", ""))
    img = None if effect else bubble_image(getattr(st, "image_path", ""))
    lay["effect"] = effect
    if img is not None or effect:
        # the picture (or the comic effect, which takes its place) sits beside
        # the words; the whole block stays centered
        if effect:
            ih = max(2.0, getattr(st, "effect_size", 0.22) * ch)
            iw = ih * comic.effect_aspect(effect, getattr(st, "effect_side", ""))
        else:
            ih = max(2.0, getattr(st, "image_size", 0.18) * ch)
            iw = ih * img.width() / max(img.height(), 1)
        gap = fm.height() * 0.35 if st.text else 0.0
        place = getattr(st, "image_place", "above")
        if place in ("left", "right"):
            W, H = iw + gap + tw, max(ih, th)
            if place == "left":
                ir = QRectF(-W / 2, -ih / 2, iw, ih)
                off = QPointF(-W / 2 + iw + gap + tw / 2, 0.0)
            else:
                ir = QRectF(W / 2 - iw, -ih / 2, iw, ih)
                off = QPointF(-W / 2 + tw / 2, 0.0)
        else:
            W, H = max(iw, tw), ih + gap + th
            if place == "below":
                ir = QRectF(-iw / 2, H / 2 - ih, iw, ih)
                off = QPointF(0.0, -H / 2 + th / 2)
            else:
                ir = QRectF(-iw / 2, -H / 2, iw, ih)
                off = QPointF(0.0, -H / 2 + ih + gap + th / 2)
        lay.update(rect=QRectF(-W / 2, -H / 2, W, H), text_offset=off, image=img, image_rect=ir)
    return lay


_image_cache: dict = {}
_image_lock = threading.Lock()      # export renders on several threads
GIF_MAX_SIDE = 640                  # animated pictures are kept at most this big (memory)


def _load_picture(path: str):
    """[(QImage, seconds shown)] for a picture file: one entry for a still,
    every frame for an animated GIF (downscaled to GIF_MAX_SIDE), or None."""
    from PySide6.QtGui import QImageReader
    r = QImageReader(path)
    if not r.canRead():
        return None
    if r.supportsAnimation() and r.imageCount() != 1:
        frames = []
        while True:
            img = r.read()
            if img.isNull():
                break
            delay = r.nextImageDelay()
            if img.width() > GIF_MAX_SIDE or img.height() > GIF_MAX_SIDE:
                img = img.scaled(GIF_MAX_SIDE, GIF_MAX_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            # browsers treat tiny/zero delays as 0.1 s; so do we
            frames.append((img.convertToFormat(QImage.Format_ARGB32_Premultiplied),
                           (delay if delay >= 20 else 100) / 1000.0))
            if len(frames) >= 1000:
                break
        if frames:
            return frames
        r = QImageReader(path)
    img = r.read()
    return None if img.isNull() else [(img, 0.0)]


def _picture_frames(path: str):
    if not path:
        return None
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return None
    with _image_lock:
        frames = _image_cache.get(key)
        if frames is None:
            frames = _load_picture(path)
            if frames is None:
                return None
            if len(_image_cache) > 32:
                _image_cache.clear()
            _image_cache[key] = frames
        return frames


def bubble_image(path: str) -> "QImage | None":
    """The picture for a text element (its first frame for a GIF) -- cached,
    reloaded if the file changes."""
    frames = _picture_frames(path)
    return frames[0][0] if frames else None


def image_is_animated(path: str) -> bool:
    frames = _picture_frames(path)
    return bool(frames) and len(frames) > 1


def bubble_image_at(path: str, t: float) -> "QImage | None":
    """The picture as shown `t` seconds in: an animated GIF plays with its
    own frame timing and loops for as long as needed."""
    frames = _picture_frames(path)
    if not frames:
        return None
    if len(frames) == 1:
        return frames[0][0]
    total = sum(d for _i, d in frames)
    if total <= 0:
        return frames[0][0]
    x = t % total
    for img, d in frames:
        if x < d:
            return img
        x -= d
    return frames[-1][0]


TEXT_IN_AT = 0.2      # speech: text transitions start this far into the grow-in (the
                      # body only scales, so the text can ride in with it early)
THOUGHT_TEXT_IN_AT = 0.55   # thought: text starts while the cloud is still puffing up
                            # (the cloud itself waits for its trail -- see thought_cloud_start)
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


BUBBLE_VARIANTS = {
    "speech": [("Neutral", ""), ("Surprise / anger", "spiky"), ("Whisper", "whisper"),
               ("Uncertain", "wiggly"), ("Intercom", "intercom")],
    "thought": [("Neutral", ""), ("Worried", "wobbly"), ("Dreamy", "dreamy"),
                ("Angry", "jagged"), ("Electronic", "electronic")],
}


def valid_variant(kind: str, variant: str) -> str:
    """`variant` if the bubble kind has it, else "" (neutral)."""
    return variant if any(v == variant for _l, v in BUBBLE_VARIANTS.get(kind, [])) else ""


def bubble_dash(variant: str, t: float, animated: bool):
    """(dash pattern in pen widths, dash offset, cap style) for the outline,
    or None for a solid line. Whisper = dashes, Dreamy = dots; animated,
    they march around the bubble."""
    if variant == "whisper":
        return [3.0, 2.2], (-t * 7.0 if animated else 0.0), Qt.FlatCap
    if variant == "dreamy":
        return [0.01, 2.2], (-t * 3.0 if animated else 0.0), Qt.RoundCap
    return None


def _hash01(*vals) -> float:
    """Deterministic pseudo-random 0..1 (same frame -> same result, so the
    preview and the export agree)."""
    x = 0
    for v in vals:
        x = (x * 1000003 + int(v) * 2654435761 + 97) & 0xFFFFFFFF
    x ^= x >> 13
    x = (x * 1274126177) & 0xFFFFFFFF
    x ^= x >> 16
    return (x & 0xFFFF) / 65535.0


def _polygon(points) -> QPainterPath:
    path = QPainterPath()
    if points:
        path.moveTo(points[0])
        for q in points[1:]:
            path.lineTo(q)
        path.closeSubpath()
    return path


def _ellipse_pt(c: QPointF, rx: float, ry: float, a: float, f: float = 1.0) -> QPointF:
    return QPointF(c.x() + rx * f * math.cos(a), c.y() + ry * f * math.sin(a))


FLICKER_HZ = 20.0      # harsh flicker: new random spike lengths this many times a second (at speed 1)


def _jitter(t: float, rate: float, *key) -> float:
    """Random 0..1 that picks a new value `rate` times a second and snaps to
    it quickly (smoothstep between picks), so the motion is harsh and
    jittery yet changes on every rendered frame instead of stepping."""
    x = t * rate
    f = math.floor(x)
    u = x - f
    u = u * u * (3 - 2 * u)
    a, b = _hash01(int(f), *key), _hash01(int(f) + 1, *key)
    return a + (b - a) * u


def _wiggle(theta: float, ph: float) -> float:
    """Uneasy, uneven wobble of a radius (a few clashing waves)."""
    return (0.034 * math.sin(10 * theta + ph) + 0.02 * math.sin(17 * theta - 1.7 * ph + 1.3)
            + 0.012 * math.sin(27 * theta + 2.3 * ph + 0.4))


def _speech_body(variant: str, cc: QPointF, rx: float, ry: float, t: float, animated: bool) -> QPainterPath:
    if variant == "spiky":
        n = 16
        pts = []
        for k in range(2 * n):
            a = math.pi * k / n
            if k % 2 == 0:
                # each spike jumps to a new length every flicker frame --
                # in and out from the center, not a smooth wave
                jump = 0.24 * (_jitter(t, FLICKER_HZ, k) - 0.4) if animated else 0.0
                f = 1.2 + 0.05 * math.sin(k * 2.7) + jump
            else:
                f = 0.9
            pts.append(_ellipse_pt(cc, rx, ry, a, f))
        return _polygon(pts)
    if variant == "wiggly":
        ph = t * 7.7 if animated else 0.0
        n = 72                 # fewer points: slightly angular, less smooth
        pts = [_ellipse_pt(cc, rx, ry, 2 * math.pi * k / n, 1.0 + _wiggle(2 * math.pi * k / n, ph))
               for k in range(n)]
        return _polygon(pts)
    path = QPainterPath()
    if variant == "intercom":
        w, h = rx * 1.84, ry * 1.6
        rr = min(w, h) * 0.04       # squarer than the usual rounding
        path.addRoundedRect(QRectF(cc.x() - w / 2, cc.y() - h / 2, w, h), rr, rr)
        return path
    path.addEllipse(cc, rx, ry)
    return path


def _quad_pts(p0: QPointF, c: QPointF, p1: QPointF, n: int):
    out = []
    for i in range(n + 1):
        s = i / n
        out.append(QPointF((1 - s) ** 2 * p0.x() + 2 * (1 - s) * s * c.x() + s * s * p1.x(),
                           (1 - s) ** 2 * p0.y() + 2 * (1 - s) * s * c.y() + s * s * p1.y()))
    return out


def _speech_tail(variant: str, cc: QPointF, rx: float, ry: float, tail: QPointF, t: float,
                 animated: bool) -> "QPainterPath | None":
    dx, dy = tail.x() - cc.x(), tail.y() - cc.y()
    if (dx / max(rx, 1e-6)) ** 2 + (dy / max(ry, 1e-6)) ** 2 <= 1.0:
        return None
    ang = math.atan2(dy / max(ry, 1e-6), dx / max(rx, 1e-6))
    if variant == "intercom":
        # a jagged, crackling "connection" line from the box to the tip
        from PySide6.QtGui import QPainterPathStroker
        start = _ellipse_pt(cc, rx, ry, ang, 0.8)
        L = math.hypot(tail.x() - start.x(), tail.y() - start.y())
        if L < 1e-3:
            return None
        ux, uy = (tail.x() - start.x()) / L, (tail.y() - start.y()) / L
        nx, ny = -uy, ux
        n = max(5, int(L / max(ry * 0.26, 1.0)))
        amp = ry * 0.32
        rate = 16.0
        line = QPainterPath(start)
        for i in range(1, n):
            f = (i + (0.35 * (_jitter(t, rate, i, 7) - 0.5) if animated else 0.0)) / n
            # amplitude can dip below zero now and then: the odd kink the other way
            j = (-0.25 + 1.85 * _jitter(t, rate, i)) if animated else (0.7 + 0.6 * _hash01(0, i, 3))
            side = 1 if i % 2 else -1
            line.lineTo(QPointF(start.x() + ux * L * f + nx * amp * side * j,
                                start.y() + uy * L * f + ny * amp * side * j))
        line.lineTo(tail)
        st = QPainterPathStroker()
        st.setWidth(max(ry * 0.14, 2.0))
        st.setJoinStyle(Qt.MiterJoin)
        st.setMiterLimit(4.0)
        st.setCapStyle(Qt.FlatCap)
        return st.createStroke(line).simplified()
    if variant == "spiky":
        b1 = _ellipse_pt(cc, rx, ry, ang - 0.22, 0.85)
        b2 = _ellipse_pt(cc, rx, ry, ang + 0.22, 0.85)
        return _polygon([b1, tail, b2])
    spread = 0.32
    b1, b2 = _ellipse_pt(cc, rx, ry, ang - spread), _ellipse_pt(cc, rx, ry, ang + spread)
    mid = _ellipse_pt(cc, rx, ry, ang)
    s1 = QPointF(cc.x() + (b1.x() - cc.x()) * 0.85, cc.y() + (b1.y() - cc.y()) * 0.85)
    s2 = QPointF(cc.x() + (b2.x() - cc.x()) * 0.85, cc.y() + (b2.y() - cc.y()) * 0.85)
    c1 = QPointF((b1.x() + tail.x()) / 2 + (mid.x() - cc.x()) * 0.05,
                 (b1.y() + tail.y()) / 2 + (mid.y() - cc.y()) * 0.05)
    c2 = QPointF((b2.x() + tail.x()) / 2, (b2.y() + tail.y()) / 2)
    if variant == "wiggly":
        # its own wobbly tail: both edges wave (and travel, when animated)
        ph = t * 7.7 if animated else 0.0
        amp = ry * 0.07
        side1 = _quad_pts(s1, c1, tail, 14)
        side2 = _quad_pts(tail, c2, s2, 14)
        pts = []
        for seq, sign in ((side1, 1), (side2, -1)):
            for i in range(len(seq)):
                q = seq[i]
                a_ = seq[min(i + 1, len(seq) - 1)]
                b_ = seq[max(i - 1, 0)]
                tx, ty = a_.x() - b_.x(), a_.y() - b_.y()
                ln = math.hypot(tx, ty) or 1.0
                s_ = i / (len(seq) - 1)
                # pinned at the base; calm near the tip so it keeps its point
                to_tip = (1 - s_) if sign == 1 else s_
                fade = math.sin(math.pi * s_) * min(1.0, to_tip / 0.5) ** 1.5
                off = amp * fade * (math.sin(s_ * 9.0 + ph * sign) + 0.5 * math.sin(s_ * 17.0 - ph * 1.6))
                pts.append(QPointF(q.x() - ty / ln * off, q.y() + tx / ln * off))
        return _polygon(pts)
    tp = QPainterPath()
    tp.moveTo(s1)
    tp.quadTo(c1, tail)
    tp.quadTo(c2, s2)
    tp.closeSubpath()
    return tp


TRAIL_STEP, TRAIL_LEN = 0.25, 0.14   # trail circle k starts at TRAIL_STEP*k/n of the grow, takes TRAIL_LEN
THOUGHT_CLOUD_END = 0.9


def thought_cloud_start(n_trail: int) -> float:
    """When the cloud starts growing: once the trail's circles have reached
    it (the one next to the cloud is most of the way grown)."""
    if n_trail <= 0:
        return 0.1
    return TRAIL_STEP * (n_trail - 1) / n_trail + TRAIL_LEN * 0.7


def bubble_shape(kind: str, body: QRectF, tip: "QPointF | None", g: float = 1.0, shrinking: bool = False,
                 variant: str = "", t: float = 0.0, animated: bool = False):
    """Bubble outline at grow progress g (1 = fully formed), plus where the
    text goes: (path, text_center, text_scale, text_alpha). `variant` picks
    the style (BUBBLE_VARIANTS); `t` (element-local seconds) drives the
    animated styles when `animated`.

    Speech grows out of the tail tip: the body flies from the tip to its
    place while scaling up (with a little overshoot) and the tail stretches
    between them. Thought sprouts its trail circles from the tip outward;
    once they reach the cloud it puffs up bump by bump outward from the
    center -- starting on the trail's side but with every bump growing at
    once (the far side is only a little behind, never waiting).
    shrinking=True runs the motion back into the tip with its own easing:
    running the grow-in's overshoot easing backwards kept the bubble near
    full size until the last couple of frames and then it vanished, so the
    shrink uses a smooth ease that ends right at the element's end."""
    variant = valid_variant(kind, variant)
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
        # phase 1: circles (squares, for Electronic) sprout from the tip
        # toward the cloud
        for idx, (pc, r) in enumerate(trail):
            order = n_tr - 1 - idx                    # tip-most first
            k = ease_back(_window(g, TRAIL_STEP * order / max(n_tr, 1), TRAIL_LEN)) if g < 1 else 1.0
            if k <= 0.01:
                continue
            dot = QPainterPath()
            if variant == "electronic":
                pulse = 1.0 + (0.22 * max(0.0, math.sin(2 * math.pi * 1.2 * t - order * 0.9)) if animated else 0.0)
                side = r * 1.7 * k * pulse
                dot.addRect(QRectF(pc.x() - side / 2, pc.y() - side / 2, side, side))
            else:
                if variant == "wobbly":
                    r = r * (1.0 + 0.1 * math.sin((9.0 * t if animated else 0.0) + idx * 2.1))
                elif variant == "" and animated:
                    r = r * (1.0 + 0.05 * math.sin(3.0 * t + idx * 2.1))
                dot.addEllipse(pc, r * k, r * k)
            path = path.united(dot)
        # phase 2: the cloud, once the trail has reached it
        cs = thought_cloud_start(n_tr)
        cg = 1.0 if g >= 1 else _window(g, cs, THOUGHT_CLOUD_END - cs)
        if cg > 0.001:
            c = c_full
            if variant == "electronic":
                u = cg
                sc = (ease_back(u) if u < 1 else 1.0)
                if animated:
                    # the pulse running up the trail of squares arrives here
                    sc *= 1.0 + 0.07 * max(0.0, math.sin(2 * math.pi * 1.2 * t - n_tr * 0.9))
                if sc > 0.01:
                    w, h = rx * 1.84 * sc, ry * 1.6 * sc
                    rr = min(w, h) * 0.027      # squarer than the usual rounding
                    box = QPainterPath()
                    box.addRoundedRect(QRectF(c.x() - w / 2, c.y() - h / 2, w, h), rr, rr)
                    path = path.united(box)
            else:
                nb = 11
                circ = 2 * math.pi * math.sqrt((rx * rx + ry * ry) / 2)
                br = circ / nb * 0.62
                slot = 2 * math.pi / nb
                ph = (7.0 * t if animated else 0.0)
                for i in range(nb):
                    a = 2 * math.pi * i / nb
                    d = abs(math.atan2(math.sin(a - ang), math.cos(a - ang))) / math.pi if tail is not None else 0.0
                    # the trail's side leads a little; every bump is growing
                    # by the time a third of the cloud's time has passed
                    u = _window(cg, 0.3 * d, 0.7) if cg < 1 else 1.0
                    kb = ease_back(u) if u < 1 else 1.0
                    reach = ease_cubic(u) if u < 1 else 1.0
                    if kb <= 0.01:
                        continue
                    wob = 1.0
                    rad = 1.0
                    if variant == "wobbly":
                        wob = 1.0 + 0.11 * math.sin(ph + i * 2.3)
                        rad = 1.0 + 0.035 * math.sin(ph * 1.3 + i * 1.7)
                    elif variant == "" and animated:
                        # neutral: a gentle, slow wiggle
                        slow = 3.0 * t
                        wob = 1.0 + 0.04 * math.sin(slow + i * 2.3)
                        rad = 1.0 + 0.015 * math.sin(slow * 1.3 + i * 1.7)
                    ex, ey = rx * 0.86 * rad * reach, ry * 0.8 * rad * reach
                    arc = [QPointF(c.x() + ex * math.cos(a - slot * 0.56 + slot * 1.12 * j / 6),
                                   c.y() + ey * math.sin(a - slot * 0.56 + slot * 1.12 * j / 6)) for j in range(7)]
                    path = path.united(_polygon([c] + arc))
                    bc = QPointF(c.x() + ex * math.cos(a), c.y() + ey * math.sin(a))
                    if variant == "jagged":
                        # harsh flicker: each spike jumps to a new length
                        flick = (0.55 + 0.9 * _jitter(t, FLICKER_HZ, i)) if animated else 1.0
                        spike = br * 1.15 * kb * (1.0 + 0.2 * math.sin(i * 1.3)) * flick
                        tipp = QPointF(bc.x() + spike * math.cos(a) * (rx / max(rx, ry)),
                                       bc.y() + spike * math.sin(a) * (ry / max(rx, ry)))
                        path = path.united(_polygon([arc[0], tipp, arc[-1]]))
                    else:
                        bump = QPainterPath()
                        bump.addEllipse(bc, br * kb * wob, br * 0.9 * kb * wob)
                        path = path.united(bump)
        return path, QPointF(0.0, 0.0), 1.0, (1.0 if g >= 1 else _window(g, THOUGHT_TEXT_IN_AT, 0.3))
    # speech
    e_pos = ease_cubic(g)
    e_size = max(0.0, ease_back(g)) if g < 1 else 1.0
    origin = tail if tail is not None else c_full
    cc = QPointF(origin.x() + (c_full.x() - origin.x()) * e_pos, origin.y() + (c_full.y() - origin.y()) * e_pos)
    if e_size <= 0.01:
        return path, cc, 0.0, 0.0
    rxs, rys = rx * e_size, ry * e_size
    path = _speech_body(variant, cc, rxs, rys, t, animated)
    if tail is not None:
        tp = _speech_tail(variant, cc, rxs, rys, tail, t, animated)
        if tp is not None:
            path = path.united(tp)
    # text rides with the body; the painter maps local (0,0) = c_full
    text_center = QPointF(cc.x() - c_full.x() * e_size, cc.y() - c_full.y() * e_size)
    return path, text_center, e_size, (1.0 if g >= 1 else _window(g, TEXT_IN_AT, 0.3))


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
    duration = project.video_duration
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

    def cancelled():
        return cancel is not None and cancel.is_set()

    audio_done = [0.0]

    def encode_audio(out, aus):
        renderer = Renderer(project, overlays=False)
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
                audio_done[0] = done / max(total, 1)
            for pkt in aus.encode(None):
                out.mux(pkt)
        finally:
            renderer.close()

    def render_range(out, vs, f0, f1, threaded: bool):
        """Encode frames [f0, f1) into vs, pts starting at 0."""
        renderer = Renderer(project, overlays=False)
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
        # Every piece of work runs on its own thread from the start -- the
        # video piece(s) AND the audio -- and this thread only reports
        # progress, so the bar moves from the first frame (encoding the
        # audio up front used to leave it at nothing for seconds).
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
                                               threads=None if jobs == 1 else max(1, cpus // jobs))
                        render_range(out, vs, f0, f1, threaded=jobs == 1)
                except BaseException as e:
                    errors.append(e)
                    if cancel is not None:
                        cancel.set()
            threads.append(threading.Thread(target=job, daemon=True, name=f"export-part{k}"))
        audio_path = f"{out_path}.audio.m4a"
        if has_audio:
            parts_files.append(audio_path)

            def audio_job():
                try:
                    with av.open(audio_path, "w", format="mp4") as aout:
                        aus = aout.add_stream("aac", rate=RATE)
                        aus.layout = "stereo"
                        aus.bit_rate = 192000
                        encode_audio(aout, aus)
                except BaseException as e:
                    errors.append(e)
                    if cancel is not None:
                        cancel.set()
            threads.append(threading.Thread(target=audio_job, daemon=True, name="export-audio"))
        for th in threads:
            th.start()
        # audio is a small share of the work next to the pictures
        a_w = 0.08 if has_audio else 0.0
        while any(th.is_alive() for th in threads):
            for th in threads:
                th.join(timeout=0.05)
            if progress is not None:
                v = min(1.0, done_frames[0] / n_frames)
                progress(min(0.99, (v * (1 - a_w) + audio_done[0] * a_w)))
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
    starting on a keyframe) and the audio into one MP4, copying packets.
    Packets are written interleaved by time (so the muxer never has to
    buffer a whole stream)."""
    import heapq
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

        def video_packets():
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
                        yield float((pkt.dts if pkt.dts is not None else pkt.pts) * st.time_base), pkt, ovs

        def audio_packets():
            st = ain.streams.audio[0]
            for pkt in ain.demux(st):
                if pkt.dts is None:
                    continue
                yield float(pkt.dts * st.time_base), pkt, oas

        sources = [video_packets()] + ([audio_packets()] if ain is not None else [])
        for _t, pkt, stream in heapq.merge(*sources, key=lambda x: x[0]):
            pkt.stream = stream
            out.mux(pkt)
        if ain is not None:
            ain.close()
