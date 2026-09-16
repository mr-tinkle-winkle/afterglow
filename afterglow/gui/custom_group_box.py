"""
Replaces native/KDE QGroupBox chrome (used for every "section" header
in Settings, plus tag-category grouping in the Sort popover's Filters
page) with a custom-painted rounded box and title, matching the rest
of the Afterglow custom-widget system.

Deliberately a drop-in for the exact construction pattern already used
everywhere: `group = CustomGroupBox("Title")` then
`layout = QVBoxLayout(group)` (or QFormLayout, etc.) works completely
unchanged at every call site -- setContentsMargins() here (reserving
room at the top for the painted title) is picked up automatically by
whatever layout gets attached next, since Qt seeds a layout's initial
margins from the widget's own contentsMargins when first assigned,
unless the caller later calls setContentsMargins on the layout itself
(none of the existing call sites do).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget

from .. import config as config_module
from .rounded_rect import rounded_rect_path
from .theme import Theme

_TITLE_HEIGHT = 26


class CustomGroupBox(QWidget):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        appearance = config_module.load().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self.setContentsMargins(12, _TITLE_HEIGHT + 6, 12, 12)

    def setTitle(self, title: str) -> None:
        self._title = title
        self.update()

    def title(self) -> str:
        return self._title

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        rect = QRectF(self.rect()).adjusted(1, _TITLE_HEIGHT / 2, -1, -1)
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 8
        radius = min(radius, rect.height() / 2, rect.width() / 2) if radius else 0
        path = rounded_rect_path(rect, radius) if radius else None

        pen = painter.pen()
        pen.setColor(self._theme.accent())
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        if path is not None:
            painter.drawPath(path)
        else:
            painter.drawRect(rect)

        if self._title:
            # A small punched-out gap behind the title text, same idea
            # as a native QGroupBox's title notch in its own top border
            # -- filled with the WINDOW/page background (approximated
            # here as the parent widget's own palette window color,
            # since these boxes sit directly on a plain settings page)
            # so the border line doesn't visibly run behind the text.
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            from PySide6.QtGui import QFontMetrics
            fm = QFontMetrics(font)
            text_width = fm.horizontalAdvance(self._title)
            gap_rect = QRectF(rect.x() + 8, 0, text_width + 8, _TITLE_HEIGHT)
            bg = self.palette().window().color()
            painter.fillRect(gap_rect, bg)
            painter.setPen(QColor(self._appearance.card_text_color))
            painter.drawText(gap_rect.adjusted(4, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, self._title)
        painter.end()
