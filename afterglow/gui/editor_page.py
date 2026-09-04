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

This delivery adds the actual trim UI on top of the video playback built
previously: drag handles on a timeline set the start/end range, dragging
a handle live-seeks the preview to that point (Medal-style "see where the
cut lands"), a Frame Perfect Accuracy checkbox controls trim precision
vs. speed, and Local Save commits the trim via library.apply_trim().
Save & Upload is wired up but not functional yet -- YouTube upload isn't
built. The audio graph editor (per-segment volume/mute/trim/reposition)
is still a separate, later phase.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QSlider,
    QCheckBox, QMessageBox,
)

from .. import library
from .mpv_widget import MpvVideoWidget
from .trim_timeline import TrimTimeline


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
        # Live playback preview is bounded to the current UNSAVED trim
        # selection -- Play always starts from the trim start, and
        # playback auto-pauses at the trim end, so scrubbing through a
        # long capture to find your cut points doesn't mean sitting
        # through everything after them too. Deliberately separate from
        # trim_timeline.start/end: those update continuously while a
        # handle is being dragged (for the live label), but the preview
        # bounds below only update once the handle is released, so the
        # clamp point doesn't jitter mid-drag.
        self._preview_start: float = 0.0
        self._preview_end: float = 0.0

        layout = QVBoxLayout(self)

        # ---- video + volume slider row ----
        video_row = QHBoxLayout()
        self.volume_slider = QSlider(Qt.Vertical)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setToolTip("Playback volume (this doesn't affect the saved video)")
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        video_row.addWidget(self.volume_slider)

        self.video_widget = MpvVideoWidget()
        video_row.addWidget(self.video_widget, stretch=1)
        layout.addLayout(video_row, stretch=1)

        self.video_widget.position_changed.connect(self._on_position_changed)
        self.video_widget.duration_known.connect(self._on_duration_known)
        self.video_widget.playback_ended.connect(self._on_playback_ended)
        self.video_widget.set_volume(self.volume_slider.value())

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

        # ---- trim timeline ----
        self.trim_timeline = TrimTimeline()
        self.trim_timeline.setEnabled(False)
        self.trim_timeline.range_changed.connect(self._on_trim_range_changed)
        self.trim_timeline.seek_requested.connect(self._on_trim_seek_requested)
        self.trim_timeline.drag_started.connect(self._on_trim_drag_started)
        self.trim_timeline.drag_finished.connect(self._on_trim_drag_finished)
        layout.addWidget(self.trim_timeline)

        self.trim_range_label = QLabel("Start: 0:00   End: 0:00   Selected: 0:00")
        self.trim_range_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.trim_range_label)

        self.info_label = QLabel("No video selected. Double-click a clip in the Library, "
                                  "or right-click it and choose Edit.")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        coming_soon_label = QLabel(
            "The audio graph editor (per-segment volume/mute/trim/reposition) "
            "is coming in a later build phase."
        )
        coming_soon_label.setStyleSheet("color: gray;")
        coming_soon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(coming_soon_label)

        # ---- save controls ----
        save_row = QHBoxLayout()
        self.frame_perfect_checkbox = QCheckBox("Frame Perfect Accuracy")
        self.frame_perfect_checkbox.setToolTip(
            "Exact frame-accurate trim (slower, full re-encode). Off uses a "
            "fast keyframe-based trim -- fine for most clips, but the actual "
            "cut point may land on the nearest keyframe rather than exactly "
            "where you dragged the handle."
        )
        save_row.addWidget(self.frame_perfect_checkbox)
        save_row.addStretch(1)

        self.local_save_btn = QPushButton("Local Save")
        self.local_save_btn.setEnabled(False)
        self.local_save_btn.clicked.connect(self._local_save)
        save_row.addWidget(self.local_save_btn)

        self.save_upload_btn = QPushButton("Save && Upload")
        self.save_upload_btn.setEnabled(False)
        self.save_upload_btn.clicked.connect(self._save_and_upload)
        save_row.addWidget(self.save_upload_btn)
        layout.addLayout(save_row)

        button_row = QHBoxLayout()
        self.undo_btn = QPushButton("Undo Edits")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo)
        button_row.addWidget(self.undo_btn)

        self.clear_backup_btn = QPushButton("Clear Edit Backup")
        self.clear_backup_btn.setToolTip(
            "Delete this video's edit backup to free up space. The edit "
            "itself stays applied -- you just give up the ability to Undo."
        )
        self.clear_backup_btn.setEnabled(False)
        self.clear_backup_btn.clicked.connect(self._clear_edit_backup)
        button_row.addWidget(self.clear_backup_btn)
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
            self.clear_backup_btn.setEnabled(False)
            self.play_pause_btn.setEnabled(False)
            self.seek_slider.setEnabled(False)
            self.trim_timeline.setEnabled(False)
            self.local_save_btn.setEnabled(False)
            self.save_upload_btn.setEnabled(False)
            return

        video = library.get_video(self.current_video_id)
        self.video_widget.load(video.path)
        self.play_pause_btn.setEnabled(True)
        self.play_pause_btn.setText("Play")
        self.seek_slider.setEnabled(True)
        self.seek_slider.setValue(0)
        self.trim_timeline.setEnabled(True)
        self.local_save_btn.setEnabled(True)
        self.save_upload_btn.setEnabled(True)

        self.info_label.setText(
            f"<b>{video.title}</b>"
            f"{' &middot; ' + ', '.join(video.tags) if video.tags else ''}"
        )
        # Both require an actual backup to act on -- once Clear Edit Backup
        # (or a prior Undo) has removed it, has_edit can still be true
        # (the trim is still applied) but there's nothing left to undo or
        # clear.
        can_undo = video.has_edit and video.backup_path is not None
        self.undo_btn.setEnabled(can_undo)
        self.clear_backup_btn.setEnabled(can_undo)

    # ------------------------------------------------------------ transport

    def _toggle_play_pause(self) -> None:
        if self.video_widget.is_paused:
            # Starting playback from outside (or at the tail of) the
            # current trim selection jumps to the trim start first,
            # rather than resuming from wherever the playhead happened
            # to be left -- otherwise "Play" after scrubbing past the end
            # bound would just immediately hit it again and stop.
            position = self._current_position()
            if position < self._preview_start or position >= self._preview_end:
                self.video_widget.seek(self._preview_start)
            self.video_widget.play()
            self.play_pause_btn.setText("Pause")
        else:
            self.video_widget.pause()
            self.play_pause_btn.setText("Play")

    def _on_volume_changed(self, value: int) -> None:
        self.video_widget.set_volume(value)

    def _current_position(self) -> float:
        if self._duration <= 0:
            return 0.0
        return (self.seek_slider.value() / self.seek_slider.maximum()) * self._duration

    def _on_position_changed(self, position: float) -> None:
        if (
            not self.video_widget.is_paused
            and self._preview_end > 0
            and position >= self._preview_end
        ):
            # Live preview is bounded to the unsaved trim selection --
            # stop right at the trim end instead of playing on into
            # footage that's about to be cut.
            self.video_widget.pause()
            self.video_widget.seek(self._preview_end)
            self.play_pause_btn.setText("Play")
            return
        if not self._slider_being_dragged and self._duration > 0:
            slider_value = int((position / self._duration) * self.seek_slider.maximum())
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(slider_value)
            self.seek_slider.blockSignals(False)
        self.time_label.setText(f"{_format_time(position)} / {_format_time(self._duration)}")
        self.trim_timeline.set_playhead(position)

    def _on_duration_known(self, duration: float) -> None:
        self._duration = duration
        self.time_label.setText(f"0:00 / {_format_time(duration)}")
        self.trim_timeline.set_duration(duration)
        self._preview_start = 0.0
        self._preview_end = duration
        self._update_trim_range_label(0.0, duration)

    def _on_slider_released(self) -> None:
        self._slider_being_dragged = False
        if self._duration > 0:
            fraction = self.seek_slider.value() / self.seek_slider.maximum()
            self.video_widget.seek(fraction * self._duration)

    def _on_playback_ended(self) -> None:
        self.play_pause_btn.setText("Play")

    # ------------------------------------------------------------ trim timeline

    def _on_trim_drag_started(self) -> None:
        # Dragging a handle while the video is playing makes the live-seek
        # feedback confusing to watch -- pause first, matching how Medal
        # and similar tools behave while scrubbing trim handles.
        self.video_widget.pause()
        self.play_pause_btn.setText("Play")

    def _on_trim_range_changed(self, start: float, end: float) -> None:
        self._update_trim_range_label(start, end)

    def _on_trim_drag_finished(self, start: float, end: float) -> None:
        # Commit the new preview bounds only now, on release -- not on
        # every intermediate range_changed while the handle is still
        # moving, so the live-playback clamp doesn't jitter mid-drag.
        self._preview_start = start
        self._preview_end = end

    def _on_trim_seek_requested(self, position: float) -> None:
        self.video_widget.seek(position)

    def _update_trim_range_label(self, start: float, end: float) -> None:
        self.trim_range_label.setText(
            f"Start: {_format_time(start)}   End: {_format_time(end)}   "
            f"Selected: {_format_time(end - start)}"
        )

    # ------------------------------------------------------------ save

    def _local_save(self) -> None:
        if self.current_video_id is None:
            return
        start = self.trim_timeline.start
        end = self.trim_timeline.end
        frame_perfect = self.frame_perfect_checkbox.isChecked()
        try:
            library.apply_trim(self.current_video_id, start, end, frame_perfect=frame_perfect)
        except Exception as e:
            QMessageBox.critical(self, "Trim Failed", str(e))
            return
        self._refresh_display()

    def _save_and_upload(self) -> None:
        QMessageBox.information(
            self, "Not Implemented Yet",
            "YouTube upload is coming in a later build phase (OAuth setup "
            "isn't wired up yet). Use Local Save for now.",
        )

    # ------------------------------------------------------------ undo

    def _undo(self) -> None:
        if self.current_video_id is not None:
            library.undo_edit(self.current_video_id)
            self._refresh_display()

    def _clear_edit_backup(self) -> None:
        if self.current_video_id is not None:
            library.clear_edit_backup(self.current_video_id)
            self._refresh_display()
