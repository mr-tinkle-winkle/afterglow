"""
A larger, playable preview of a Library video -- title, filters, and
info alongside a real embedded player (play/pause, volume, a seek
scrubber, fullscreen, watch speed), opened via double-click on a card's
thumbnail. Distinct from the Editor: this is read-only playback, no
trimming -- reuses MpvVideoWidget (the same embedding editor_page.py
uses) directly rather than re-solving mpv embedding here.

Play/pause, volume, and fullscreen are custom-painted (no external
icon asset needed for any of them -- a triangle/two bars, a speaker
cone + sound-wave arcs, and four corner brackets are all simple enough
vector shapes to draw directly); the scrubber is a real QSlider
(dragging, click-to-seek, and keyboard step all come for free from
that) recolored via stylesheet to match the theme rather than rebuilt
from scratch. All three custom-painted controls can be swapped for a
provided icon image later without changing anything else -- see each
class's own paintEvent.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPainterPath
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QDoubleSpinBox,
    QAbstractButton, QSizePolicy,
)

from .. import config as config_module
from .. import library
from .theme import Theme, contrast_text
from .outlined_label import OutlinedLabel
from .custom_button import CustomButton
from .mpv_widget import MpvVideoWidget
from .video_card import _format_duration, _format_file_size, _format_date, FAVORITE_STAR

_TRANSPORT_BTN_SIZE = 40


class _PlayPauseButton(QAbstractButton):
    """Checked = currently playing (draws two pause bars); unchecked =
    paused (draws a right-pointing play triangle)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(_TRANSPORT_BTN_SIZE, _TRANSPORT_BTN_SIZE)
        appearance = config_module.load().appearance
        self._theme = Theme(appearance)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        bg = self._theme.accent()
        if self.underMouse():
            bg = bg.lighter(115)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(rect)

        fg = contrast_text(bg)
        painter.setBrush(fg)
        cx, cy, r = rect.center().x(), rect.center().y(), rect.width() * 0.28
        if self.isChecked():  # playing -> pause bars
            bar_w = r * 0.5
            painter.drawRect(QRectF(cx - r, cy - r, bar_w, 2 * r))
            painter.drawRect(QRectF(cx + r - bar_w, cy - r, bar_w, 2 * r))
        else:  # paused -> play triangle
            path = QPainterPath()
            path.moveTo(cx - r * 0.7, cy - r)
            path.lineTo(cx - r * 0.7, cy + r)
            path.lineTo(cx + r, cy)
            path.closeSubpath()
            painter.drawPath(path)
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class _FullscreenButton(QAbstractButton):
    """Four corner brackets pointing outward (enter fullscreen) or
    inward (exit fullscreen), depending on `checked`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(_TRANSPORT_BTN_SIZE, _TRANSPORT_BTN_SIZE)
        appearance = config_module.load().appearance
        self._theme = Theme(appearance)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        bg = self._theme.accent()
        if self.underMouse():
            bg = bg.lighter(115)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(rect)

        pen = painter.pen()
        pen.setColor(contrast_text(bg))
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)

        margin = rect.width() * 0.26
        arm = rect.width() * 0.16
        inward = self.isChecked()  # exiting fullscreen -> brackets point inward
        corners = [
            (rect.left() + margin, rect.top() + margin, 1, 1),
            (rect.right() - margin, rect.top() + margin, -1, 1),
            (rect.left() + margin, rect.bottom() - margin, 1, -1),
            (rect.right() - margin, rect.bottom() - margin, -1, -1),
        ]
        for x, y, sx, sy in corners:
            dx, dy = (-sx, -sy) if inward else (sx, sy)
            painter.drawLine(int(x), int(y), int(x + dx * arm), int(y))
            painter.drawLine(int(x), int(y), int(x), int(y + dy * arm))
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class _VolumeButton(QAbstractButton):
    """A speaker cone + up to two sound-wave arcs. Click toggles mute
    (handled by the dialog, not this button itself)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(28, 28)
        self._muted = False

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(config_module.load().appearance.card_text_color)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)

        w, h = self.width(), self.height()
        cone = QPainterPath()
        cone.moveTo(w * 0.15, h * 0.38)
        cone.lineTo(w * 0.4, h * 0.38)
        cone.lineTo(w * 0.65, h * 0.15)
        cone.lineTo(w * 0.65, h * 0.85)
        cone.lineTo(w * 0.4, h * 0.62)
        cone.lineTo(w * 0.15, h * 0.62)
        cone.closeSubpath()
        painter.drawPath(cone)

        pen = painter.pen()
        pen.setColor(color)
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        if self._muted:
            painter.drawLine(int(w * 0.7), int(h * 0.3), int(w * 0.9), int(h * 0.7))
            painter.drawLine(int(w * 0.9), int(h * 0.3), int(w * 0.7), int(h * 0.7))
        else:
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(int(w * 0.68), int(h * 0.28), int(w * 0.2), int(h * 0.44), -60 * 16, 120 * 16)
            painter.drawArc(int(w * 0.78), int(h * 0.16), int(w * 0.22), int(h * 0.68), -55 * 16, 110 * 16)
        painter.end()


class VideoPreviewDialog(QDialog):
    def __init__(self, video: "library.Video", parent=None):
        super().__init__(parent)
        appearance = config_module.load().appearance
        self._theme = Theme(appearance)
        self._video = video
        self._duration = 0.0
        self._current_pos = 0.0
        self._seeking = False
        self._pre_mute_volume = 80

        self.setWindowTitle(video.title or "Preview")
        self.resize(1100, 720)
        self.setStyleSheet(f"QDialog {{ background-color: {self._theme.library_background().name()}; }}")

        outer = QVBoxLayout(self)

        # ---- title + close ----
        header = QHBoxLayout()
        display_title = f"{FAVORITE_STAR} {video.title}" if video.favorite else (video.title or "(untitled)")
        title_label = OutlinedLabel(display_title)
        title_label.setStyleSheet("font-size: 20px;")
        title_label.set_colors(appearance.card_text_color, appearance.card_text_outline_color,
                                outline_width=appearance.card_text_outline_width)
        header.addWidget(title_label, stretch=1)
        close_btn = CustomButton("Close")
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn)
        outer.addLayout(header)

        # ---- filters + info line ----
        info_row = QHBoxLayout()
        if video.tags:
            tags_label = OutlinedLabel(", ".join(video.tags))
            tags_label.setStyleSheet("font-size: 12px;")
            tags_label.set_colors(appearance.card_text_color, appearance.card_text_outline_color,
                                   outline_width=appearance.card_text_outline_width * 0.5)
            info_row.addWidget(tags_label)
        info_row.addStretch(1)
        meta_parts = [p for p in (
            _format_duration(video.duration_sec), _format_file_size(video.path), _format_date(video.created_at),
        ) if p]
        if meta_parts:
            meta_label = OutlinedLabel(" \u2022 ".join(meta_parts))
            meta_label.setStyleSheet("font-size: 12px;")
            meta_label.set_colors(appearance.card_text_color, appearance.card_text_outline_color,
                                   outline_width=appearance.card_text_outline_width * 0.5)
            info_row.addWidget(meta_label)
        outer.addLayout(info_row)

        # ---- video (significantly larger than a library card) ----
        self.video_widget = MpvVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.clicked.connect(self._toggle_play_pause)
        self.video_widget.position_changed.connect(self._on_position_changed)
        self.video_widget.duration_known.connect(self._on_duration_known)
        self.video_widget.playback_ended.connect(self._on_playback_ended)
        outer.addWidget(self.video_widget, stretch=1)

        # ---- transport ----
        transport = QHBoxLayout()

        self.play_pause_btn = _PlayPauseButton()
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        transport.addWidget(self.play_pause_btn)

        self.time_label = QLabel("0:00 / 0:00")
        transport.addWidget(self.time_label)

        self.scrubber = QSlider(Qt.Horizontal)
        self.scrubber.setRange(0, 1000)
        self.scrubber.sliderPressed.connect(self._on_scrub_start)
        self.scrubber.sliderMoved.connect(self._on_scrub_moved)
        self.scrubber.sliderReleased.connect(self._on_scrub_end)
        self.scrubber.setStyleSheet(
            "QSlider::groove:horizontal { background: " + self._theme.card_background().name() + "; height: 6px; border-radius: 3px; }"
            "QSlider::handle:horizontal { background: " + self._theme.accent().name() + "; width: 14px; margin: -5px 0; border-radius: 7px; }"
            "QSlider::sub-page:horizontal { background: " + self._theme.accent().name() + "; border-radius: 3px; }"
        )
        transport.addWidget(self.scrubber, stretch=1)

        self.volume_btn = _VolumeButton()
        self.volume_btn.clicked.connect(self._toggle_mute)
        transport.addWidget(self.volume_btn)

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.setStyleSheet(self.scrubber.styleSheet())
        transport.addWidget(self.volume_slider)

        transport.addWidget(QLabel("Speed:"))
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.05, 4.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setSuffix("x")
        self.speed_spin.setToolTip("Preview-only playback speed")
        self.speed_spin.valueChanged.connect(self.video_widget.set_speed)
        transport.addWidget(self.speed_spin)

        self.fullscreen_btn = _FullscreenButton()
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        transport.addWidget(self.fullscreen_btn)

        outer.addLayout(transport)

        self.video_widget.load(video.path)
        self.video_widget.set_volume(80)
        self.play_pause_btn.setChecked(True)

    # ------------------------------------------------------------ playback

    def _toggle_play_pause(self) -> None:
        if self.video_widget.is_paused:
            self.video_widget.play()
        else:
            self.video_widget.pause()
        self.play_pause_btn.setChecked(not self.video_widget.is_paused)

    def _on_position_changed(self, position: float) -> None:
        self._current_pos = position
        if not self._seeking and self._duration > 0:
            self.scrubber.blockSignals(True)
            self.scrubber.setValue(round(position / self._duration * 1000))
            self.scrubber.blockSignals(False)
        self._update_time_label()

    def _on_duration_known(self, duration: float) -> None:
        self._duration = duration
        self._update_time_label()

    def _on_playback_ended(self) -> None:
        self.play_pause_btn.setChecked(False)

    def _update_time_label(self) -> None:
        current = _format_duration(self._current_pos) or "0:00"
        total = _format_duration(self._duration) or "0:00"
        self.time_label.setText(f"{current} / {total}")

    # ------------------------------------------------------------ scrubber

    def _on_scrub_start(self) -> None:
        self._seeking = True

    def _on_scrub_moved(self, value: int) -> None:
        if self._duration > 0:
            self._current_pos = value / 1000 * self._duration
            self._update_time_label()

    def _on_scrub_end(self) -> None:
        if self._duration > 0:
            self.video_widget.seek(self.scrubber.value() / 1000 * self._duration)
        self._seeking = False

    # ------------------------------------------------------------ volume

    def _on_volume_changed(self, value: int) -> None:
        self.video_widget.set_volume(value)
        self.volume_btn.set_muted(value == 0)

    def _toggle_mute(self) -> None:
        if self.volume_slider.value() > 0:
            self._pre_mute_volume = self.volume_slider.value()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(self._pre_mute_volume or 80)

    # ------------------------------------------------------------ fullscreen / lifecycle

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.fullscreen_btn.setChecked(False)
        else:
            self.showFullScreen()
            self.fullscreen_btn.setChecked(True)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            if self.isFullScreen():
                self._toggle_fullscreen()
            else:
                self.close()
        elif event.key() == Qt.Key_Space:
            self._toggle_play_pause()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        self.video_widget.shutdown()
        super().closeEvent(event)
