"""
A QLabel that draws its text with a colored outline (stroke) behind a
colored fill, instead of QLabel's own plain (outline-less) rendering --
the default styling for all on-card text (title, info/date lines, tag
names), per Max's request for readable text over the info box's
accent-colored background.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel


class OutlinedLabel(QLabel):
    def __init__(self, text: str = "", parent=None,
                 fill_color: str = "#9bcbff", outline_color: str = "#3669a0",
                 outline_width: float = 1.0):
        super().__init__(text, parent)
        self._fill_color = QColor(fill_color)
        self._outline_color = QColor(outline_color)
        self._outline_width = outline_width

    def set_colors(self, fill_color: str, outline_color: str, outline_width: float = 1.0) -> None:
        self._fill_color = QColor(fill_color)
        self._outline_color = QColor(outline_color)
        self._outline_width = outline_width
        self.update()

    def paintEvent(self, event) -> None:
        text = self.text()
        if not text or not text.strip():
            return  # nothing to draw -- also avoids stroking a lone placeholder space visibly
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        fm = QFontMetrics(self.font())
        text_width = fm.horizontalAdvance(text)
        align = self.alignment()
        if align & Qt.AlignHCenter:
            x = (self.width() - text_width) / 2
        elif align & Qt.AlignRight:
            x = self.width() - text_width
        else:
            x = 0.0
        if align & Qt.AlignVCenter:
            y = (self.height() + fm.ascent() - fm.descent()) / 2
        else:
            y = float(fm.ascent())

        path = QPainterPath()
        path.addText(x, y, self.font(), text)

        if self._outline_width > 0:
            # TWO SEPARATE passes, not one combined stroke+fill drawPath
            # call (an earlier version of this did that, and it was
            # wrong -- reported directly as "the outline bleeds onto
            # the text, making the text just the color of the
            # outline"). A single drawPath with both a pen and brush
            # set strokes CENTERED on the path, eating into the fill
            # from BOTH sides equally -- at typical UI text sizes,
            # where glyph strokes are only a couple pixels wide to
            # begin with, that inward bite alone is enough to consume
            # the entire glyph interior, leaving nothing of the fill
            # color visible at all.
            #
            # The fix: stroke-only first (bottom layer, pen set, brush
            # NoBrush) draws the outline extending BOTH inward and
            # outward from the glyph's true edge; fill-only second (top
            # layer, brush set, pen NoPen) draws the EXACT original
            # glyph shape completely opaque on top, which fully
            # restores/covers the inward half the stroke pass drew into
            # -- what's left visible is only the OUTWARD half of the
            # stroke, a clean border around an untouched, fully-colored
            # fill. Doubling the pen width here (vs. not doubling, from
            # the old single-pass version) is intentional and correct
            # under this new technique specifically: since half of it
            # is always going to be covered by the fill pass, the
            # configured outline_width should describe the VISIBLE
            # (outward-only) thickness, not the pen's own raw width.
            outline_pen = QPen(self._outline_color, self._outline_width * 2)
            outline_pen.setJoinStyle(Qt.RoundJoin)
            outline_pen.setCapStyle(Qt.RoundCap)
            painter.setPen(outline_pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._fill_color)
        painter.drawPath(path)
        painter.end()
        # Deliberately NOT calling super().paintEvent() -- this fully
        # replaces QLabel's own text rendering rather than layering on
        # top of it.
