"""
The previewer's trim bar: the SAME interaction model as TrimTimeline
(left-click/drag seeks, right-click grabs the nearest trim handle, same
signals, same coordinate math) drawn in the current UI style instead of
the old textured-image look.

Subclasses TrimTimeline rather than copying it, so drag/seek behavior
stays a single implementation -- only paintEvent (and the colors it
needs) differ. The Editor keeps using plain TrimTimeline untouched; it
is being rebuilt separately.

Look, all from Theme/appearance so it follows the palette settings:
- track: rounded pill in library_background() (darker than the
  card_background() box it sits in), so it reads as an inset groove
- selected range: accent() pill on top of the track
- handles: rounded grips in the card text color with a text-outline
  ring and two small grip lines, so they match on-card OutlinedLabels
- playhead: a round dot in the same text/outline colors, drawn above
  the track
Corner rounding follows the app's Rounded Corners setting (pill shapes
when on, small fixed radius when off, never fully square).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen

from .. import config as config_module
from .rounded_rect import rounded_rect_path
from .theme import Theme
from .trim_timeline import TrimTimeline

_BASE_TRACK_HEIGHT = 10
_BASE_HANDLE_WIDTH = 14
_BASE_PLAYHEAD_DIAMETER = 18
_BASE_HEIGHT = 40
_OFF_RADIUS = 3.0


class PreviewTrimBar(TrimTimeline):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scale = 1.0
        self._appearance = config_module.load_readonly().appearance
        self._theme = Theme(self._appearance)
        self.setCursor(Qt.PointingHandCursor)
        self.set_scale(1.0)

    def set_scale(self, factor: float) -> None:
        self._scale = factor
        self._handle_width = max(round(_BASE_HANDLE_WIDTH * factor), 6)
        self.setMinimumHeight(max(round(_BASE_HEIGHT * factor), 24))
        self.update()

    def _corner(self, height: float) -> float:
        if self._appearance.rounded_corners_enabled:
            return height / 2  # full pill; rounded_rect_path clamps anyway
        return min(_OFF_RADIUS, height / 2)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        theme = self._theme
        text_color = QColor(self._appearance.card_text_color)
        outline_color = QColor(self._appearance.card_text_outline_color)
        s = self._scale

        cy = self.height() / 2
        track_h = max(_BASE_TRACK_HEIGHT * s, 4)
        left = float(self._handle_width)
        width = float(self._usable_width())

        # Track
        track = QRectF(left, cy - track_h / 2, width, track_h)
        painter.fillPath(rounded_rect_path(track, self._corner(track_h)), theme.library_background())

        # Selected range
        start_x = self._time_to_x(self._start)
        end_x = self._time_to_x(self._end)
        if end_x - start_x > 0.5:
            sel = QRectF(start_x, cy - track_h / 2, end_x - start_x, track_h)
            painter.fillPath(rounded_rect_path(sel, self._corner(track_h)), theme.accent())

        # Handles: rounded grips centered on the boundary
        handle_h = self.height() - 6
        for x in (start_x, end_x):
            rect = QRectF(x - self._handle_width / 2, (self.height() - handle_h) / 2,
                          self._handle_width, handle_h)
            path = rounded_rect_path(rect, self._corner(self._handle_width) if
                                     self._appearance.rounded_corners_enabled else _OFF_RADIUS)
            painter.fillPath(path, text_color)
            painter.setPen(QPen(outline_color, max(1.5 * s, 1.0)))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
            # two grip lines
            painter.setPen(QPen(theme.accent().darker(150), max(1.5 * s, 1.0), Qt.SolidLine, Qt.RoundCap))
            gap = max(3 * s, 2.0)
            line_h = handle_h * 0.32
            for dx in (-gap / 2, gap / 2):
                painter.drawLine(QPointF(x + dx, cy - line_h / 2), QPointF(x + dx, cy + line_h / 2))

        # Playhead: a dot above everything, clamped inside the widget
        d = max(_BASE_PLAYHEAD_DIAMETER * s, 8)
        px = min(max(self._time_to_x(self._playhead), d / 2), self.width() - d / 2)
        painter.setPen(QPen(outline_color, max(2 * s, 1.0)))
        painter.setBrush(text_color)
        painter.drawEllipse(QPointF(px, cy), d / 2 - 1, d / 2 - 1)
        painter.end()
