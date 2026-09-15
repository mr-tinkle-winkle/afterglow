"""
The search field, redesigned per Max's direct correction: not a text
box that opens to the SIDE of the Search button (the old
_toggle_search_visibility placeholder), but a small popup that appears
UNDERNEATH the button and visually attaches to it with a little
triangular tail, like a comic speech bubble. Outline color is the
button/accent color; fill color is the video card background color --
"the color scheme as mentioned" reused here in the specific pairing
Max asked for, distinct from the Sort popover's own accent-bordered/
card-filled panel only in which two colors play which role (there they
match; here they're each pulled from a specific named source).

Qt.Popup gives the auto-close-on-outside-click behavior for free (a
mouse press anywhere outside the popup's geometry closes it) -- no
separate "click elsewhere" handling needed.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPoint
from PySide6.QtGui import QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit

from .. import config as config_module
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text

_TAIL_HEIGHT = 10
_TAIL_WIDTH = 18
_WIDTH = 260
_BODY_HEIGHT = 44


class SearchBubble(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        appearance = config_module.load().appearance
        self._theme = Theme(appearance)
        self._radius = appearance.rounded_corner_radius if appearance.rounded_corners_enabled else 12
        self._tail_x = _WIDTH // 4  # overwritten per-show in show_below()

        self.setFixedSize(_WIDTH, _TAIL_HEIGHT + _BODY_HEIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, _TAIL_HEIGHT + 8, 14, 8)

        self.line_edit = QLineEdit(self)
        self.line_edit.setPlaceholderText("Search title or description...")
        text_color = contrast_text(self._theme.card_background()).name()
        # Scoped to QLineEdit specifically (not a bare "background-color:
        # ..." with no type selector) -- deliberately avoiding the exact
        # unscoped-stylesheet-cascade mistake documented in HANDOFF.md;
        # this selector only ever matches this one widget anyway.
        self.line_edit.setStyleSheet(
            f"QLineEdit {{ background: transparent; border: none; color: {text_color}; }}"
        )
        layout.addWidget(self.line_edit)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        body_rect = QRectF(0, _TAIL_HEIGHT, _WIDTH, _BODY_HEIGHT)
        radius = min(self._radius, body_rect.height() / 2, body_rect.width() / 2)
        body_path = rounded_rect_path(body_rect, radius)

        tail = QPolygonF([
            QPoint(round(self._tail_x - _TAIL_WIDTH / 2), _TAIL_HEIGHT),
            QPoint(round(self._tail_x), 0),
            QPoint(round(self._tail_x + _TAIL_WIDTH / 2), _TAIL_HEIGHT),
        ])
        tail_path = QPainterPath()
        tail_path.addPolygon(tail)
        tail_path.closeSubpath()

        combined = body_path.united(tail_path)
        painter.fillPath(combined, self._theme.card_background())
        pen = painter.pen()
        pen.setColor(self._theme.accent())
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.drawPath(combined)
        painter.end()

    def show_below(self, anchor: QWidget) -> None:
        """Position directly under `anchor` (the Search button), with
        the tail pointing up at roughly its horizontal center."""
        anchor_global = anchor.mapToGlobal(QPoint(0, anchor.height()))
        # Keep the tail over the button even when the bubble itself is
        # wider than the button and had to shift left to stay on
        # screen/aligned -- clamped so the tail never renders outside
        # the bubble's own rounded body.
        self._tail_x = max(_TAIL_WIDTH, min(_WIDTH - _TAIL_WIDTH, anchor.width() // 2))
        self.move(anchor_global.x(), anchor_global.y())
        self.show()
        self.line_edit.setFocus()
        self.update()
