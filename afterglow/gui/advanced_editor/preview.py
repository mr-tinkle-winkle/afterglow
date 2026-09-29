"""
The Advanced Editor's preview: plays the timeline through the SAME
renderer that Save uses (afterglow.nle.render), so the preview is exactly
what gets saved, with audio through QAudioSink.

Transform/crop handles: when one segment with a picture is selected and
under the playhead, its box is drawn over the preview. Drag inside to
move, drag a corner to scale, drag the round handle above it to rotate.
With Crop on, the edge handles crop instead. If a property has keyframes,
dragging sets a keyframe at the playhead (see
EditorController.animated_fn). Clicking a picture selects its segment.
"""
from __future__ import annotations

import copy
import math

from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget

from ... import config as config_module
from ...nle import render as nle_render
from ...nle.model import EPS, KIND_IMAGE, KIND_TEXT, Project, Segment
from ..custom_button import CustomButton
from ..rounded_rect import rounded_rect_path
from ..theme import Theme
from . import icons
from .controller import EditorController, probe_cached
from .timeline import format_time

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
except Exception:          # QtMultimedia missing -> silent preview
    QAudioSink = None

AUDIO_CHUNK = 1024
AUDIO_BUFFER_SEC = 0.12
AUDIO_START_TIMEOUT_NS = 3_000_000_000
HANDLE_R = 6


class AudioOut:
    """Push-mode stereo float output at the renderer's rate.

    The output stream is kept open between plays (fed silence while
    paused, closed after IDLE_CLOSE_SEC idle): opening a stream can take a
    noticeable moment on some systems, and paying that on every press of
    Space made playback start late. Timing: every byte written is
    counted, so when playback starts the position of its first real
    sample in the stream is known exactly; the device's processed count
    passing that point is "the first sample is audible now"."""

    IDLE_CLOSE_SEC = 20.0

    def __init__(self):
        self.sink = None
        self.io = None
        self.pos = 0.0            # timeline time of the next sample to write
        self.playing = False
        self._written_us = 0
        self._content_us = 0
        self._last_pu = -1
        self._last_pu_ns = 0
        if QAudioSink is None:
            return
        dev = QMediaDevices.defaultAudioOutput()
        if dev.isNull():
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(nle_render.RATE)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Float)
        if not dev.isFormatSupported(fmt):
            return
        self.sink = QAudioSink(dev, fmt)
        self.sink.setBufferSize(int(nle_render.RATE * AUDIO_BUFFER_SEC) * 8)

    @property
    def available(self) -> bool:
        return self.sink is not None

    @property
    def is_open(self) -> bool:
        return self.io is not None

    def open(self) -> None:
        if self.sink is None or self.io is not None:
            return
        self.io = self.sink.start()
        self._written_us = 0
        self._last_pu = -1

    def close(self) -> None:
        if self.sink is not None and self.io is not None:
            self.sink.stop()
        self.io = None
        self.playing = False

    def start(self, t: float) -> None:
        """Begin writing timeline audio from t (opening the stream if needed)."""
        self.open()
        if self.io is None:
            return
        self.pos = t
        self.playing = True
        self._content_us = self._written_us
        self._last_pu = -1

    def pause(self) -> None:
        self.playing = False

    def _write(self, data) -> None:
        self.io.write(data.tobytes())
        self._written_us += int(round(data.shape[0] * 1e6 / nle_render.RATE))

    def feed(self, renderer, end_t: float) -> None:
        if self.io is None:
            return
        # Write whatever room the device reports (it can be smaller than one
        # chunk -- waiting for a full chunk could starve it entirely).
        free = self.sink.bytesFree() // 8
        import numpy as np
        while free >= 64:
            n = min(free, AUDIO_CHUNK)
            if self.playing and self.pos < end_t:
                data = renderer.audio(self.pos, n)
                self.pos += n / nle_render.RATE
            else:
                data = np.zeros((n, 2), np.float32)
            self._write(data)
            free -= n

    def played_seconds(self, now_ns: int) -> "float | None":
        """Seconds of the current playback actually heard so far
        (interpolated between the device's coarse updates), or None until
        its first sample reaches the speaker."""
        if self.io is None or not self.playing:
            return None
        pu = self.sink.processedUSecs()
        if pu <= self._content_us:
            return None
        if pu != self._last_pu:
            self._last_pu = pu
            self._last_pu_ns = now_ns
        return (pu - self._content_us) / 1e6 + min(0.04, (now_ns - self._last_pu_ns) / 1e9)


class PreviewCanvas(QWidget):
    """Letterboxed preview image + the transform overlay."""

    def __init__(self, panel: "PreviewPanel"):
        super().__init__(panel)
        self.panel = panel
        self.ctl = panel.ctl
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(200, 120)
        self.setFocusPolicy(Qt.ClickFocus)
        self.image = None
        self.crop_mode = False
        self._drag = None
        self._hover_handle = None

    # ---- geometry --------------------------------------------------------
    def frame_rect(self) -> QRectF:
        p = self.ctl.project
        if p is None:
            return QRectF(self.rect())
        ar = p.width / max(p.height, 1)
        w, h = self.width(), self.height()
        if w / max(h, 1) > ar:
            fw, fh = h * ar, h
        else:
            fw, fh = w, w / ar
        return QRectF((w - fw) / 2, (h - fh) / 2, fw, fh)

    def canvas_to_widget(self) -> QTransform:
        p = self.ctl.project
        fr = self.frame_rect()
        k = fr.width() / max(p.width, 1)
        tr = QTransform()
        tr.translate(fr.left(), fr.top())
        tr.scale(k, k)
        return tr

    def seg_geometry(self, seg: Segment):
        """(transform canvas->widget for the segment's local box, box QRectF)
        mirroring Renderer._draw_segment / _draw_picture / _draw_text."""
        p = self.ctl.project
        local = self.ctl.playhead - seg.start
        kf = seg.keyframes
        trf = seg.transform
        x = nle_render.eval_keyframes(kf.get("x"), local, trf.x)
        y = nle_render.eval_keyframes(kf.get("y"), local, trf.y)
        scale = nle_render.eval_keyframes(kf.get("scale"), local, trf.scale) * nle_render.zoom_factor(seg, local)
        rot = nle_render.eval_keyframes(kf.get("rotation"), local, trf.rotation)
        cw, ch = p.width, p.height
        part = next((pt for pt in seg.parts if pt.has_video and pt.visible and pt.active_at(local)), None)
        if part is None:
            return None
        if part.kind == KIND_TEXT:
            st = part.text
            if st is None:
                return None
            lay = nle_render.text_layout(st, ch)
            r = nle_render.bubble_body(lay) if st.bubble else lay["rect"].adjusted(-4, -2, 4, 2)
            bw, bh = r.width(), r.height()
        else:
            if part.kind == KIND_IMAGE:
                from PySide6.QtGui import QImageReader
                sz = QImageReader(part.source).size()
                sw, sh = max(sz.width(), 1), max(sz.height(), 1)
            else:
                info = probe_cached(part.source)
                sw, sh = max(info.get("width") or 1, 1), max(info.get("height") or 1, 1)
            crop_w = max(1e-6, 1 - trf.crop_left - trf.crop_right) * sw
            crop_h = max(1e-6, 1 - trf.crop_top - trf.crop_bottom) * sh
            fit = min(cw / crop_w, ch / crop_h)
            bw, bh = crop_w * fit, crop_h * fit
        t = QTransform()
        t.translate(cw / 2 + x * cw, ch / 2 + y * ch)
        t.rotate(rot)
        t.scale(scale, scale)
        return t * self.canvas_to_widget(), QRectF(-bw / 2, -bh / 2, bw, bh), part.kind

    def target_segment(self) -> "Segment | None":
        sel = self.ctl.selected_segments()
        if len(sel) != 1:
            return None
        s = sel[0]
        if not s.has_video or not s.covers(self.ctl.playhead) or not s.visible:
            return None
        return s

    def tail_point(self, seg: Segment) -> "QPointF | None":
        """Widget position of a speech/thought bubble's tail tip, or None."""
        part = next((pt for pt in seg.parts if pt.kind == KIND_TEXT and pt.text and pt.text.bubble), None)
        if part is None:
            return None
        p = self.ctl.project
        local = self.ctl.playhead - seg.start
        kf = seg.keyframes
        tx = nle_render.eval_keyframes(kf.get("tail_x"), local, part.text.tail_x)
        ty = nle_render.eval_keyframes(kf.get("tail_y"), local, part.text.tail_y)
        return self.canvas_to_widget().map(QPointF(p.width / 2 + tx * p.width, p.height / 2 + ty * p.height))

    def _handles(self, geo, seg: "Segment | None" = None):
        tr, box, kind = geo
        pts = {
            "tl": box.topLeft(), "tr": box.topRight(), "br": box.bottomRight(), "bl": box.bottomLeft(),
        }
        if self.crop_mode and kind != KIND_TEXT:
            pts = {
                "cl": QPointF(box.left(), box.center().y()), "cr": QPointF(box.right(), box.center().y()),
                "ct": QPointF(box.center().x(), box.top()), "cb": QPointF(box.center().x(), box.bottom()),
            }
        out = {k: tr.map(v) for k, v in pts.items()}
        if not self.crop_mode:
            top_mid = tr.map(QPointF(box.center().x(), box.top()))
            center = tr.map(box.center())
            d = top_mid - center
            n = math.hypot(d.x(), d.y()) or 1.0
            out["rot"] = top_mid + QPointF(d.x() / n * 26, d.y() / n * 26)
            tip = self.tail_point(seg) if seg is not None else None
            if tip is not None:
                out["tail"] = tip
        return out

    # ---- paint -------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor(8, 8, 10))
        if self.ctl.project is None:
            p.setPen(QColor(160, 160, 160))
            p.drawText(self.rect(), Qt.AlignCenter, "No project open")
            p.end()
            return
        fr = self.frame_rect()
        if self.image is not None:
            p.drawImage(fr, self.image)
        else:
            p.fillRect(fr, QColor(0, 0, 0))
        seg = self.target_segment()
        if seg is not None:
            geo = self.seg_geometry(seg)
            if geo is not None:
                tr, box, kind = geo
                poly = tr.map(QPolygonF(box))
                p.setPen(QPen(QColor(255, 255, 255, 220), 1.5, Qt.DashLine if self.crop_mode else Qt.SolidLine))
                p.setBrush(Qt.NoBrush)
                p.drawPolygon(poly)
                hs = self._handles(geo, seg)
                if "tail" in hs:
                    p.setPen(QPen(QColor(255, 180, 60, 200), 1.2, Qt.DashLine))
                    p.drawLine(tr.map(box.center()), hs["tail"])
                if "rot" in hs:
                    top_mid = tr.map(QPointF(box.center().x(), box.top()))
                    p.drawLine(top_mid, hs["rot"])
                for name, pt in hs.items():
                    active = name == self._hover_handle
                    p.setPen(QPen(QColor(0, 0, 0, 200), 1))
                    p.setBrush(QColor("#ffd43b") if active else QColor("white"))
                    if name == "tail":
                        p.setBrush(QColor("#ffd43b") if active else QColor("#ff9f1c"))
                        p.drawPolygon(QPolygonF([QPointF(pt.x(), pt.y() - HANDLE_R - 2), QPointF(pt.x() + HANDLE_R + 2, pt.y()),
                                                 QPointF(pt.x(), pt.y() + HANDLE_R + 2), QPointF(pt.x() - HANDLE_R - 2, pt.y())]))
                    elif name == "rot":
                        p.drawEllipse(pt, HANDLE_R, HANDLE_R)
                    else:
                        p.drawRect(QRectF(pt.x() - HANDLE_R, pt.y() - HANDLE_R, 2 * HANDLE_R, 2 * HANDLE_R))
        p.end()

    # ---- mouse -------------------------------------------------------------
    def _hit_handle(self, pos: QPointF):
        seg = self.target_segment()
        if seg is None:
            return None, None
        geo = self.seg_geometry(seg)
        if geo is None:
            return None, None
        for name, pt in self._handles(geo, seg).items():
            if abs(pt.x() - pos.x()) <= HANDLE_R + 3 and abs(pt.y() - pos.y()) <= HANDLE_R + 3:
                return seg, name
        tr, box, _k = geo
        inv, ok = tr.inverted()
        if ok and box.contains(inv.map(pos)):
            return seg, "move"
        return seg, None

    def _segment_at(self, pos: QPointF) -> "Segment | None":
        p = self.ctl.project
        for track in p.tracks:                      # top track first = drawn on top
            for s in track.segments:
                if not (s.visible and s.has_video and s.covers(self.ctl.playhead)):
                    continue
                geo = self.seg_geometry(s)
                if geo is None:
                    continue
                tr, box, _k = geo
                inv, ok = tr.inverted()
                if ok and box.contains(inv.map(pos)):
                    return s
        return None

    def mousePressEvent(self, event) -> None:
        if self.ctl.project is None or event.button() != Qt.LeftButton:
            return
        pos = event.position()
        seg, handle = self._hit_handle(pos)
        if handle is None:
            s = self._segment_at(pos)
            if s is not None:
                self.ctl.set_selection([s.id], anchor=s.id)
                seg, handle = s, "move"
            else:
                self.ctl.set_selection([])
                return
        if seg.locked:
            return
        geo = self.seg_geometry(seg)
        if geo is None:
            return
        tr, box, kind = geo
        center = tr.map(box.center())
        local = self.ctl.playhead - seg.start
        kf = seg.keyframes
        t = seg.transform
        self._drag = {
            "seg_id": seg.id, "handle": handle, "press": pos, "center": center,
            "base": self.ctl.project.to_dict(),
            "x": nle_render.eval_keyframes(kf.get("x"), local, t.x),
            "y": nle_render.eval_keyframes(kf.get("y"), local, t.y),
            "scale": nle_render.eval_keyframes(kf.get("scale"), local, t.scale),
            "rotation": nle_render.eval_keyframes(kf.get("rotation"), local, t.rotation),
            "crop": (t.crop_left, t.crop_top, t.crop_right, t.crop_bottom),
            "tail": self._tail_values(seg, local),
            "inv": tr.inverted()[0], "box": QRectF(box),
        }
        self.ctl.begin("Crop" if handle.startswith("c") and len(handle) == 2
                       else "Move tail" if handle == "tail" else "Transform")

    @staticmethod
    def _tail_values(seg: Segment, local: float):
        part = next((pt for pt in seg.parts if pt.kind == KIND_TEXT and pt.text), None)
        if part is None:
            return (0.0, 0.0)
        kf = seg.keyframes
        return (nle_render.eval_keyframes(kf.get("tail_x"), local, part.text.tail_x),
                nle_render.eval_keyframes(kf.get("tail_y"), local, part.text.tail_y))

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        d = self._drag
        if d is None:
            _seg, h = self._hit_handle(pos)
            if h != self._hover_handle:
                self._hover_handle = h
                self.update()
            self.setCursor({None: Qt.ArrowCursor, "move": Qt.SizeAllCursor, "rot": Qt.CrossCursor,
                            "tail": Qt.PointingHandCursor}.get(
                h, Qt.SizeFDiagCursor if h in ("tl", "br") else Qt.SizeBDiagCursor if h in ("tr", "bl")
                else Qt.SizeHorCursor if h in ("cl", "cr") else Qt.SizeVerCursor))
            return
        p = self.ctl.project
        k = self.frame_rect().width() / max(p.width, 1)
        h = d["handle"]
        sid = d["seg_id"]
        values = {}
        if h == "move":
            dx = (pos.x() - d["press"].x()) / k / p.width
            dy = (pos.y() - d["press"].y()) / k / p.height
            nx, ny = d["x"] + dx, d["y"] + dy
            if not (event.modifiers() & Qt.AltModifier):     # snap to center
                if abs(nx) < 0.012:
                    nx = 0.0
                if abs(ny) < 0.012:
                    ny = 0.0
            values = {"x": nx, "y": ny}
        elif h == "tail":
            dx = (pos.x() - d["press"].x()) / k / p.width
            dy = (pos.y() - d["press"].y()) / k / p.height
            values = {"tail_x": d["tail"][0] + dx, "tail_y": d["tail"][1] + dy}
        elif h in ("tl", "tr", "br", "bl"):
            c = d["center"]
            d0 = math.hypot(d["press"].x() - c.x(), d["press"].y() - c.y()) or 1.0
            d1 = math.hypot(pos.x() - c.x(), pos.y() - c.y())
            values = {"scale": max(0.02, d["scale"] * d1 / d0)}
        elif h == "rot":
            c = d["center"]
            a0 = math.degrees(math.atan2(d["press"].y() - c.y(), d["press"].x() - c.x()))
            a1 = math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))
            r = d["rotation"] + (a1 - a0)
            r = (r + 180) % 360 - 180
            if event.modifiers() & Qt.ShiftModifier:
                r = round(r / 15) * 15
            elif abs(r) < 2:
                r = 0.0
            values = {"rotation": r}
        else:
            # crop: movement in the box's own (unrotated) coordinates
            lp0 = d["inv"].map(d["press"])
            lp1 = d["inv"].map(pos)
            box = d["box"]
            cl, ct, cr, cb = d["crop"]
            full_w = box.width() / max(1e-6, 1 - cl - cr)
            full_h = box.height() / max(1e-6, 1 - ct - cb)
            dxl = (lp1.x() - lp0.x()) / full_w
            dyl = (lp1.y() - lp0.y()) / full_h
            if h == "cl":
                cl = min(max(0.0, cl + dxl), 0.95 - cr)
            elif h == "cr":
                cr = min(max(0.0, cr - dxl), 0.95 - cl)
            elif h == "ct":
                ct = min(max(0.0, ct + dyl), 0.95 - cb)
            elif h == "cb":
                cb = min(max(0.0, cb - dyl), 0.95 - ct)
            values = {"crop_left": cl, "crop_top": ct, "crop_right": cr, "crop_bottom": cb}

        def fn(proj):
            restored = Project.from_dict(copy.deepcopy(d["base"]))
            proj.__dict__.update(restored.__dict__)
            for prop, v in values.items():
                if prop.startswith("crop_"):
                    from ...nle import ops
                    ops.set_transform(proj, [sid], **{prop: v})
                else:
                    self.ctl.animated_fn([sid], prop, v)(proj)
        self.ctl.live(fn)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is not None:
            self._drag = None
            self.ctl.end()

    # ---- in-place text editing (double-click a text element) --------------
    def mouseDoubleClickEvent(self, event) -> None:
        if self.ctl.project is None:
            return
        s = self._segment_at(event.position())
        if s is None or s.locked or not any(pt.kind == KIND_TEXT and pt.text for pt in s.parts):
            return
        self.ctl.set_selection([s.id], anchor=s.id)
        self.start_text_edit(s)

    def start_text_edit(self, seg: Segment) -> None:
        self.finish_text_edit(commit=True)
        geo = self.seg_geometry(seg)
        part = next((pt for pt in seg.parts if pt.kind == KIND_TEXT and pt.text), None)
        if geo is None or part is None:
            return
        tr, box, _k = geo
        area = tr.mapRect(box).toAlignedRect().adjusted(-6, -4, 6, 4)
        ed = _InlineTextEdit(self, part.text, commit=lambda: self.finish_text_edit(True),
                             cancel=lambda: self.finish_text_edit(False))
        k = self.frame_rect().width() / max(self.ctl.project.width, 1)
        ed.set_scale(None, self.ctl.project.height * math.hypot(tr.m11(), tr.m12()))
        ed.setGeometry(area.intersected(self.rect()) if area.width() > 60 else area.adjusted(-40, 0, 40, 0))
        ed.setPlainText(part.text.text)
        ed.selectAll()
        ed.show()
        ed.setFocus()
        self._editing = (seg.id, ed)

    _editing = None

    def finish_text_edit(self, commit: bool) -> None:
        if self._editing is None:
            return
        sid, ed = self._editing
        self._editing = None
        text = ed.toPlainText()
        ed.hide()
        ed.deleteLater()
        if not commit:
            return

        def fn(p):
            _, s = p.find_segment(sid)
            if s is None or s.locked:
                return
            for part in s.parts:
                if part.kind == KIND_TEXT and part.text is not None:
                    part.text.text = text
            s.name = text.split("\n")[0][:40] or "Text"
        self.ctl.perform("Edit text", fn)


class _InlineTextEdit(QPlainTextEdit):
    """Editor shown over a text element after a double-click. Enter
    commits, Shift+Enter adds a line, Esc cancels, clicking away commits."""

    def __init__(self, parent, style, commit, cancel):
        super().__init__(parent)
        self._commit, self._cancel = commit, cancel
        self._style = style
        self._done = False
        self.setFrameShape(QPlainTextEdit.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QPlainTextEdit { background: rgba(0, 0, 0, 150); color: white;"
                           " border: 2px solid #ffd43b; border-radius: 6px; }")
        self.document().setDocumentMargin(2)

    def set_scale(self, _unused, canvas_px_height: float) -> None:
        f = QFont(self._style.font_family)
        f.setPixelSize(max(10, int(self._style.font_size * canvas_px_height)))
        f.setBold(self._style.bold)
        f.setItalic(self._style.italic)
        self.setFont(f)
        opt = self.document().defaultTextOption()
        opt.setAlignment(Qt.AlignHCenter)
        self.document().setDefaultTextOption(opt)

    def event(self, event) -> bool:
        # Keep every key for the editor while typing (the page's shortcuts,
        # e.g. Esc = deselect, would otherwise take Esc/Delete/arrows).
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self._done = True
            self._commit()
            return
        if event.key() == Qt.Key_Escape:
            self._done = True
            self._cancel()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if not self._done:
            self._done = True
            QTimer.singleShot(0, self._commit)


class PreviewPanel(QWidget):
    playing_changed = Signal(bool)

    def __init__(self, controller: EditorController, parent=None):
        super().__init__(parent)
        self.ctl = controller
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self.renderer: "nle_render.Renderer | None" = None
        self.audio = AudioOut()
        self._playing = False
        self._clock = QElapsedTimer()
        self._t0 = 0.0
        self._own_move = False
        self._render_pending = False
        self._clock_mode = "wall"
        self._wall_base_ns = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.canvas = PreviewCanvas(self)
        layout.addWidget(self.canvas, stretch=1)

        row = QHBoxLayout()
        row.setSpacing(6)
        text_c = appearance.card_text_color

        def btn(name, tip, slot, size=34):
            b = CustomButton()
            b.set_circular(size)
            b.set_icon_pixmap(icons.icon(name, text_c))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
            return b
        self.time_label = QLabel("0:00.00 / 0:00.00")
        self.time_label.setStyleSheet(f"QLabel {{ color: {text_c}; font-family: monospace; }}")
        row.addWidget(self.time_label)
        row.addStretch(1)
        btn("to_start", "Go to start (Home)", lambda: self.seek(0.0))
        btn("step_back", "Previous frame (,)", lambda: self.ctl.step_playhead(frames=-1))
        self.play_btn = btn("play", "Play / Pause (Space)", self.toggle_play, 42)
        btn("step_fwd", "Next frame (.)", lambda: self.ctl.step_playhead(frames=1))
        btn("to_end", "Go to end (End)", lambda: self.seek(self.ctl.project.duration if self.ctl.project else 0))
        row.addStretch(1)
        self.crop_btn = CustomButton("Crop")
        self.crop_btn.setCheckable(True)
        self.crop_btn.setToolTip("Crop handles on the selected picture (instead of move/scale/rotate)")
        self.crop_btn.toggled.connect(self._set_crop_mode)
        row.addWidget(self.crop_btn)
        layout.addLayout(row)

        # While paused, keep the open audio stream fed with silence, and
        # close it after a while (see AudioOut).
        self._idle_timer = QTimer(self)
        self._idle_timer.setInterval(40)
        self._idle_timer.timeout.connect(self._idle_feed)
        self._idle_clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(4)
        self._timer.timeout.connect(self._tick)

        controller.changed.connect(self.request_frame)
        controller.selection_changed.connect(self.canvas.update)
        controller.playhead_changed.connect(self._on_playhead)
        controller.state_changed.connect(self._on_state)

    # ---- project ---------------------------------------------------------
    def _on_state(self) -> None:
        p = self.ctl.project
        if self.renderer is not None and (p is None or self.renderer.project is not p):
            self.stop()
            self.renderer.close()
            self.renderer = None
        if p is not None and self.renderer is None:
            self.renderer = nle_render.Renderer(p)
            self.request_frame()
        self._update_time()

    def _set_crop_mode(self, on: bool) -> None:
        self.canvas.crop_mode = on
        self.canvas.update()

    # ---- frames ------------------------------------------------------------
    def request_frame(self) -> None:
        if not self._render_pending:
            self._render_pending = True
            QTimer.singleShot(0, self._render_now)

    def _render_now(self) -> None:
        self._render_pending = False
        if self.renderer is None or self.ctl.project is None:
            self.canvas.image = None
            self.canvas.update()
            return
        fr = self.canvas.frame_rect()
        dpr = self.devicePixelRatioF()
        p = self.ctl.project
        w = int(min(p.width, fr.width() * dpr))
        h = int(min(p.height, fr.height() * dpr))
        if w < 4 or h < 4:
            return
        try:
            self.canvas.image = self.renderer.frame(self.ctl.playhead, w, h)
        except Exception:
            self.canvas.image = None
        self.canvas.update()
        self._update_time()

    def _update_time(self) -> None:
        p = self.ctl.project
        if p is None:
            self.time_label.setText("0:00.00 / 0:00.00")
            return
        self.time_label.setText(f"{format_time(self.ctl.playhead, p.fps, True)} / {format_time(p.duration, p.fps, True)}")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.request_frame()

    # ---- playback ----------------------------------------------------------
    @property
    def playing(self) -> bool:
        return self._playing

    def toggle_play(self) -> None:
        if self._playing:
            self.stop()
        else:
            self.play()

    def play(self) -> None:
        p = self.ctl.project
        if p is None or self.renderer is None or p.duration <= EPS:
            return
        if self.ctl.playhead >= p.duration - 1e-3:
            self._own_move = True
            self.ctl.set_playhead(0.0)
            self._own_move = False
        self._playing = True
        self._t0 = self.ctl.playhead
        self._clock.start()
        self._start_clock()
        self.audio.feed(self.renderer, p.duration)
        self._timer.start()
        self.play_btn.set_icon_pixmap(icons.icon("pause", self._appearance.card_text_color))
        self.playing_changed.emit(True)

    def _start_clock(self) -> None:
        self._idle_timer.stop()
        has_sound = self.audio.available and self._timeline_has_audio()
        if has_sound:
            self.audio.start(self._t0)
            has_sound = self.audio.is_open
        self._clock_mode = "wait" if has_sound else "wall"
        self._wall_base_ns = 0

    def warm_up_audio(self) -> None:
        """Open the audio stream ahead of the first play (called when a
        project is shown), so the first Space press starts promptly."""
        if self.audio.available and not self.audio.is_open and not self._playing:
            self.audio.open()
            self._idle_clock.start()
            self._idle_timer.start()

    def release_audio(self) -> None:
        self._idle_timer.stop()
        if not self._playing:
            self.audio.close()

    def _idle_feed(self) -> None:
        if self._playing:
            return
        if not self.audio.is_open or self._idle_clock.elapsed() > AudioOut.IDLE_CLOSE_SEC * 1000:
            self.audio.close()
            self._idle_timer.stop()
            return
        self.audio.feed(self.renderer, 0.0)

    def _timeline_has_audio(self) -> bool:
        p = self.ctl.project
        return p is not None and any(s.has_audio and not s.muted for s in p.all_segments())

    def stop(self) -> None:
        if not self._playing:
            return
        self._playing = False
        self._timer.stop()
        self.audio.pause()
        if self.audio.is_open:
            self._idle_clock.start()
            self._idle_timer.start()
        self.play_btn.set_icon_pixmap(icons.icon("play", self._appearance.card_text_color))
        self.playing_changed.emit(False)

    def seek(self, t: float) -> None:
        self.ctl.set_playhead(t)

    def _on_playhead(self, t: float) -> None:
        if self._playing and not self._own_move:
            # The user moved the playhead while playing: continue from there.
            self._t0 = t
            self._clock.restart()
            self._start_clock()
        if not self._playing:
            self.request_frame()
        else:
            self._update_time()

    def _tick(self) -> None:
        p = self.ctl.project
        if p is None or self.renderer is None:
            self.stop()
            return
        now = self._clock.nsecsElapsed()
        # Clock: the audio device's own "played" position when there is
        # sound output (so picture and sound stay in sync whatever the
        # device's latency), waiting on the first frame until the device
        # starts pulling; the wall clock when there's no audio output or
        # it never starts.
        if self._clock_mode == "wait":
            played = self.audio.played_seconds(now)
            if played is not None:
                self._clock_mode = "audio"
            elif now > AUDIO_START_TIMEOUT_NS:
                self._clock_mode = "wall"
                self._wall_base_ns = now
        if self._clock_mode == "audio":
            played = self.audio.played_seconds(now)
            t = self._t0 + (played if played is not None else 0.0)
        elif self._clock_mode == "wall":
            t = self._t0 + (now - self._wall_base_ns) / 1e9
        else:
            t = self._t0
        end = p.duration
        if t >= end:
            t = end
        self.audio.feed(self.renderer, end)
        self._own_move = True
        self.ctl.set_playhead(t)
        self._own_move = False
        fr = self.canvas.frame_rect()
        dpr = self.devicePixelRatioF()
        w = int(min(p.width, fr.width() * dpr))
        h = int(min(p.height, fr.height() * dpr))
        if w >= 4 and h >= 4:
            try:
                self.canvas.image = self.renderer.frame(t, w, h)
            except Exception:
                pass
            self.canvas.update()
        if t >= end:
            self.stop()

    def shutdown(self) -> None:
        self.stop()
        self._idle_timer.stop()
        self.audio.close()
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None
