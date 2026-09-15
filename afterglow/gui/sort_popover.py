"""
Replaces the old combined Filters/Sort By/Info QMenu (three labeled
sections stacked vertically in one dropdown) with an actual custom
popup panel: three horizontally-tiled tabs, each switching a real page
of widgets below rather than scrolling through one long vertical list.

Corner rounding, per Max's instruction: the two OUTER tabs round only
their own outer top corner (left tab: top-left; right tab: top-right);
the MIDDLE tab has no rounding at all, and no tab rounds a corner that
touches a neighboring tab or the content area below it -- same "don't
round a touching seam" convention already used for the Local/Uploaded
tab icons (see library_page.py's _composite_tab_icon). The content
area below rounds only its own bottom two corners, for the same
reason (its top edge is flush against the tab strip).

Colors come from Theme, same as the rest of the UI Update: the active
tab and the content panel's border use accent(); inactive tabs and the
content panel's fill use card_background() -- "the color scheme as
mentioned" per how this was actually asked for, i.e. the same
Afterglow Theme palette already used for buttons/cards elsewhere,
not a new one-off color choice.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPoint
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QAbstractButton

from .. import config as config_module
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text


class _PopoverTabButton(QAbstractButton):
    def __init__(self, text: str, position: str, theme: Theme, radius: float, parent=None):
        """position: 'left' | 'middle' | 'right' -- decides which single
        corner (if any) this tab is allowed to round."""
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._position = position
        self._theme = theme
        self._radius = radius
        self.setMinimumHeight(32)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())

        top_left = self._position == "left"
        top_right = self._position == "right"
        radius = min(self._radius, rect.height()) if self._radius else 0

        bg = self._theme.accent() if self.isChecked() else self._theme.card_background()
        if self.underMouse() and not self.isChecked():
            bg = bg.lighter(115)

        if radius:
            path = rounded_rect_path(
                rect, radius,
                top_left=top_left, top_right=top_right,
                bottom_left=False, bottom_right=False,
            )
            painter.fillPath(path, bg)
        else:
            painter.fillRect(self.rect(), bg)

        painter.setPen(contrast_text(bg))
        painter.drawText(self.rect(), Qt.AlignCenter, self.text())
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class _RoundedContentArea(QStackedWidget):
    """The page content sits inside this -- painted with a rounded-
    bottom-corners card (accent-colored outline, card_background fill)
    behind whatever page widget is showing, matching the visual weight
    of a VideoCard's own info box."""

    def __init__(self, theme: Theme, radius: float, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._radius = radius
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 0, -1, -1)
        radius = min(self._radius, rect.height() / 2, rect.width() / 2) if self._radius else 0
        path = rounded_rect_path(rect, radius, top_left=False, top_right=False)
        painter.fillPath(path, self._theme.card_background())
        pen = painter.pen()
        pen.setColor(self._theme.accent())
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()
        super().paintEvent(event)


class SortPopover(QWidget):
    TAB_LABELS = ("Filters", "Sort By", "Info")

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        appearance = config_module.load().appearance
        theme = Theme(appearance)
        radius = appearance.rounded_corner_radius if appearance.rounded_corners_enabled else 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.setSpacing(0)
        positions = ["left", "middle", "right"]
        self._tab_buttons: list[_PopoverTabButton] = []
        for label, position in zip(self.TAB_LABELS, positions):
            btn = _PopoverTabButton(label, position, theme, radius)
            btn.clicked.connect(lambda _checked, i=len(self._tab_buttons): self.set_current_index(i))
            tab_row.addWidget(btn, stretch=1)
            self._tab_buttons.append(btn)
        outer.addLayout(tab_row)

        self._stack = _RoundedContentArea(theme, radius)
        outer.addWidget(self._stack)

        self.setFixedWidth(320)
        self._tab_buttons[0].setChecked(True)

    def set_page_widget(self, index: int, widget: QWidget) -> None:
        """Swap in a freshly-built page widget at `index`, preserving
        whichever page is currently showing -- called on every refresh()
        since the Filters page's content depends on which tags
        currently exist (same reason the old QMenu was rebuilt on every
        refresh too)."""
        current = self._stack.currentIndex()
        old = self._stack.widget(index) if index < self._stack.count() else None
        if old is not None:
            self._stack.removeWidget(old)
            old.deleteLater()
        self._stack.insertWidget(index, widget)
        if 0 <= current < self._stack.count():
            self._stack.setCurrentIndex(current)

    def set_current_index(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == index)

    def show_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        pos = anchor.mapToGlobal(QPoint(0, anchor.height()))
        self.move(pos)
        self.show()
