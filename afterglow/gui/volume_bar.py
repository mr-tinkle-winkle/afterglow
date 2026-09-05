"""
Horizontal volume control: a speaker icon on the left, then a track that
fills the rest of the widget's width. The track shows the INACTIVE
gradient as a static full-width background, with the ACTIVE gradient drawn
on top of it resized to the current value's proportion (same "resize to
fit the selected span" approach as the trim timeline's connector gradient),
and the marker icon centered on the current value's position.

Deliberately mirrors trim_timeline.py's split between pure coordinate math
(_value_at_x) and Qt event plumbing, for the same reason: testable without
needing to construct real QMouseEvent objects.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRect, QSize, QPointF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from .resources import resource_qpixmap

ICON_MARGIN = 6      # gap between the speaker icon and the track
TRACK_HEIGHT = 22
MARKER_SIZE = QSize(20, 20)  # matches trim_timeline's marker size


class VolumeBar(QWidget):
    value_changed = Signal(int)  # 0-100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self._value = 100

        self._speaker_pixmap = resource_qpixmap("volume_speaker_icon.png")
        self._active_pixmap = resource_qpixmap("active_volume_gradient.png")
        self._inactive_pixmap = resource_qpixmap("inactive_volume_gradient.png")
        self._marker_pixmap = resource_qpixmap("bar_marker.png")

    # ------------------------------------------------------------ public API

    def set_value(self, value: int) -> None:
        self._value = max(0, min(100, value))
        self.update()

    @property
    def value(self) -> int:
        return self._value

    # ------------------------------------------------------------ pure coordinate math

    def _icon_size(self) -> int:
        return self.height()

    def _track_rect(self) -> QRect:
        icon_size = self._icon_size()
        left = icon_size + ICON_MARGIN
        width = max(self.width() - left, 1)
        top = (self.height() - TRACK_HEIGHT) // 2
        return QRect(left, top, width, TRACK_HEIGHT)

    def _value_at_x(self, x: float) -> int:
        track = self._track_rect()
        if track.width() <= 0:
            return self._value
        fraction = (x - track.left()) / track.width()
        return max(0, min(100, round(fraction * 100)))

    def _x_at_value(self, value: int) -> float:
        track = self._track_rect()
        return track.left() + (value / 100) * track.width()

    # ------------------------------------------------------------ Qt event wrappers

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._set_value_from_x(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            self._set_value_from_x(event.position().x())

    def _set_value_from_x(self, x: float) -> None:
        self.set_value(self._value_at_x(x))
        self.value_changed.emit(self._value)

    # ------------------------------------------------------------ rendering

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        icon_size = self._icon_size()
        icon_rect = QRect(0, 0, icon_size, icon_size).adjusted(4, 4, -4, -4)
        painter.drawPixmap(icon_rect, self._speaker_pixmap)

        track = self._track_rect()
        # Static full-width background -- "place the inactive gradient
        # behind the volume meter, unchanging."
        painter.drawPixmap(track, self._inactive_pixmap)

        # Active fill, resized to the current value's proportion of the
        # track -- same technique as the trim timeline's connector
        # gradient between its two handles.
        fill_width = int(track.width() * (self._value / 100))
        if fill_width > 0:
            fill_rect = QRect(track.left(), track.top(), fill_width, track.height())
            painter.drawPixmap(fill_rect, self._active_pixmap)

        # Marker at the current value's position.
        marker_x = self._x_at_value(self._value)
        marker_rect = QRect(0, 0, MARKER_SIZE.width(), MARKER_SIZE.height())
        marker_rect.moveCenter(QPointF(marker_x, self.height() / 2).toPoint())
        painter.drawPixmap(marker_rect, self._marker_pixmap)

        painter.end()
