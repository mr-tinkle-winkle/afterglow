"""
The trim timeline: a horizontal bar spanning the full video duration, with
two draggable handles marking the selected start/end range, plus a
playhead marker showing current playback position.

Split deliberately into pure, testable pieces vs. Qt event plumbing:
- _time_to_x / _x_to_time: pure coordinate math, no widget state needed
  beyond width/duration.
- _drag_to: the actual state update for a drag, independent of how the
  drag was initiated (real mouse event or a test calling it directly).
mousePressEvent/mouseMoveEvent/mouseReleaseEvent are thin wrappers around
these, so the logic itself is testable without needing to fight
constructing real QMouseEvent objects or simulate an actual display.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPointF, QRect, QSize
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget

from .resources import resource_qpixmap

# Widened from the original 8px -- handle_texture.png is ~91% transparent
# (a thin grip-line pattern on a mostly-clear background), and an 8px-wide
# handle left almost nothing of it visible. Widening alone wasn't the
# actual fix though (see the handle-drawing code below) -- it's paired
# with a solid base fill so there's still a clearly visible handle body
# even where the texture itself is transparent.
HANDLE_WIDTH = 16
MIN_GAP_SEC = 0.05  # smallest allowed distance between start and end handles

# The playhead/volume marker asset (bar_marker.png) is a roughly square
# badge/ring graphic, not an elongated bar -- so unlike the handle
# texture, it's drawn at a fixed icon size centered on its position
# rather than stretched to fill a tall thin rect (which would smear a
# round badge into an unrecognizable vertical streak).
MARKER_SIZE = QSize(20, 20)


class TrimTimeline(QWidget):
    range_changed = Signal(float, float)   # start, end -- emitted live while dragging a handle
    seek_requested = Signal(float)         # emitted for both live handle-drag preview AND a plain left-click/drag seek
    drag_started = Signal()                # emitted on a handle grab (right-click), before any movement -- callers can pause playback here
    drag_finished = Signal(float, float)   # start, end -- emitted when a handle drag ends, once the range is committed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(48)
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._playhead = 0.0
        self._dragging: str | None = None  # None | "start" | "end" -- a right-click handle grab
        self._left_seeking = False         # a left-click/drag plain seek, independent of handle dragging

        # Loaded once, then drawn at paint time. The connector gradient is
        # resized to whatever width the current start/end gap is on each
        # repaint; the handle texture is stretched to the handle's own
        # rect (not tiled -- it's a scroll handle, not a repeating pattern).
        self._handle_pixmap = resource_qpixmap("handle_texture.png")
        self._gradient_pixmap = resource_qpixmap("handle_connector_gradient.png")
        self._marker_pixmap = resource_qpixmap("bar_marker.png")

    # ------------------------------------------------------------ public API

    def set_duration(self, duration: float) -> None:
        self._duration = max(duration, 0.001)
        self._start = 0.0
        self._end = self._duration
        self.update()

    def set_range(self, start: float, end: float) -> None:
        self._start = max(0.0, min(start, self._duration))
        self._end = max(0.0, min(end, self._duration))
        self.update()

    def set_playhead(self, position: float) -> None:
        self._playhead = position
        self.update()

    @property
    def start(self) -> float:
        return self._start

    @property
    def end(self) -> float:
        return self._end

    # ------------------------------------------------------------ pure coordinate math

    def _usable_width(self) -> float:
        return max(self.width() - 2 * HANDLE_WIDTH, 1)

    def _time_to_x(self, t: float) -> float:
        if self._duration <= 0:
            return float(HANDLE_WIDTH)
        return HANDLE_WIDTH + (t / self._duration) * self._usable_width()

    def _x_to_time(self, x: float) -> float:
        if self._duration <= 0:
            return 0.0
        t = (x - HANDLE_WIDTH) / self._usable_width() * self._duration
        return min(max(t, 0.0), self._duration)

    # ------------------------------------------------------------ drag state (testable directly)

    def _press_at(self, x: float) -> None:
        """A right-click (or a direct call, for tests): grabs the nearer
        handle if the click is close to one, otherwise jumps whichever
        handle is on the same side of the playhead as the click and
        starts dragging it from there. Left-click is handled separately
        by _seek_at below -- it never touches the trim range at all."""
        start_x = self._time_to_x(self._start)
        end_x = self._time_to_x(self._end)
        if abs(x - start_x) <= HANDLE_WIDTH * 1.5:
            self._dragging = "start"
            self.drag_started.emit()
        elif abs(x - end_x) <= HANDLE_WIDTH * 1.5:
            self._dragging = "end"
            self.drag_started.emit()
        else:
            # Clicked on the bar itself, not on a handle. Move whichever
            # handle is on the same side of the current playback marker
            # as the click -- left of the playhead always moves the
            # start handle, right of it always moves the end handle --
            # and start dragging it from there immediately, rather than
            # requiring the handle to be grabbed precisely first.
            playhead_x = self._time_to_x(self._playhead)
            self._dragging = "start" if x < playhead_x else "end"
            self.drag_started.emit()
            self._drag_to(self._x_to_time(x))

    def _drag_to(self, t: float) -> None:
        if self._dragging is None:
            return
        if self._dragging == "start":
            self._start = max(0.0, min(t, self._end - MIN_GAP_SEC))
        elif self._dragging == "end":
            self._end = min(self._duration, max(t, self._start + MIN_GAP_SEC))
        self.update()
        self.range_changed.emit(self._start, self._end)
        self.seek_requested.emit(t)

    def _release(self) -> None:
        was_dragging = self._dragging is not None
        self._dragging = None
        self._left_seeking = False
        if was_dragging:
            self.drag_finished.emit(self._start, self._end)

    def _seek_at(self, x: float) -> None:
        """A left-click (or drag): just moves playback to that point on
        the timeline. Never grabs or moves a handle, regardless of how
        close to one the click lands."""
        self._left_seeking = True
        self.seek_requested.emit(self._x_to_time(x))

    # ------------------------------------------------------------ Qt event wrappers

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.RightButton:
            self._press_at(event.position().x())
        elif event.button() == Qt.LeftButton:
            self._seek_at(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self._dragging is not None:
            self._drag_to(self._x_to_time(event.position().x()))
        elif self._left_seeking:
            self.seek_requested.emit(self._x_to_time(event.position().x()))

    def mouseReleaseEvent(self, event) -> None:
        self._release()

    # ------------------------------------------------------------ rendering

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        # Without this, QPainter scales pixmaps with fast/nearest-neighbor
        # sampling -- for the handle texture specifically (~91%
        # transparent, thin detail lines) that was landing almost
        # entirely on transparent source pixels and contributed to it
        # reading as invisible. Smooth sampling alone wasn't the whole
        # fix (see the solid base fill below), but it does make the
        # texture's own detail actually survive being scaled.
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        bar_y = self.height() // 2 - 4
        bar_height = 8
        bar_rect_left = HANDLE_WIDTH
        bar_rect_width = self._usable_width()

        # Full-duration background track
        painter.fillRect(bar_rect_left, bar_y, int(bar_rect_width), bar_height, QColor("#3a3a3a"))

        # Selected range highlight -- the connector gradient image,
        # stretched to whatever width the current start/end gap is.
        start_x = self._time_to_x(self._start)
        end_x = self._time_to_x(self._end)
        selection_rect = QRect(int(start_x), bar_y, max(int(end_x - start_x), 0), bar_height)
        if selection_rect.width() > 0:
            painter.drawPixmap(selection_rect, self._gradient_pixmap)

        # Playhead -- a fixed-size marker icon centered on the position,
        # rather than a full-height line (see MARKER_SIZE's comment).
        playhead_x = self._time_to_x(self._playhead)
        marker_rect = QRect(0, 0, MARKER_SIZE.width(), MARKER_SIZE.height())
        marker_rect.moveCenter(QPointF(playhead_x, self.height() / 2).toPoint())
        painter.drawPixmap(marker_rect, self._marker_pixmap)

        # Handles -- a solid base (this IS the visible handle shape/body)
        # with the texture image stretched on top of it for decoration.
        # The texture alone is almost entirely transparent, so without
        # this base fill there was no visible handle at all -- just a
        # faint scatter of texture detail with nothing solid behind it.
        for x in (start_x, end_x):
            handle_rect = QRect(int(x - HANDLE_WIDTH / 2), 2, HANDLE_WIDTH, self.height() - 4)
            painter.setPen(QPen(QColor("#000000"), 1))
            painter.setBrush(QColor("#e0e0e0"))
            painter.drawRect(handle_rect)
            painter.drawPixmap(handle_rect, self._handle_pixmap)

        painter.end()
