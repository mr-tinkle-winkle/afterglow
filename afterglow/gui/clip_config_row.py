"""
One row in the Clip Options list on the Settings page. Collapsible
("expandable list" per spec): collapsed shows a one-line summary
(name, length, hotkey), expanded shows the editable fields.

This widget only edits in-memory state + emits signals; SettingsPage is
responsible for persisting changes to the DB via clips.py, and for
deciding when to actually save (see SettingsPage for the save strategy).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QSpinBox, QToolButton, QPushButton, QFileDialog, QFrame,
)

from ..hotkeys import ComboError, parse_combo
from .hotkey_record_dialog import HotkeyRecordDialog


class ClipConfigRow(QFrame):
    changed = Signal()          # any field edited (name/length/sound/hotkey)
    delete_requested = Signal()

    def __init__(self, clip_config_id: int | None, name: str, length_seconds: int,
                 sound_path: str | None, hotkey: str | None, parent=None):
        super().__init__(parent)
        self.clip_config_id = clip_config_id  # None for a not-yet-saved new row
        self.setFrameShape(QFrame.StyledPanel)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        # ---- collapsible header ----
        header = QHBoxLayout()
        self.toggle_btn = QToolButton()
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(True)
        self.toggle_btn.setArrowType(Qt.DownArrow)
        self.toggle_btn.clicked.connect(self._on_toggle)
        header.addWidget(self.toggle_btn)

        self.summary_label = QLabel()
        header.addWidget(self.summary_label, stretch=1)

        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self.delete_requested.emit)
        header.addWidget(self.delete_btn)

        outer.addLayout(header)

        # ---- expandable body ----
        self.body = QWidget()
        form = QFormLayout(self.body)

        self.name_edit = QLineEdit(name)
        self.name_edit.textChanged.connect(self._on_any_change)
        form.addRow("Name:", self.name_edit)

        self.length_spin = QSpinBox()
        self.length_spin.setRange(1, 3600)
        self.length_spin.setSuffix(" sec")
        self.length_spin.setValue(length_seconds)
        self.length_spin.valueChanged.connect(self._on_any_change)
        form.addRow("Length:", self.length_spin)

        sound_row = QHBoxLayout()
        self.sound_edit = QLineEdit(sound_path or "")
        self.sound_edit.setPlaceholderText("(use default sound)")
        self.sound_edit.textChanged.connect(self._on_any_change)
        sound_browse = QPushButton("Browse...")
        sound_browse.clicked.connect(self._browse_sound)
        sound_row.addWidget(self.sound_edit)
        sound_row.addWidget(sound_browse)
        form.addRow("Sound:", sound_row)

        hotkey_row = QHBoxLayout()
        self.hotkey_edit = QLineEdit(hotkey or "")
        self.hotkey_edit.setReadOnly(True)
        self.hotkey_edit.setPlaceholderText("(no hotkey set)")
        record_btn = QPushButton("Record...")
        record_btn.clicked.connect(self._record_hotkey)
        hotkey_row.addWidget(self.hotkey_edit)
        hotkey_row.addWidget(record_btn)
        form.addRow("Hotkey:", hotkey_row)

        outer.addWidget(self.body)
        self._update_summary()

    # ------------------------------------------------------------ behavior

    def _on_toggle(self) -> None:
        expanded = self.toggle_btn.isChecked()
        self.body.setVisible(expanded)
        self.toggle_btn.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)

    def _on_any_change(self, *_args) -> None:
        self._update_summary()
        self.changed.emit()

    def _update_summary(self) -> None:
        hotkey = self.hotkey_edit.text() or "no hotkey"
        self.summary_label.setText(
            f"<b>{self.name_edit.text() or '(unnamed)'}</b>  "
            f"&nbsp;&middot;&nbsp; {self.length_spin.value()}s "
            f"&nbsp;&middot;&nbsp; {hotkey}"
        )

    def _browse_sound(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Sound File", "", "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*)"
        )
        if path:
            self.sound_edit.setText(path)

    def _record_hotkey(self) -> None:
        dialog = HotkeyRecordDialog(self, current_combo=self.hotkey_edit.text() or None)
        if dialog.exec() and dialog.result_combo:
            try:
                parse_combo(dialog.result_combo)  # validate before accepting
            except ComboError:
                return
            self.hotkey_edit.setText(dialog.result_combo)
            self._on_any_change()

    # ------------------------------------------------------------ data access

    def to_fields(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "length_seconds": self.length_spin.value(),
            "sound_path": self.sound_edit.text().strip() or None,
            "hotkey": self.hotkey_edit.text().strip() or None,
        }
