"""
The Qt overlay window of the indicator helper: one transparent, click-through surface per
(screen, anchor), painting every live capture of that stack with the shared drawing code.

On Wayland it is a layer-shell surface (`layershell.configure`) anchored to the anchor's edge
with margin 0 -- the padding is applied in the drawing (`layout`), so the enter / exit
animations can start and finish truly off-screen.  On X11 it is a frameless, always-on-top,
tool, input-transparent window placed by geometry.  It is created when the first capture of
its stack appears and destroyed when the last one is gone, so nothing sits above a fullscreen
game between clips.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QGuiApplication
from PySide6.QtWidgets import QWidget

from . import layout
from .paint import StackPainter
from . import layershell


class IndicatorSurface(QWidget):
    def __init__(self, model, key, anchor: str, size: float, pad_x: float, pad_y: float, screen,
                 clock, use_layer_shell: bool):
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
