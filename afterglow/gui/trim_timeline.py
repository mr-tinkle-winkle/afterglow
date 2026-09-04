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

from PySide6.QtCore import Qt, Signal, QPointF, QRect
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget

from .resources import resource_qpixmap

HANDLE_WIDTH = 8
MIN_GAP_SEC = 0.05  # smallest allowed distance between start and end handles


class TrimTimeline(QWidget):
    range_changed = Signal(float, float)   # start, end -- emitted live while dragging a handle
    seek_requested = Signal(float)         # emitted while dragging, so the preview follows the handle
    drag_started = Signal()                # emitted on press, before any movement -- callers can pause playback here
    drag_finished = Signal(float, float)   # start, end -- emitted on release, once the range is committed

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(48)
        self._duration = 0.0
        self._start = 0.0
        self._end = 0.0
        self._playhead = 0.0
        self._dragging: str | None = None  # None | "start" | "end"

        # Loaded once, then stretched to fit at paint time -- the handle
        # is a scroll handle, not a repeating pattern, so it's stretched
        # to the handle's own proportions rather than tiled. Same for the
        # connector gradient: one image, resized to whatever width the
        # current start/end gap happens to be on each repaint.
        self._handle_pixmap = resource_qpixmap("handle_texture.png")
        self._gradient_pixmap = resource_qpixmap("handle_connector_gradient.png")

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
        if was_dragging:
            self.drag_finished.emit(self._start, self._end)

    # ------------------------------------------------------------ Qt event wrappers

    def mousePressEvent(self, event) -> None:
        self._press_at(event.position().x())

    def mouseMoveEvent(self, event) -> None:
        if self._dragging is not None:
            self._drag_to(self._x_to_time(event.position().x()))

    def mouseReleaseEvent(self, event) -> None:
        self._release()

    # ------------------------------------------------------------ rendering

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

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

        # Playhead
        playhead_x = self._time_to_x(self._playhead)
        painter.setPen(QPen(QColor("#ffffff"), 2))
        painter.drawLine(QPointF(playhead_x, 4), QPointF(playhead_x, self.height() - 4))

        # Handles -- the texture image stretched to the handle's own
        # rect (not tiled; it's a scroll handle, not a repeating pattern).
        for x in (start_x, end_x):
            handle_rect = QRect(int(x - HANDLE_WIDTH / 2), 2, HANDLE_WIDTH, self.height() - 4)
            painter.drawPixmap(handle_rect, self._handle_pixmap)

        painter.end()
