"""
The search field, redesigned per Max's direct correction: not a text
box that opens to the SIDE of the Search button (the old
_toggle_search_visibility placeholder), but a small popup that appears
UNDERNEATH the button and visually attaches to it with a little tail,
like a comic speech bubble. Outline color is the button/accent color;
fill color is the video card background color.

Revised again this round, per three more direct corrections:
1. The outline used to visibly "continue through" the tail as if the
   tail and body had two separate outlines, rather than reading as one
   continuous bubble outline -- caused by the tail's flat base sitting
   exactly COINCIDENT with the body's top edge (touching, not
   overlapping). QPainterPath.united()'s boolean-op result can leave a
   stray seam exactly where two shapes only touch at a shared edge
   rather than genuinely overlapping, since that edge doesn't fully
   resolve into either shape's interior. Fixed by extending the tail's
   base _TAIL_OVERLAP px past the body's top edge, into the body's own
   interior -- the seam then sits fully inside the united region and
   never becomes part of the outline Qt actually strokes.
2. The tail is now a curved, rounded shape (built from two cubic
   Beziers rather than three straight polygon edges) and wider at its
   base (_TAIL_WIDTH), so it reads as flowing into the bubble rather
   than a sharp triangular point stuck on top of it.
3. The bubble now centers itself directly under the anchor button
   (not left-aligned to it, which put a wide bubble mostly to one
   side) -- the tail sits at the bubble's horizontal center, which is
   therefore always aligned under the button too.

Qt.Popup gives the auto-close-on-outside-click behavior for free (a
mouse press anywhere outside the popup's geometry closes it) -- no
separate "click elsewhere" handling needed.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPoint
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit

from .. import config as config_module
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text

_TAIL_HEIGHT = 14   # visible height of the tail above the body's top edge
_TAIL_OVERLAP = 8   # how far the tail's base extends PAST that edge, into the body -- see module docstring, point 1
_TAIL_WIDTH = 44
_WIDTH = 260
_BODY_HEIGHT = 44


class SearchBubble(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        appearance = config_module.load().appearance
        self._theme = Theme(appearance)
        self._radius = appearance.rounded_corner_radius if appearance.rounded_corners_enabled else 12
        self._tail_x = _WIDTH // 2  # bubble is always centered under its anchor -- see show_below()

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

    def _build_path(self) -> QPainterPath:
        body_rect = QRectF(0, _TAIL_HEIGHT, _WIDTH, _BODY_HEIGHT)
        radius = min(self._radius, body_rect.height() / 2, body_rect.width() / 2)
        body_path = rounded_rect_path(body_rect, radius)

        base_y = _TAIL_HEIGHT + _TAIL_OVERLAP  # inside the body -- see module docstring, point 1
        peak_y = 0.0
        base_left = self._tail_x - _TAIL_WIDTH / 2
        base_right = self._tail_x + _TAIL_WIDTH / 2
        # A rounded tip (small flat-ish top) rather than a sharp point --
        # the two cubics curve inward from each base corner and meet
        # just shy of dead-center at the peak, which is what gives the
        # "flows into the bubble" look instead of a straight-edged
        # triangle (point 2 above).
        tip_half_width = _TAIL_WIDTH * 0.12
        curve_reach = (base_y - peak_y) * 0.6

        tail_path = QPainterPath()
        tail_path.moveTo(base_left, base_y)
        tail_path.cubicTo(
            base_left, base_y - curve_reach,
            self._tail_x - tip_half_width, peak_y + curve_reach * 0.4,
            self._tail_x - tip_half_width, peak_y,
        )
        tail_path.lineTo(self._tail_x + tip_half_width, peak_y)
        tail_path.cubicTo(
            self._tail_x + tip_half_width, peak_y + curve_reach * 0.4,
            base_right, base_y - curve_reach,
            base_right, base_y,
        )
        tail_path.lineTo(base_left, base_y)
        tail_path.closeSubpath()

        return body_path.united(tail_path)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        combined = self._build_path()
        painter.fillPath(combined, self._theme.card_background())
        pen = painter.pen()
        pen.setColor(self._theme.accent())
        pen.setWidthF(2)
        painter.setPen(pen)
        painter.drawPath(combined)
        painter.end()

    def show_below(self, anchor: QWidget) -> None:
        """Position directly BELOW `anchor` (the Search button), centered
        on it -- not off to one side, per Max's direct correction --
        with the tail (always at the bubble's own horizontal center)
        pointing straight up at the button."""
        anchor_global = anchor.mapToGlobal(QPoint(0, anchor.height()))
        center_x = anchor_global.x() + anchor.width() // 2
        self.move(center_x - _WIDTH // 2, anchor_global.y())
        self.show()
        self.line_edit.setFocus()
        self.update()
