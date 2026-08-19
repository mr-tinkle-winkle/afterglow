"""
The Settings page (page 1 of 3 in the eventual app). Covers only what's
needed for the clipping functionality right now, per the current build
order: OBS connection, clips directory, default sound, and the clip
options list (name/length/sound/hotkey each). YouTube settings are
deliberately not shown yet -- those fields already exist in config.py's
schema but the UI for them comes with the Uploaded Library page later.

Save strategy: explicit "Save" button rather than autosave-on-every-
keystroke. Clip config rows are diffed against the DB on save (added /
updated / deleted) rather than the naive "delete everything, reinsert
everything" approach, so that a clip config's numeric id -- and therefore
any daemon state or future video->clip_config_id references -- stays
stable across edits.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLineEdit,
    QSpinBox, QPushButton, QFileDialog, QLabel, QScrollArea, QMessageBox,
)

import config as config_module
import clips
from clips import ClipError
from gui.clip_config_row import ClipConfigRow


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = config_module.load()
        self._rows: list[ClipConfigRow] = []
        self._deleted_ids: set[int] = set()

        outer = QVBoxLayout(self)

        outer.addWidget(self._build_obs_group())
        outer.addWidget(self._build_general_group())
        outer.addWidget(self._build_clip_options_group(), stretch=1)

        save_row = QHBoxLayout()
        self.status_label = QLabel("")
        save_row.addWidget(self.status_label, stretch=1)
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        outer.addLayout(save_row)

        self._load_clip_configs()

    # ------------------------------------------------------------ OBS group

    def _build_obs_group(self) -> QGroupBox:
        group = QGroupBox("OBS Connection")
        form = QFormLayout(group)

        self.obs_host_edit = QLineEdit(self._settings.obs.host)
        form.addRow("Host:", self.obs_host_edit)

        self.obs_port_spin = QSpinBox()
        self.obs_port_spin.setRange(1, 65535)
        self.obs_port_spin.setValue(self._settings.obs.port)
        form.addRow("Port:", self.obs_port_spin)

        pw_row = QHBoxLayout()
        self.obs_password_edit = QLineEdit(self._settings.obs.password)
        self.obs_password_edit.setEchoMode(QLineEdit.Password)
        show_btn = QPushButton("Show")
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda checked: self.obs_password_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        pw_row.addWidget(self.obs_password_edit)
        pw_row.addWidget(show_btn)
        form.addRow("Password:", pw_row)

        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_obs_connection)
        form.addRow("", test_btn)

        return group

    def _test_obs_connection(self) -> None:
        from config import OBSSettings
        from obs_client import OBSClient, OBSError

        test_settings = OBSSettings(
            host=self.obs_host_edit.text().strip(),
            port=self.obs_port_spin.value(),
            password=self.obs_password_edit.text(),
        )
        try:
            with OBSClient(test_settings) as c:
                buffer_len = c.get_replay_buffer_max_seconds()
            msg = "Connected to OBS successfully."
            if buffer_len:
                msg += f"\nConfigured replay buffer length: {buffer_len}s"
                max_clip_len = max((r.length_spin.value() for r in self._rows), default=0)
                if max_clip_len > buffer_len:
                    msg += (
                        f"\n\nWarning: your longest clip option ({max_clip_len}s) "
                        f"exceeds OBS's replay buffer length ({buffer_len}s). "
                        f"That clip will end up shorter than requested. "
                        f"Increase the buffer length in OBS's Output settings."
                    )
            QMessageBox.information(self, "OBS Connection", msg)
        except OBSError as e:
            QMessageBox.critical(self, "OBS Connection Failed", str(e))

    # ------------------------------------------------------------ general group

    def _build_general_group(self) -> QGroupBox:
        group = QGroupBox("General")
        form = QFormLayout(group)

        dir_row = QHBoxLayout()
        self.clips_dir_edit = QLineEdit(self._settings.clips_dir)
        dir_browse = QPushButton("Browse...")
        dir_browse.clicked.connect(self._browse_clips_dir)
        dir_row.addWidget(self.clips_dir_edit)
        dir_row.addWidget(dir_browse)
        form.addRow("Clips folder:", dir_row)

        sound_row = QHBoxLayout()
        self.default_sound_edit = QLineEdit(self._settings.default_sound_path)
        self.default_sound_edit.setPlaceholderText("(no default sound)")
        sound_browse = QPushButton("Browse...")
        sound_browse.clicked.connect(self._browse_default_sound)
        sound_row.addWidget(self.default_sound_edit)
        sound_row.addWidget(sound_browse)
        form.addRow("Default sound:", sound_row)

        return group

    def _browse_clips_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose Clips Folder", self.clips_dir_edit.text())
        if path:
            self.clips_dir_edit.setText(path)

    def _browse_default_sound(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Default Sound", "", "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*)"
        )
        if path:
            self.default_sound_edit.setText(path)

    # ------------------------------------------------------------ clip options group

    def _build_clip_options_group(self) -> QGroupBox:
        group = QGroupBox("Clip Options")
        outer = QVBoxLayout(group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.addStretch(1)
        scroll.setWidget(self.rows_container)
        outer.addWidget(scroll, stretch=1)

        add_btn = QPushButton("+ Add Clip Option")
        add_btn.clicked.connect(lambda: self._add_row())
        outer.addWidget(add_btn)

        return group

    def _load_clip_configs(self) -> None:
        for cfg in clips.list_clip_configs():
            self._add_row(cfg.id, cfg.name, cfg.length_seconds, cfg.sound_path, cfg.hotkey)

    def _add_row(self, clip_config_id: int | None = None, name: str = "New Clip",
                 length_seconds: int = 30, sound_path: str | None = None,
                 hotkey: str | None = None) -> None:
        row = ClipConfigRow(clip_config_id, name, length_seconds, sound_path, hotkey)
        row.delete_requested.connect(lambda: self._remove_row(row))
        # insert before the trailing stretch
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self._rows.append(row)

    def _remove_row(self, row: ClipConfigRow) -> None:
        if row.clip_config_id is not None:
            self._deleted_ids.add(row.clip_config_id)
        self._rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    # ------------------------------------------------------------ save

    def _save(self) -> None:
        # Validate all rows first so a bad row doesn't leave things half-saved.
        seen_names = set()
        for row in self._rows:
            fields = row.to_fields()
            if not fields["name"]:
                QMessageBox.warning(self, "Invalid Clip Option", "Every clip option needs a name.")
                return
            key = fields["name"].lower()
            if key in seen_names:
                QMessageBox.warning(
                    self, "Duplicate Name",
                    f"Clip option name '{fields['name']}' is used more than once. "
                    f"Names must be unique.",
                )
                return
            seen_names.add(key)

        self._settings.obs.host = self.obs_host_edit.text().strip()
        self._settings.obs.port = self.obs_port_spin.value()
        self._settings.obs.password = self.obs_password_edit.text()
        self._settings.clips_dir = self.clips_dir_edit.text().strip()
        self._settings.default_sound_path = self.default_sound_edit.text().strip()
        config_module.save(self._settings)

        try:
            for clip_config_id in self._deleted_ids:
                clips.delete_clip_config(clip_config_id)
            self._deleted_ids.clear()

            for row in self._rows:
                fields = row.to_fields()
                if row.clip_config_id is None:
                    new_cfg = clips.create_clip_config(**fields)
                    row.clip_config_id = new_cfg.id
                else:
                    clips.update_clip_config(row.clip_config_id, **fields)
        except ClipError as e:
            QMessageBox.critical(self, "Save Failed", str(e))
            return

        self.status_label.setText("Saved.")
