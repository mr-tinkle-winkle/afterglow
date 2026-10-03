"""
The Qt overlay window of the indicator helper: one transparent, click-through surface per
(screen, anchor), painting every live capture of that stack with the shared drawing code.

On Wayland it is a layer-shell surface (`layershell.configure`) anchored to the anchor's edge
with margin 0 -- the padding is applied in the drawing (`layout`), so the enter / exit
animations can start and finish truly off-screen.  On X11 it is a frameless, always-on-top,
tool, input-transparent window placed by geometry.  It is created when the first capture of
its stack appears and destroyed when the last one is gone, so nothing sits above a fullscreen
game between clips.

Clicks: the surface ignores the pointer, except over a processing element when the processing
throttle is on (``set_click_rects``): a click there toggles the throttle's bypass.  On Wayland the
surface's input region is set to just those rects (``QWindow.setMask``, which Qt's Wayland
plugin turns into the surface's input region; the input-transparent flag gives an empty region
the rest of the time).  On X11 a mask would also clip the drawing, so a small invisible window
(``_ClickCatcher``) sits over the rects instead.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QGuiApplication, QRegion
from PySide6.QtWidgets import QWidget

from . import layout
from .paint import StackPainter
from . import layershell


class _ClickCatcher(QWidget):
    """X11: an invisible always-on-top window over the clickable rects (the surface itself stays
    input-transparent, so its drawing is never clipped)."""

    def __init__(self, on_click):
        super().__init__(None)
        self.on_click = on_click
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
                            | Qt.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event) -> None:
        pass

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.on_click()
        event.accept()


class IndicatorSurface(QWidget):
    def __init__(self, model, key, anchor: str, size: float, pad_x: float, pad_y: float, screen,
                 clock, use_layer_shell: bool, on_click=None):
        super().__init__(None)
        self.model, self.key, self.anchor = model, key, anchor
        self.size_setting, self.pad_x, self.pad_y = float(size), float(pad_x), float(pad_y)
        self.screen_obj = screen
        self._clock = clock
        self._layer = use_layer_shell
        self.configured_as_layer = False
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus
        if not use_layer_shell and QGuiApplication.platformName() == "xcb":
            flags |= Qt.X11BypassWindowManagerHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        geo = screen.geometry() if screen is not None else None
        W, H = layout.surface_size(anchor, size, pad_x, pad_y, (geo.width(), geo.height()) if geo else None)
        self.surface = (float(math.ceil(W)), float(math.ceil(H)))      # whole px: slot rects and the widget agree
        self.resize(int(self.surface[0]), int(self.surface[1]))
        self._painter = StackPainter(anchor, size, pad_x, pad_y, self.surface)
        self.on_click = on_click
        self._click_rects: "list[QRectF]" = []
        self._region_key = None
        self._catcher: "_ClickCatcher | None" = None
        self._base_flags = flags

    # ------------------------------------------------------------ clicks

    @property
    def painter(self) -> StackPainter:
        return self._painter

    def update_clicks(self, now: float) -> None:
        self.set_click_rects(self._painter.click_rects(self.model, self.key, now) if self.on_click else [])

    def set_click_rects(self, rects) -> None:
        self._click_rects = [QRectF(r) for r in rects]
        key = tuple(tuple(int(v) for v in (r.left(), r.top(), r.width(), r.height())) for r in self._click_rects)
        if key == self._region_key:
            return
        self._region_key = key
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self._click_rects)
        if self._layer:
            self._apply_input_region()
        elif QGuiApplication.platformName() == "xcb":
            self._apply_catcher()

    def _apply_input_region(self) -> None:
        win = self.windowHandle()
        if win is None:
            return
        if self._click_rects:
            region = QRegion()
            for r in self._click_rects:
                region = region.united(QRegion(r.toAlignedRect()))
            win.setMask(region)
            win.setFlags(win.flags() & ~Qt.WindowTransparentForInput)
            win.setMask(QRegion())            # re-apply: dropping the flag may reset the input region
            win.setMask(region)
        else:
            win.setMask(QRegion())
            win.setFlags(win.flags() | Qt.WindowTransparentForInput)

    def _apply_catcher(self) -> None:
        if not self._click_rects:
            if self._catcher is not None:
                self._catcher.hide()
            return
        if self._catcher is None:
            self._catcher = _ClickCatcher(self._clicked)
        box = QRectF(self._click_rects[0])
        for r in self._click_rects[1:]:
            box = box.united(r)
        top_left = self.mapToGlobal(box.topLeft().toPoint())
        self._catcher.setGeometry(top_left.x(), top_left.y(), max(1, int(box.width())), max(1, int(box.height())))
        self._catcher.show()
        self._catcher.raise_()

    def _clicked(self) -> None:
        if self.on_click:
            self.on_click()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and any(r.contains(event.position()) for r in self._click_rects):
            self._clicked()
        event.accept()

    def hideEvent(self, event) -> None:
        if self._catcher is not None:
            self._catcher.hide()
        super().hideEvent(event)

    # ------------------------------------------------------------ showing

    def present(self) -> None:
        """Create the native window, make it a layer surface (Wayland) or place it (X11), show it."""
        self.winId()                                   # the QWindow must exist before layer-shell configure
        win = self.windowHandle()
        if self.screen_obj is not None and win is not None:
            win.setScreen(self.screen_obj)
        if self._layer:
            self.configured_as_layer = layershell.configure(win, layout.anchor_edges(self.anchor))
        else:
            geo = self.screen_obj.geometry() if self.screen_obj is not None else QRectF(0, 0, 1920, 1080)
            x, y = layout.surface_origin(self.anchor, self.surface, QRectF(geo))
            self.move(int(x), int(y))
        self.show()

    # ------------------------------------------------------------ painting

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        self._painter.paint_stack(p, self.model, self.key, self._clock())
        p.end()

    def paint_frame(self, p: QPainter, style, fr, now: float) -> None:
        self._painter.paint_frame(p, style, fr, now)
