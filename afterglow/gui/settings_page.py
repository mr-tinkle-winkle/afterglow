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
    QSpinBox, QDoubleSpinBox, QPushButton, QFileDialog, QLabel, QScrollArea,
    QMessageBox, QTabWidget, QCheckBox, QComboBox,
)

from .. import config as config_module
from .. import clips
from ..clips import ClipError
from .clip_config_row import ClipConfigRow
from .filters_settings_page import FiltersSettingsPage


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = config_module.load()
        self._rows: list[ClipConfigRow] = []
        self._deleted_ids: set[int] = set()

        outer = QVBoxLayout(self)

        # A tab per settings cluster. "Clipping" is everything that used
        # to be the page's single "General" section (OBS/clip-capture
        # settings) -- renamed once an actual appearance-focused
        # "General" tab existed, since "General" then meant something
        # more specific than "everything else."
        tabs = QTabWidget()
        clipping_page = QWidget()
        clipping_layout = QVBoxLayout(clipping_page)
        clipping_layout.addWidget(self._build_obs_group())
        clipping_layout.addWidget(self._build_clipping_group())
        clipping_layout.addWidget(self._build_clip_options_group(), stretch=1)
        tabs.addTab(clipping_page, "Clipping")

        general_page = QWidget()
        general_layout = QVBoxLayout(general_page)
        general_layout.addWidget(self._build_appearance_group())
        general_layout.addStretch(1)
        tabs.addTab(general_page, "General")

        self.filters_settings_page = FiltersSettingsPage()
        tabs.addTab(self.filters_settings_page, "Filters")
        outer.addWidget(tabs, stretch=1)

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

        self.obs_wait_after_finish_spin = QDoubleSpinBox()
        self.obs_wait_after_finish_spin.setRange(0.0, 30.0)
        self.obs_wait_after_finish_spin.setSingleStep(0.5)
        self.obs_wait_after_finish_spin.setSuffix(" sec")
        self.obs_wait_after_finish_spin.setValue(
            self._settings.obs.wait_after_replay_buffer_finishes_sec
        )
        self.obs_wait_after_finish_spin.setToolTip(
            "afterglow reacts to OBS's own ReplayBufferSaved event, so this "
            "should normally stay at 0. Only raise it if capture still "
            "seems to grab an incomplete/truncated file on your machine."
        )
        form.addRow("Wait after OBS Replay Buffer Finishes:", self.obs_wait_after_finish_spin)

        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self._test_obs_connection)
        form.addRow("", test_btn)

        return group

    def _test_obs_connection(self) -> None:
        from ..config import OBSSettings
        from ..obs_client import OBSClient, OBSError

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

    # ------------------------------------------------------------ clipping group

    def _build_clipping_group(self) -> QGroupBox:
        group = QGroupBox("Clip Capture")
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

        # Holds the dialog's result between "Advanced Sound..." closing
        # and _save() actually writing it to self._settings -- mirrors
        # every other field on this page, which all stay as pending
        # widget state until Save is clicked.
        self._pending_advanced_sounds = dict(self._settings.advanced_sounds)
        self._pending_error_sounds = dict(self._settings.error_sounds)
        self._pending_default_error_sound = self._settings.default_error_sound_path
        advanced_sound_btn = QPushButton("Advanced Sound...")
        advanced_sound_btn.clicked.connect(self._open_advanced_sound_dialog)
        form.addRow("", advanced_sound_btn)

        return group

    # ------------------------------------------------------------ appearance group

    def _build_appearance_group(self) -> QGroupBox:
        group = QGroupBox("Appearance")
        form = QFormLayout(group)
        a = self._settings.appearance

        self.resize_text_check = QCheckBox()
        self.resize_text_check.setChecked(a.resize_text_to_fit)
        self.resize_text_check.setToolTip(
            "Shrinks a clip's title font just enough to keep it on one line "
            "instead of wrapping to a second."
        )
        form.addRow("Resize Text to Fit:", self.resize_text_check)

        self.inactive_border_width_spin = QSpinBox()
        self.inactive_border_width_spin.setRange(0, 50)
        self.inactive_border_width_spin.setValue(a.inactive_border_width)
        form.addRow("Inactive Border Width:", self.inactive_border_width_spin)

        self.inactive_border_brightness_spin = QSpinBox()
        self.inactive_border_brightness_spin.setRange(0, 100)
        self.inactive_border_brightness_spin.setSuffix("%")
        self.inactive_border_brightness_spin.setValue(a.inactive_border_brightness)
        form.addRow("Inactive Border Brightness:", self.inactive_border_brightness_spin)

        self.active_border_width_spin = QSpinBox()
        self.active_border_width_spin.setRange(0, 50)
        self.active_border_width_spin.setValue(a.active_border_width)
        form.addRow("Active Border Width:", self.active_border_width_spin)

        self.active_border_brightness_spin = QSpinBox()
        self.active_border_brightness_spin.setRange(0, 100)
        self.active_border_brightness_spin.setSuffix("%")
        self.active_border_brightness_spin.setValue(a.active_border_brightness)
        form.addRow("Active Border Brightness:", self.active_border_brightness_spin)

        # Per-button multiplier ON TOP OF the two shared brightness
        # settings above -- 100% = no change from the shared value for
        # that button; e.g. 50% here darkens this ONE button further
        # still, while values above 100% only get back toward "no
        # darkening at all" rather than actually brightening past it
        # (see _ScalingIconButton's own comment on why).
        self.library_border_brightness_mult_spin = QSpinBox()
        self.library_border_brightness_mult_spin.setRange(0, 200)
        self.library_border_brightness_mult_spin.setSuffix("%")
        self.library_border_brightness_mult_spin.setValue(a.library_border_brightness_multiplier)
        form.addRow("Library Border Brightness Multiplier:", self.library_border_brightness_mult_spin)

        self.editor_border_brightness_mult_spin = QSpinBox()
        self.editor_border_brightness_mult_spin.setRange(0, 200)
        self.editor_border_brightness_mult_spin.setSuffix("%")
        self.editor_border_brightness_mult_spin.setValue(a.editor_border_brightness_multiplier)
        form.addRow("Editor Border Brightness Multiplier:", self.editor_border_brightness_mult_spin)

        self.settings_border_brightness_mult_spin = QSpinBox()
        self.settings_border_brightness_mult_spin.setRange(0, 200)
        self.settings_border_brightness_mult_spin.setSuffix("%")
        self.settings_border_brightness_mult_spin.setValue(a.settings_border_brightness_multiplier)
        form.addRow("Settings Border Brightness Multiplier:", self.settings_border_brightness_mult_spin)

        # Shared across all three sidebar buttons (see
        # AppearanceSettings.sidebar_border_image_path's own comment for
        # why this one isn't per-button like the multipliers above).
        sidebar_image_row = QHBoxLayout()
        self.sidebar_border_image_edit = QLineEdit(a.sidebar_border_image_path)
        self.sidebar_border_image_edit.setPlaceholderText("(use the built-in gradient)")
        sidebar_image_browse = QPushButton("Browse...")
        sidebar_image_browse.clicked.connect(
            lambda: self._browse_border_image(self.sidebar_border_image_edit)
        )
        sidebar_image_row.addWidget(self.sidebar_border_image_edit)
        sidebar_image_row.addWidget(sidebar_image_browse)
        form.addRow("Sidebar Border Image:", sidebar_image_row)

        self.sidebar_border_hue_shift_spin = QSpinBox()
        self.sidebar_border_hue_shift_spin.setRange(0, 359)
        self.sidebar_border_hue_shift_spin.setSuffix("\u00b0")
        self.sidebar_border_hue_shift_spin.setValue(a.sidebar_border_hue_shift)
        form.addRow("Sidebar Border Hue Shift:", self.sidebar_border_hue_shift_spin)

        self.settings_border_combo = QComboBox()
        self.settings_border_combo.addItem("Disabled", "disabled")
        self.settings_border_combo.addItem("Only when on the settings page", "only_settings")
        self.settings_border_combo.addItem("Always", "always")
        index = self.settings_border_combo.findData(a.settings_border_mode)
        self.settings_border_combo.setCurrentIndex(index if index >= 0 else 1)
        form.addRow("Border around Settings?", self.settings_border_combo)

        self.unedited_selected_border_width_spin = QSpinBox()
        self.unedited_selected_border_width_spin.setRange(0, 50)
        self.unedited_selected_border_width_spin.setValue(a.unedited_selected_border_width)
        form.addRow("Unedited/Selected Border Width:", self.unedited_selected_border_width_spin)

        self.unedited_highlight_brightness_spin = QSpinBox()
        self.unedited_highlight_brightness_spin.setRange(0, 100)
        self.unedited_highlight_brightness_spin.setSuffix("%")
        self.unedited_highlight_brightness_spin.setValue(a.unedited_highlight_brightness)
        form.addRow("Unedited Highlight Brightness:", self.unedited_highlight_brightness_spin)

        unedited_image_row = QHBoxLayout()
        self.unedited_border_image_edit = QLineEdit(a.unedited_border_image_path)
        self.unedited_border_image_edit.setPlaceholderText("(use the built-in gradient)")
        unedited_image_browse = QPushButton("Browse...")
        unedited_image_browse.clicked.connect(
            lambda: self._browse_border_image(self.unedited_border_image_edit)
        )
        unedited_image_row.addWidget(self.unedited_border_image_edit)
        unedited_image_row.addWidget(unedited_image_browse)
        form.addRow("Unedited Border Image:", unedited_image_row)

        self.unedited_border_hue_shift_spin = QSpinBox()
        self.unedited_border_hue_shift_spin.setRange(0, 359)
        self.unedited_border_hue_shift_spin.setSuffix("\u00b0")
        self.unedited_border_hue_shift_spin.setValue(a.unedited_border_hue_shift)
        form.addRow("Unedited Border Hue Shift:", self.unedited_border_hue_shift_spin)

        selected_image_row = QHBoxLayout()
        self.selected_border_image_edit = QLineEdit(a.selected_border_image_path)
        self.selected_border_image_edit.setPlaceholderText("(use the built-in gold/white gradient)")
        selected_image_browse = QPushButton("Browse...")
        selected_image_browse.clicked.connect(
            lambda: self._browse_border_image(self.selected_border_image_edit)
        )
        selected_image_row.addWidget(self.selected_border_image_edit)
        selected_image_row.addWidget(selected_image_browse)
        form.addRow("Selected Border Image:", selected_image_row)

        self.filter_icon_size_spin = QSpinBox()
        self.filter_icon_size_spin.setRange(8, 200)
        self.filter_icon_size_spin.setSuffix(" px")
        self.filter_icon_size_spin.setValue(a.filter_icon_size)
        form.addRow("Filter Icons Size:", self.filter_icon_size_spin)

        # Replaces the old single "Library Page Icons Size" -- that one
        # setting was actually wired to all three sidebar nav buttons at
        # once (a bug) and had no effect at all on the Library page's own
        # tab icons, despite the name. Five independent settings now:
        # three for the sidebar nav buttons (Library/Editor/Settings),
        # two for the Library page's own Local ("Saved Videos")/Uploaded
        # tab icons.
        self.library_icon_size_spin = QSpinBox()
        self.library_icon_size_spin.setRange(10, 200)
        self.library_icon_size_spin.setSuffix("%")
        self.library_icon_size_spin.setValue(a.library_icon_size)
        form.addRow("Library Icon Size (sidebar):", self.library_icon_size_spin)

        self.editor_icon_size_spin = QSpinBox()
        self.editor_icon_size_spin.setRange(10, 200)
        self.editor_icon_size_spin.setSuffix("%")
        self.editor_icon_size_spin.setValue(a.editor_icon_size)
        form.addRow("Editor Icon Size (sidebar):", self.editor_icon_size_spin)

        self.settings_icon_size_spin = QSpinBox()
        self.settings_icon_size_spin.setRange(10, 200)
        self.settings_icon_size_spin.setSuffix("%")
        self.settings_icon_size_spin.setValue(a.settings_icon_size)
        form.addRow("Settings Icon Size (sidebar):", self.settings_icon_size_spin)

        self.saved_videos_icon_size_spin = QSpinBox()
        self.saved_videos_icon_size_spin.setRange(10, 200)
        self.saved_videos_icon_size_spin.setSuffix("%")
        self.saved_videos_icon_size_spin.setValue(a.saved_videos_icon_size)
        form.addRow("Saved Videos Icon Size (Library tab):", self.saved_videos_icon_size_spin)

        self.uploaded_videos_icon_size_spin = QSpinBox()
        self.uploaded_videos_icon_size_spin.setRange(10, 200)
        self.uploaded_videos_icon_size_spin.setSuffix("%")
        self.uploaded_videos_icon_size_spin.setValue(a.uploaded_videos_icon_size)
        form.addRow("Uploaded Videos Icon Size (Library tab):", self.uploaded_videos_icon_size_spin)

        # A combo box rather than a checkbox pair (Fullscreen + Maximized)
        # deliberately -- two independent checkboxes could both end up
        # checked at once, which has no sensible meaning. Moved here from
        # Clipping since it's a startup-appearance choice.
        self.startup_window_mode_combo = QComboBox()
        self.startup_window_mode_combo.addItem("Normal", "normal")
        self.startup_window_mode_combo.addItem("Maximized", "maximized")
        self.startup_window_mode_combo.addItem("Fullscreen", "fullscreen")
        index = self.startup_window_mode_combo.findData(a.startup_window_mode)
        self.startup_window_mode_combo.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Startup Window Mode:", self.startup_window_mode_combo)

        note = QLabel(
            "Sidebar/border settings apply next launch (the sidebar buttons "
            "are only ever built once)."
        )
        note.setStyleSheet("color: gray;")
        note.setWordWrap(True)
        form.addRow("", note)

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

    def _browse_border_image(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Border Image", "", "Image Files (*.png *.jpg *.jpeg *.bmp *.webp);;All Files (*)"
        )
        if path:
            edit.setText(path)

    def _open_advanced_sound_dialog(self) -> None:
        from .advanced_sound_dialog import AdvancedSoundDialog
        dialog = AdvancedSoundDialog(
            self._pending_advanced_sounds, self._pending_error_sounds,
            self._pending_default_error_sound, parent=self,
        )
        if dialog.exec():
            self._pending_advanced_sounds = dialog.get_advanced_sounds()
            self._pending_error_sounds = dialog.get_error_sounds()
            self._pending_default_error_sound = dialog.get_default_error_sound()

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
        self._settings.obs.wait_after_replay_buffer_finishes_sec = (
            self.obs_wait_after_finish_spin.value()
        )
        self._settings.clips_dir = self.clips_dir_edit.text().strip()
        self._settings.default_sound_path = self.default_sound_edit.text().strip()
        self._settings.advanced_sounds = dict(self._pending_advanced_sounds)
        self._settings.error_sounds = dict(self._pending_error_sounds)
        self._settings.default_error_sound_path = self._pending_default_error_sound

        a = self._settings.appearance
        a.resize_text_to_fit = self.resize_text_check.isChecked()
        a.inactive_border_width = self.inactive_border_width_spin.value()
        a.inactive_border_brightness = self.inactive_border_brightness_spin.value()
        a.active_border_width = self.active_border_width_spin.value()
        a.active_border_brightness = self.active_border_brightness_spin.value()
        a.library_border_brightness_multiplier = self.library_border_brightness_mult_spin.value()
        a.editor_border_brightness_multiplier = self.editor_border_brightness_mult_spin.value()
        a.settings_border_brightness_multiplier = self.settings_border_brightness_mult_spin.value()
        a.sidebar_border_image_path = self.sidebar_border_image_edit.text().strip()
        a.sidebar_border_hue_shift = self.sidebar_border_hue_shift_spin.value()
        a.unedited_border_image_path = self.unedited_border_image_edit.text().strip()
        a.unedited_border_hue_shift = self.unedited_border_hue_shift_spin.value()
        a.selected_border_image_path = self.selected_border_image_edit.text().strip()
        a.settings_border_mode = self.settings_border_combo.currentData()
        a.unedited_selected_border_width = self.unedited_selected_border_width_spin.value()
        a.unedited_highlight_brightness = self.unedited_highlight_brightness_spin.value()
        a.filter_icon_size = self.filter_icon_size_spin.value()
        a.library_icon_size = self.library_icon_size_spin.value()
        a.editor_icon_size = self.editor_icon_size_spin.value()
        a.settings_icon_size = self.settings_icon_size_spin.value()
        a.saved_videos_icon_size = self.saved_videos_icon_size_spin.value()
        a.uploaded_videos_icon_size = self.uploaded_videos_icon_size_spin.value()
        a.startup_window_mode = self.startup_window_mode_combo.currentData()

        config_module.save(self._settings)
        self.filters_settings_page.save()

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

    def refresh_dynamic_lists(self) -> None:
        """Passthrough to the Filters sub-page -- see its own docstring
        for why this is needed (called by MainWindow on Settings nav)."""
        self.filters_settings_page.refresh_dynamic_lists()
