"""
Wraps a page widget with a gradient image behind it, inset by a small
margin so a border of the gradient shows around the page's edges --
Library and Editor otherwise sat directly against the stack's background
with nothing visually separating them, which read as "a little empty in
between."

The wrapped widget's own colors/content are unaffected -- it just gets
placed on top of the gradient with a margin around it, rather than filling
the whole container.
"""
from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget, QVBoxLayout

from .resources import resource_qpixmap

BORDER_MARGIN = 10


class GradientBackdrop(QWidget):
    def __init__(self, content: QWidget, gradient_image_name: str, parent=None):
        super().__init__(parent)
        self._gradient_pixmap = resource_qpixmap(gradient_image_name)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(BORDER_MARGIN, BORDER_MARGIN, BORDER_MARGIN, BORDER_MARGIN)
        layout.addWidget(content)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # Scaled to fit this widget's current full size (not the inset
        # content area) -- it's the border peeking out around the
        # content that's the point.
        painter.drawPixmap(QRect(0, 0, self.width(), self.height()), self._gradient_pixmap)
        painter.end()
        super().paintEvent(event)
