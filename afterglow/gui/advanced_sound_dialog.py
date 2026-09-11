"""
"Advanced Sound": a distinct sound for each pipeline keyframe (see
keyframes.py) plus a distinct "Error Noise" for if that keyframe's own
work fails, with a single fallback error sound for keyframes that don't
have their own. Opened from Settings > Clip Capture via an "Advanced
Sound..." button rather than cramming ten-plus file pickers into the
main form directly.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLineEdit, QPushButton,
    QFileDialog, QLabel, QDialogButtonBox, QWidget,
)

from .. import keyframes

_SOUND_FILE_FILTER = "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*)"


class AdvancedSoundDialog(QDialog):
    def __init__(self, advanced_sounds: dict[str, str], error_sounds: dict[str, str],
                 default_error_sound: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Sound")

        self._advanced_edits: dict[str, QLineEdit] = {}
        self._error_edits: dict[str, QLineEdit] = {}

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("<b>Keyframe Sounds</b> -- played as each stage completes"))
        sounds_form = QFormLayout()
        layout.addLayout(sounds_form)
        for key, label in keyframes.PIPELINE_KEYFRAMES:
            row_widget, edit = self._make_sound_row(advanced_sounds.get(key, ""))
            self._advanced_edits[key] = edit
            sounds_form.addRow(f"{label}:", row_widget)

        layout.addWidget(QLabel("<b>Error Noise</b> -- played instead, if that stage fails"))
        error_form = QFormLayout()
        layout.addLayout(error_form)
        for key, label in keyframes.PIPELINE_KEYFRAMES:
            row_widget, edit = self._make_sound_row(error_sounds.get(key, ""))
            self._error_edits[key] = edit
            error_form.addRow(f"{label}:", row_widget)

        default_error_row, self._default_error_edit = self._make_sound_row(default_error_sound)
        error_form.addRow("Default (fallback):", default_error_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _make_sound_row(self, current_path: str) -> tuple[QWidget, QLineEdit]:
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(current_path)
        edit.setPlaceholderText("(none)")
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda: self._browse(edit))
        row.addWidget(edit)
        row.addWidget(browse)
        return row_widget, edit

    def _browse(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose Sound File", edit.text(), _SOUND_FILE_FILTER)
        if path:
            edit.setText(path)

    def get_advanced_sounds(self) -> dict[str, str]:
        return {k: e.text().strip() for k, e in self._advanced_edits.items() if e.text().strip()}

    def get_error_sounds(self) -> dict[str, str]:
        return {k: e.text().strip() for k, e in self._error_edits.items() if e.text().strip()}

    def get_default_error_sound(self) -> str:
        return self._default_error_edit.text().strip()
