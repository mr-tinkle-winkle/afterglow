"""
A rounded, theme-colored button replacing native/KDE-styled QToolButtons
-- the "Custom Buttons" setting's actual implementation. Used for the
Library's Search/Refresh/Filters/Sort By/Info row for now; the Local/
Uploaded page switcher is a separate, bigger piece of work still using
the native QTabBar (see HANDOFF.md).

Subclasses QToolButton (not QPushButton) specifically so existing
QToolButton-only features -- setPopupMode(InstantPopup) + setMenu(),
already used by Filters/Sort By/Info -- keep working completely
unchanged; only the PAINTING is replaced, not the click/popup behavior.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QToolButton

from .. import config as config_module
from .rounded_rect import rounded_rect_path
from .theme import Theme


class CustomButton(QToolButton):
    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCursor(Qt.PointingHandCursor)
        appearance = config_module.load().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 0
        # Clamp to half the button's own (usually short) height -- a
        # small button fully rounded into a pill shape at the default
        # 24px radius is fine, but rounded_rect_path's own half-of-
        # smaller-dimension clamp already handles this; being explicit
        # here just keeps a very short button from ever wanting a
        # radius bigger than its own height in the first place.
        radius = min(radius, rect.height() / 2) if radius else 0

        bg = self._theme.button_color()
        if self.isDown():
            bg = bg.darker(125)
        elif self.underMouse():
            bg = bg.lighter(115)

        if radius:
            painter.setClipPath(rounded_rect_path(rect, radius))
        painter.fillRect(self.rect(), bg)
        painter.setClipping(False)

        painter.setPen(self._theme.button_text_color())
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())
        painter.end()
        # Deliberately NOT calling super().paintEvent() -- this fully
        # replaces QToolButton's native/KDE-styled rendering rather than
        # layering on top of it (the whole point of "Custom Buttons").

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        self.update()
