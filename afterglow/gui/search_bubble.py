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

from PySide6.QtCore import Qt, QRectF, QPoint, Signal
from PySide6.QtGui import QPainter, QPainterPath
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit

from .. import config as config_module
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text
from .custom_checkbox import CustomCheckBox

_TAIL_HEIGHT = 20   # visible height of the tail above the body's top edge
_TAIL_OVERLAP = 8   # how far the tail's base extends PAST that edge, into the body -- see module docstring, point 1
_TAIL_WIDTH = 44
_TAIL_GAP = 6        # blank space between the anchor button and the tail's own tip -- "float just a bit off" the button
_WIDTH = 260
_BODY_HEIGHT = 44


class SearchBubble(QWidget):
    # Emitted when the person actually wants to run the search --
    # pressing Enter in the field, or clicking the confirm checkbox
    # next to it -- NOT on every keystroke. Typing alone only updates
    # what's visibly in the box; LibraryPage reads line_edit.text()
    # itself when this fires, rather than this signal carrying the text.
    search_confirmed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        appearance = config_module.load().appearance
        self._theme = Theme(appearance)
        self._radius = appearance.rounded_corner_radius if appearance.rounded_corners_enabled else 12
        self._tail_x = _WIDTH // 2  # bubble is always centered under its anchor -- see show_below()

        self.setFixedSize(_WIDTH, _TAIL_HEIGHT + _BODY_HEIGHT)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, _TAIL_HEIGHT + 8, 14, 8)
        row.setSpacing(8)

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
        # Search no longer runs live-as-you-type -- only on Enter or the
        # confirm checkbox below, per Max's direct instruction (typing
        # alone used to trigger _do_refresh on every keystroke via a
        # chain this widget doesn't own; that wiring now waits for one
        # of these two signals instead -- see LibraryPage).
        self.line_edit.returnPressed.connect(self.search_confirmed.emit)
        row.addWidget(self.line_edit, stretch=1)

        self.confirm_checkbox = CustomCheckBox()
        self.confirm_checkbox.setToolTip("Search")
        self.confirm_checkbox.clicked.connect(self.search_confirmed.emit)
        row.addWidget(self.confirm_checkbox)

    def _build_path(self) -> QPainterPath:
        body_rect = QRectF(0, _TAIL_HEIGHT, _WIDTH, _BODY_HEIGHT)
        radius = min(self._radius, body_rect.height() / 2, body_rect.width() / 2)
        body_path = rounded_rect_path(body_rect, radius)

        base_y = _TAIL_HEIGHT + _TAIL_OVERLAP  # inside the body -- see module docstring, point 1
        peak_y = 0.0
        base_left = self._tail_x - _TAIL_WIDTH / 2
        base_right = self._tail_x + _TAIL_WIDTH / 2
        # A genuine POINT at the top now (not a small rounded/flat tip
        # like before) -- per Max's direct correction, the tail should
        # "reach a point where it ends at the top." The curve itself
        # is one cubic per side, with the first control point pulled
        # HORIZONTALLY toward the peak (same y as the base, not above
        # it) so the curve leaves the body's flat top edge on a near-
        # horizontal tangent -- "it should flatten out as it reaches
        # the text bubble" -- rather than shooting straight up from the
        # base corner. The second control point sits close to the
        # peak itself, which is what pulls the curve sharply back
        # inward at the top and gives it the pronounced "bump"/arc
        # shape Max described, instead of a straight diagonal edge.
        flare = _TAIL_WIDTH * 0.35
        arc_reach = (base_y - peak_y) * 0.25

        tail_path = QPainterPath()
        tail_path.moveTo(base_left, base_y)
        tail_path.cubicTo(
            base_left + flare, base_y,
            self._tail_x, peak_y + arc_reach,
            self._tail_x, peak_y,
        )
        tail_path.cubicTo(
            self._tail_x, peak_y + arc_reach,
            base_right - flare, base_y,
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
        pointing up at the button, floating _TAIL_GAP px clear of it
        rather than touching (per his direct correction on this round
        too) -- the gap is just blank space added to the move()
        position, since the tail's own tip already sits at the very
        top (y=0) of this widget's fixed geometry."""
        anchor_global = anchor.mapToGlobal(QPoint(0, anchor.height()))
        center_x = anchor_global.x() + anchor.width() // 2
        self.move(center_x - _WIDTH // 2, anchor_global.y() + _TAIL_GAP)
        self.show()
        self.line_edit.setFocus()
        self.update()
