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

The actual Medal-style trim UI (libmpv preview, drag handles, audio graph
with per-segment volume/mute/trim/reposition) is the next build phase and
isn't implemented yet -- this page currently shows which video is loaded
and basic info about it, as the correctly-wired shell that phase will be
built into.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton

from .. import library, thumbnails


class EditorPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_video_id: int | None = None

        layout = QVBoxLayout(self)

        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(300)
        self.preview_label.setStyleSheet("background: #1a1a1a;")
        layout.addWidget(self.preview_label, stretch=1)

        self.info_label = QLabel("No video selected. Double-click a clip in the Library, "
                                  "or right-click it and choose Edit.")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        coming_soon_row = QHBoxLayout()
        coming_soon_label = QLabel(
            "Trim controls, live preview scrubbing, and the audio graph editor "
            "are coming in the next build phase."
        )
        coming_soon_label.setStyleSheet("color: gray;")
        coming_soon_label.setAlignment(Qt.AlignCenter)
        coming_soon_row.addWidget(coming_soon_label)
        layout.addLayout(coming_soon_row)

        button_row = QHBoxLayout()
        self.undo_btn = QPushButton("Undo Last Edit")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo)
        button_row.addWidget(self.undo_btn)
        layout.addLayout(button_row)

    def load_video(self, video_id: int) -> None:
        self.current_video_id = video_id
        self._refresh_display()

    def _refresh_display(self) -> None:
        if self.current_video_id is None:
            self.preview_label.clear()
            self.preview_label.setText("")
            self.info_label.setText(
                "No video selected. Double-click a clip in the Library, "
                "or right-click it and choose Edit."
            )
            self.undo_btn.setEnabled(False)
            return

        video = library.get_video(self.current_video_id)
        thumb_path = thumbnails.get_thumbnail(video.id, Path(video.path))
        if thumb_path:
            pixmap = QPixmap(str(thumb_path))
            self.preview_label.setPixmap(
                pixmap.scaled(640, 360, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self.preview_label.setText("(no preview available)")

        self.info_label.setText(
            f"<b>{video.title}</b><br>{video.duration_sec:.1f}s"
            f"{' &middot; ' + ', '.join(video.tags) if video.tags else ''}"
        )
        self.undo_btn.setEnabled(video.has_edit)

    def _undo(self) -> None:
        if self.current_video_id is not None:
            library.undo_edit(self.current_video_id)
            self._refresh_display()
