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

BASE_HEIGHT = 16      # 40% of the original 40px
ICON_MARGIN = 6      # gap between the speaker icon and the track
BASE_TRACK_HEIGHT = 8      # even (not 9) so it centers exactly on an integer
                       # pixel -- see the marker-centering note below
BASE_MARKER_SIZE = QSize(15, 15)  # 1.25x the previous 12px
BASE_SPEAKER_ICON_SIZE = 16       # 2x the previous EFFECTIVE drawn size (was a
                              # 16px box shrunk by a 4px margin on each
                              # side down to 8px actually drawn)


def _round_to_even(x: float) -> int:
    return max(2 * round(x / 2), 2)


class VolumeBar(QWidget):
    value_changed = Signal(int)  # 0-100

    def __init__(self, parent=None):
        super().__init__(parent)
        self._track_height = BASE_TRACK_HEIGHT
        self._marker_size = BASE_MARKER_SIZE
        self._speaker_icon_size = BASE_SPEAKER_ICON_SIZE
        self.setFixedHeight(BASE_HEIGHT)
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

    def set_scale(self, factor: float) -> None:
        self.setFixedHeight(max(round(BASE_HEIGHT * factor), 8))
        # Stays even at every scale, not just at 1.0x -- an odd value
        # here is exactly what caused the marker-vs-track vertical
        # misalignment bug fixed earlier (see the centering note below).
        self._track_height = _round_to_even(BASE_TRACK_HEIGHT * factor)
        self._marker_size = QSize(
            max(round(BASE_MARKER_SIZE.width() * factor), 6),
            max(round(BASE_MARKER_SIZE.height() * factor), 6),
        )
        self._speaker_icon_size = max(round(BASE_SPEAKER_ICON_SIZE * factor), 8)
        self.update()

    # ------------------------------------------------------------ pure coordinate math

    def _icon_size(self) -> int:
        return self._speaker_icon_size

    def _track_rect(self) -> QRect:
        icon_size = self._icon_size()
        left = icon_size + ICON_MARGIN
        width = max(self.width() - left, 1)
        # self._track_height is kept even specifically so this divides
        # with no remainder -- see the marker vertical-centering note in
        # paintEvent below for why an odd track height was the actual
        # cause of the marker looking off-center.
        top = (self.height() - self._track_height) // 2
        return QRect(left, top, width, self._track_height)

    def _value_at_x(self, x: float) -> int:
        track = self._track_rect()
        half_marker = self._marker_size.width() / 2
        usable_left = track.left() + half_marker
        usable_right = track.left() + track.width() - half_marker
        if usable_right <= usable_left:
            return self._value
        fraction = (x - usable_left) / (usable_right - usable_left)
        return max(0, min(100, round(fraction * 100)))

    def _x_at_value(self, value: int) -> float:
        # The marker's CENTER travel range is inset by half its own width
        # on each side, so the marker's full rect always stays within the
        # track -- without this, at value=100 the center would land at
        # the track's far edge and roughly half the marker would extend
        # past the widget's right boundary, which Qt simply doesn't draw
        # (this was the "marker gets cut off / doesn't render at all at
        # the end" bug -- clipped geometry, not a missing-render issue).
        track = self._track_rect()
        half_marker = self._marker_size.width() / 2
        usable_left = track.left() + half_marker
        usable_right = track.left() + track.width() - half_marker
        if usable_right <= usable_left:
            return track.left() + track.width() / 2
        return usable_left + (value / 100) * (usable_right - usable_left)

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
        # No shrinking margin here (unlike before) -- self._speaker_icon_size
        # already IS the target drawn size, doubled from the old
        # icon_size-minus-4px-margin-on-each-side result.
        icon_rect = QRect(0, (self.height() - icon_size) // 2, icon_size, icon_size)
        painter.drawPixmap(icon_rect, self._speaker_pixmap)

        track = self._track_rect()

        # Active fill (0 to the current value) and the inactive remainder
        # (value to 100) each get the FULL gradient image resized to fit
        # their own span -- not the whole gradient stretched across the
        # entire track with one part painted over. The earlier version
        # did the latter for the inactive side, which meant whatever was
        # visible beyond the active fill was only a cropped SLICE of the
        # inactive gradient (e.g. just its rightmost 30% of colors at
        # value=70), rather than the inactive gradient's full color range
        # always being visible within however much space it actually has.
        fill_width = int(track.width() * (self._value / 100))
        if fill_width > 0:
            fill_rect = QRect(track.left(), track.top(), fill_width, track.height())
            painter.drawPixmap(fill_rect, self._active_pixmap)
        if fill_width < track.width():
            remainder_rect = QRect(
                track.left() + fill_width, track.top(),
                track.width() - fill_width, track.height(),
            )
            painter.drawPixmap(remainder_rect, self._inactive_pixmap)

        # Marker at the current value's position. Vertically centered on
        # self.height()/2 exactly -- the marker looking "slightly below"
        # center was actually track_height being odd (9): (16-9)//2 == 3
        # (integer division truncates down), so the track's own visual
        # center sat at 3+9/2=7.5 while the marker centered on the
        # widget's true center of 8.0 -- correct on its own, but visibly
        # lower than the track it sits on. track_height is kept even at
        # every scale, so its center always lands on the same point too.
        marker_x = self._x_at_value(self._value)
        marker_rect = QRect(0, 0, self._marker_size.width(), self._marker_size.height())
        marker_rect.moveCenter(QPointF(marker_x, self.height() / 2).toPoint())
        painter.drawPixmap(marker_rect, self._marker_pixmap)

        painter.end()
