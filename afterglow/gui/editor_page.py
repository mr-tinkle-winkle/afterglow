"""
The Editor page. Per your requirement: it must remember whatever video was
last being edited even after navigating away to Settings/Library and back.

That persistence falls out naturally from how MainWindow is structured --
each page is constructed exactly once and kept alive inside the
QStackedWidget for the lifetime of the app (switching pages just changes
which widget is visible, it never destroys/recreates them). So
EditorPage's `self.current_video_id` simply stays whatever it was, with
no extra save/restore logic needed. This is why load_video() below is the
ONLY place state changes -- there's no "on page shown, reload state" path,
because there's nothing to reload from; it never went away.

This delivery adds actual video playback (play/pause, seek slider, time
display) via MpvVideoWidget -- previously this page only showed a static
thumbnail. The Medal-style trim UI (drag handles for start/end, live
scrub-while-dragging, the audio graph editor) is still the next build
phase; this is "you can now watch the video," not yet "you can now trim
it visually."
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QSlider,
)

from .. import library
from .mpv_widget import MpvVideoWidget


def _format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes}:{secs:02d}"


class EditorPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_video_id: int | None = None
        self._duration: float = 0.0
        self._slider_being_dragged = False

        layout = QVBoxLayout(self)

        self.video_widget = MpvVideoWidget()
        layout.addWidget(self.video_widget, stretch=1)
        self.video_widget.position_changed.connect(self._on_position_changed)
        self.video_widget.duration_known.connect(self._on_duration_known)
        self.video_widget.playback_ended.connect(self._on_playback_ended)

        # ---- transport controls ----
        transport_row = QHBoxLayout()
        self.play_pause_btn = QPushButton("Play")
        self.play_pause_btn.setEnabled(False)
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        transport_row.addWidget(self.play_pause_btn)

        self.time_label = QLabel("0:00 / 0:00")
        transport_row.addWidget(self.time_label)

        self.seek_slider = QSlider(Qt.Horizontal)
        self.seek_slider.setEnabled(False)
        self.seek_slider.setRange(0, 1000)  # fixed resolution; mapped to actual duration in _on_slider_*
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "_slider_being_dragged", True))
        self.seek_slider.sliderReleased.connect(self._on_slider_released)
        transport_row.addWidget(self.seek_slider, stretch=1)
        layout.addLayout(transport_row)

        self.info_label = QLabel("No video selected. Double-click a clip in the Library, "
                                  "or right-click it and choose Edit.")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        coming_soon_label = QLabel(
            "Trim handles, live scrub-while-dragging, and the audio graph "
            "editor are coming in the next build phase."
        )
        coming_soon_label.setStyleSheet("color: gray;")
        coming_soon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(coming_soon_label)

        button_row = QHBoxLayout()
        self.undo_btn = QPushButton("Undo Last Edit")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo)
        button_row.addWidget(self.undo_btn)
        layout.addLayout(button_row)

    # ------------------------------------------------------------ loading

    def load_video(self, video_id: int) -> None:
        self.current_video_id = video_id
        self._duration = 0.0
        self._refresh_display()

    def _refresh_display(self) -> None:
        if self.current_video_id is None:
            self.info_label.setText(
                "No video selected. Double-click a clip in the Library, "
                "or right-click it and choose Edit."
            )
            self.undo_btn.setEnabled(False)
            self.play_pause_btn.setEnabled(False)
            self.seek_slider.setEnabled(False)
            return

        video = library.get_video(self.current_video_id)
        self.video_widget.load(video.path)
        self.play_pause_btn.setEnabled(True)
        self.play_pause_btn.setText("Play")
        self.seek_slider.setEnabled(True)
        self.seek_slider.setValue(0)

        self.info_label.setText(
            f"<b>{video.title}</b>"
            f"{' &middot; ' + ', '.join(video.tags) if video.tags else ''}"
        )
        self.undo_btn.setEnabled(video.has_edit)

    # ------------------------------------------------------------ transport

    def _toggle_play_pause(self) -> None:
        if self.video_widget.is_paused:
            self.video_widget.play()
            self.play_pause_btn.setText("Pause")
        else:
            self.video_widget.pause()
            self.play_pause_btn.setText("Play")

    def _on_position_changed(self, position: float) -> None:
        if not self._slider_being_dragged and self._duration > 0:
            slider_value = int((position / self._duration) * self.seek_slider.maximum())
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(slider_value)
            self.seek_slider.blockSignals(False)
        self.time_label.setText(f"{_format_time(position)} / {_format_time(self._duration)}")

    def _on_duration_known(self, duration: float) -> None:
        self._duration = duration
        self.time_label.setText(f"0:00 / {_format_time(duration)}")

    def _on_slider_released(self) -> None:
        self._slider_being_dragged = False
        if self._duration > 0:
            fraction = self.seek_slider.value() / self.seek_slider.maximum()
            self.video_widget.seek(fraction * self._duration)

    def _on_playback_ended(self) -> None:
        self.play_pause_btn.setText("Play")

    # ------------------------------------------------------------ undo

    def _undo(self) -> None:
        if self.current_video_id is not None:
            library.undo_edit(self.current_video_id)
            self._refresh_display()
