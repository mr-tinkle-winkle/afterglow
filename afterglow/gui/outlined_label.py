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
            # A single drawPath with both a pen and a brush set both
            # strokes (the outline, centered on each glyph's own edge)
            # and fills (the interior) in one pass. The pen width is
            # used directly (NOT doubled) -- measured directly that
            # doubling it (an earlier version of this code did)
            # completely swallows the fill color at typical UI text
            # sizes: normal glyph strokes are only a couple pixels wide
            # at 10-17pt, so a pen much above ~1px leaves zero interior
            # pixels for the fill to show through at all, defeating the
            # whole two-tone effect. 1.0 (the default) is a good
            # balance verified directly -- clearly visible outline,
            # fill still dominant -- at the title's font size
            # specifically.
            pen = QPen(self._outline_color, self._outline_width)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
        else:
            # Below a certain font size (measured directly: this app's
            # 10px info/date/tag-name lines), even the thinnest usable
            # pen swallows the ENTIRE glyph interior -- normal letter
            # strokes are only ~1px wide at that size, so there's no
            # room left for the fill to show through at all, and the
            # text would just render as a solid block of outline_color
            # instead of the intended fill_color. Falling back to a
            # plain fill (no stroke) at outline_width<=0 keeps that
            # text genuinely readable in fill_color rather than
            # silently becoming outline_color instead.
            painter.setPen(Qt.NoPen)
        painter.setBrush(self._fill_color)
        painter.drawPath(path)
        painter.end()
        # Deliberately NOT calling super().paintEvent() -- this fully
        # replaces QLabel's own text rendering rather than layering on
        # top of it.
