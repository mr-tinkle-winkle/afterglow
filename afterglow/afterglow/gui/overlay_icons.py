"""
Small glyph icons for the input overlay, drawn with QPainter so no new image
resources are needed (the app's other icons are hand-supplied PNGs; swap
these for PNGs the same way if wanted). Dark glyph on transparent, like
the other header icons -- CustomButton.set_icon_pixmap() draws it scaled.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPixmap, QColor, QPen

_SIZE = 128


def overlay_icon(on: bool) -> QPixmap:
    """A keyboard glyph; with a diagonal strike-through when the overlay is off."""
    pm = QPixmap(_SIZE, _SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    white = QColor(38, 66, 104)   # dark slate blue, like the other header glyphs on their light buttons
    p.setPen(QPen(white, 7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    body = QRectF(10, 34, 108, 60)
    p.drawRoundedRect(body, 10, 10)
    p.setPen(Qt.NoPen)
    p.setBrush(white)
    # key rows
    for row, (n, y) in enumerate(((6, 46), (6, 60))):
        gap = 4
        w = (body.width() - 24 - gap * (n - 1)) / n
        for i in range(n):
            p.drawRoundedRect(QRectF(body.left() + 12 + i * (w + gap), y, w, 8), 2, 2)
    p.drawRoundedRect(QRectF(body.left() + 30, 76, body.width() - 60, 8), 2, 2)   # space bar
    if not on:
        p.setPen(QPen(white, 9, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(20, 108, 108, 20)
    p.end()
    return pm
