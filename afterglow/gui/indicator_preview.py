"""
The clip indicator's live preview: a small stand-in screen (a 1280x720 canvas, drawn to scale)
on which the clapper loops enter -> clap -> exit -> circle (-> purple circle -> gone).

It runs the helper's own pieces -- the same `indicator.model.Model` state machine and the same
`indicator.paint.StackPainter` -- so what shows here is what the screen shows, including where
each animation starts, how it is clipped by the overlay surface, and the circle colours.  Only the
window is different.

The widget keeps a clock of its own (`_clock`, replaceable in tests).  A 60 fps timer runs only
while something moves; between loops the widget is idle and costs nothing.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import Qt, QRectF, QTimer, Signal, QSize
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget

from .. import config as config_module
from ..indicator import ANCHORS, layout
from ..indicator.model import Model, Style
from ..indicator.paint import StackPainter
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text

CANVAS_W, CANVAS_H = 1280.0, 720.0
KEY_SCREEN = "preview"

# When the OBS save is "confirmed", when the clip "arrives", when its input overlay "finishes"
# -- seconds after the start of a loop, or after the previous step for the later ones.
CLAP_AFTER_ENTER = 0.35
DONE_AFTER_CLAP = 1.6
OVERLAY_AFTER_CLAP = 1.6        # the overlay variant: gray circle, then purple
OVERLAY_DONE_AFTER = 1.3
IDLE_BETWEEN_LOOPS = 0.9


class IndicatorPreview(QWidget):
    """``style`` is a dict like indicator_client.build_style() produces; ``set_style`` restarts the loop."""

    def __init__(self, parent=None, with_overlay: bool = True, clock=time.monotonic):
        super().__init__(parent)
        self._clock = clock
        self._with_overlay = with_overlay
        self._style = Style.from_dict({})
        self._overrides: dict = {}
        self._model = Model()
        self._sp: "StackPainter | None" = None
        self._origin = (0.0, 0.0)
        self._surface = (0.0, 0.0)
        self._n = 0
        self._cur: "str | None" = None
        self._t0 = 0.0
        self._sent: set = set()
        self._idle_until: "float | None" = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.step)
        self._idle_timer = QTimer(self)                  # the pause between two loops: nothing runs meanwhile
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self.restart)
        self._theme = Theme(config_module.load_readonly().appearance)
        self.setMinimumSize(320, 180)
        self.restart()

    # ------------------------------------------------------------ control

    def set_style(self, style: dict) -> None:
        self._style_dict = dict(style or {})
        self._apply()

    def set_overrides(self, **kw) -> None:
        """Temporarily play with some settings changed (hovering an animation in a dropdown)."""
        self._overrides = {k: v for k, v in kw.items() if v is not None}
        self._apply()

    def clear_overrides(self) -> None:
        if self._overrides:
            self._overrides = {}
            self._apply()

    def style(self) -> Style:
        return self._style

    def _apply(self) -> None:
        d = dict(getattr(self, "_style_dict", {}) or {})
        d.update(self._overrides)
        self._style = Style.from_dict(d)
        self.restart()

    def restart(self) -> None:
        st = self._style
        self._surface = tuple(float(math.ceil(v)) for v in
                              layout.surface_size(st.anchor, st.size, st.padding_x, st.padding_y, (CANVAS_W, CANVAS_H)))
        self._origin = layout.surface_origin(st.anchor, self._surface, QRectF(0, 0, CANVAS_W, CANVAS_H))
        self._sp = StackPainter(st.anchor, st.size, st.padding_x, st.padding_y, self._surface)
        self._model = Model()
        self._n += 1
        self._cur = f"preview-{self._n}"
        self._sent = set()
        self._idle_until = None
        self._idle_timer.stop()
        self._t0 = self._clock()
        self._model.event(self._cur, "start", 0.0, st, KEY_SCREEN)
        self._sent.add("start")
        self._start_timer()
        self.update()

    def _start_timer(self) -> None:
        if self.isVisible() and not self._timer.isActive():
            self._timer.start()

    # ------------------------------------------------------------ the loop

    def _script(self, now: float) -> None:
        """Send the events that are due at ``now`` (seconds since the loop began)."""
        ind = self._model._inds.get(self._cur)
        if ind is None:
            return
        enter_dur = ind.enter_dur()
        t_clap = enter_dur + CLAP_AFTER_ENTER
        steps = [("clap", t_clap)]
        if self._with_overlay:
            steps += [("overlay", t_clap + OVERLAY_AFTER_CLAP), ("overlay_done", t_clap + OVERLAY_AFTER_CLAP + OVERLAY_DONE_AFTER)]
        else:
            steps += [("done", t_clap + DONE_AFTER_CLAP)]
        for name, at in steps:
            if name not in self._sent and now >= at:
                self._sent.add(name)
                self._model.event(self._cur, name, at)

    def step(self) -> None:
        now = self._clock() - self._t0
        if self._idle_until is not None:
            if now >= self._idle_until:
                self.restart()
            return
        self._script(now)
        self._model.tick(now)
        if len(self._model) == 0:
            self._idle_until = now + IDLE_BETWEEN_LOOPS
            self._timer.stop()
            if self.isVisible():
                self._idle_timer.start(int(IDLE_BETWEEN_LOOPS * 1000))
        self.update()

    def finished(self) -> bool:
        return self._idle_until is not None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.restart()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        self._idle_timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------ painting

    def sizeHint(self) -> QSize:
        return QSize(480, 270)

    def _canvas_rect(self) -> QRectF:
        w, h = self.width(), self.height()
        s = min(w / CANVAS_W, h / CANVAS_H)
        cw, ch = CANVAS_W * s, CANVAS_H * s
        return QRectF((w - cw) / 2, (h - ch) / 2, cw, ch)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        cr = self._canvas_rect()
        radius = 8.0
        path = rounded_rect_path(cr, radius)
        g = QLinearGradient(cr.topLeft(), cr.bottomRight())
        g.setColorAt(0.0, QColor("#2a2f3a"))
        g.setColorAt(1.0, QColor("#14161b"))
        p.fillPath(path, g)
        # a few window-ish shapes so the position reads against something
        p.save()
        p.setClipPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 12))
        p.drawRoundedRect(QRectF(cr.left() + cr.width() * 0.12, cr.top() + cr.height() * 0.14, cr.width() * 0.5, cr.height() * 0.55), 6, 6)
        p.setBrush(QColor(255, 255, 255, 8))
        p.drawRoundedRect(QRectF(cr.left() + cr.width() * 0.36, cr.top() + cr.height() * 0.3, cr.width() * 0.5, cr.height() * 0.5), 6, 6)
        # the overlay surface, clipped like the compositor clips it
        s = cr.width() / CANVAS_W
        ox, oy = self._origin
        W, H = self._surface
        p.translate(cr.left(), cr.top())
        p.scale(s, s)
        p.translate(ox, oy)
        p.setClipRect(QRectF(0, 0, W, H))
        now = self._clock() - self._t0 if self._idle_until is None else 0.0
        if self._sp is not None and self._idle_until is None:
            self._sp.paint_stack(p, self._model, (KEY_SCREEN, self._style.anchor), now)
        p.restore()
        p.setPen(QPen(self._theme.accent(), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.end()


class AnchorPicker(QWidget):
    """A 3x3 grid of cells -- the eight anchors, the centre disabled -- one selected."""
    changed = Signal(str)

    _CELLS = {(0, 0): "top_left", (0, 1): "top", (0, 2): "top_right",
              (1, 0): "left", (1, 2): "right",
              (2, 0): "bottom_left", (2, 1): "bottom", (2, 2): "bottom_right"}

    def __init__(self, anchor: str = "bottom_right", parent=None):
        super().__init__(parent)
        self._anchor = anchor if anchor in ANCHORS else "bottom_right"
        self._hover: "str | None" = None
        self._theme = Theme(config_module.load_readonly().appearance)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(132, 96)
        self.setToolTip("Where the clapper appears on the screen")

    def anchor(self) -> str:
        return self._anchor

    def set_anchor(self, anchor: str) -> None:
        if anchor in ANCHORS and anchor != self._anchor:
            self._anchor = anchor
            self.update()
            self.changed.emit(anchor)

    def cell_rect(self, row: int, col: int) -> QRectF:
        gap = 4.0
        cw, ch = (self.width() - 2 * gap) / 3, (self.height() - 2 * gap) / 3
        return QRectF(col * (cw + gap), row * (ch + gap), cw, ch)

    def _cell_at(self, pos) -> "str | None":
        for (r, c), name in self._CELLS.items():
            if self.cell_rect(r, c).contains(pos):
                return name
        return None

    def mouseMoveEvent(self, event) -> None:
        h = self._cell_at(event.position())
        if h != self._hover:
            self._hover = h
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()

    def mousePressEvent(self, event) -> None:
        name = self._cell_at(event.position())
        if name:
            self.set_anchor(name)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        accent = self._theme.accent()
        card = self._theme.card_background()
        for r in range(3):
            for c in range(3):
                rect = self.cell_rect(r, c)
                name = self._CELLS.get((r, c))
                path = rounded_rect_path(rect, 6)
                if name is None:                                  # the centre: not an anchor
                    p.setOpacity(0.35)
                    p.fillPath(path, card)
                    p.setOpacity(1.0)
                    continue
                bg = self._theme.turquoise() if name == self._anchor else card
                if name == self._hover and name != self._anchor:
                    bg = bg.lighter(125)
                p.fillPath(path, bg)
                p.setPen(QPen(accent, 1.2))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                # a dot at the cell's own corner / edge says which way it is
                p.setPen(Qt.NoPen)
                p.setBrush(contrast_text(bg))
                d = 3.5
                cx = rect.center().x() + (-1 if name.endswith("left") or name == "left" else 1 if name.endswith("right") or name == "right" else 0) * rect.width() * 0.22
                cy = rect.center().y() + (-1 if name.startswith("top") else 1 if name.startswith("bottom") else 0) * rect.height() * 0.22
                p.drawEllipse(QRectF(cx - d, cy - d, 2 * d, 2 * d))
        p.end()
