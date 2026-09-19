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

import tomllib

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QFileDialog, QLabel, QScrollArea,
    QComboBox, QColorDialog, QStackedWidget, QButtonGroup,
)
from PySide6.QtGui import QColor

from .. import config as config_module
from .. import clips
from ..clips import ClipError
from .clip_config_row import ClipConfigRow
from .filters_settings_page import FiltersSettingsPage
from .stats_settings_page import StatsPage
from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .custom_line_edit import CustomLineEdit
from .custom_spinbox import CustomSpinBox, CustomDoubleSpinBox
from .custom_combo_style import combo_box_stylesheet
from .custom_group_box import CustomGroupBox
from .custom_message_dialog import show_message
from .theme import Theme
from .page_outline import paint_page_outline, BORDER_WIDTH
from .smooth_scroll_area import SmoothScrollArea


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = config_module.load()
        self._rows: list[ClipConfigRow] = []
        self._deleted_ids: set[int] = set()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(BORDER_WIDTH, BORDER_WIDTH, BORDER_WIDTH, BORDER_WIDTH)

        # ---- header row: one fully separate, fully-rounded custom
        # button per settings page, each with real padding between them
        # (NOT touching/corner-skipped like the Library's Local/
        # Uploaded pair) -- per Max's direct instruction for this new
        # UI styling pass. Text for now; CustomButton already supports
        # set_icon_pixmap()/set_circular() for when Max provides the
        # actual per-tab icons and asks for these to become circles,
        # same as the Library header's Search/Refresh/Sort buttons --
        # no new plumbing needed for that later step.
        TAB_BUTTON_SPACING = 12
        self._settings_stack = QStackedWidget()
        self._tab_button_group = QButtonGroup(self)
        self._tab_button_group.setExclusive(True)
        tab_header = QHBoxLayout()
        tab_header.setSpacing(0)

        def _add_settings_tab(label: str, page: QWidget) -> None:
            index = self._settings_stack.count()
            self._settings_stack.addWidget(page)
            btn = CustomButton(label)
            btn.setCheckable(True)
            btn.setMinimumHeight(36)
            self._tab_button_group.addButton(btn, index)
            if tab_header.count() > 0:
                tab_header.addSpacing(TAB_BUTTON_SPACING)
            tab_header.addWidget(btn)
            if index == 0:
                btn.setChecked(True)

        self._tab_button_group.idClicked.connect(self._settings_stack.setCurrentIndex)

        # A tab per settings cluster. "Clipping" is everything that used
        # to be the page's single "General" section (OBS/clip-capture
        # settings) -- renamed once an actual appearance-focused
        # "General" tab existed, since "General" then meant something
        # more specific than "everything else."
        clipping_page = QWidget()
        clipping_layout = QVBoxLayout(clipping_page)
        clipping_layout.addWidget(self._build_obs_group())
        clipping_layout.addWidget(self._build_clipping_group())
        clipping_layout.addWidget(self._build_clip_options_group(), stretch=1)
        _add_settings_tab("Clipping", clipping_page)

        general_page = QWidget()
        general_layout = QVBoxLayout(general_page)
        general_layout.addWidget(self._build_appearance_group())
        general_layout.addStretch(1)
        _add_settings_tab("General", general_page)

        self.filters_settings_page = FiltersSettingsPage()
        _add_settings_tab("Filters", self.filters_settings_page)

        self.stats_page = StatsPage()
        _add_settings_tab("Stats", self.stats_page)

        advanced_page = QWidget()
        advanced_layout = QVBoxLayout(advanced_page)
        advanced_layout.addWidget(self._build_afterglow_theme_group())
        advanced_layout.addWidget(self._build_reset_group())
        advanced_layout.addWidget(self._build_import_export_group())
        advanced_layout.addWidget(self._build_performance_group())
        advanced_layout.addStretch(1)
        _add_settings_tab("Advanced", advanced_page)

        tab_header.addStretch(1)
        outer.addLayout(tab_header)
        outer.addWidget(self._settings_stack, stretch=1)

        save_row = QHBoxLayout()
        self.status_label = QLabel("")
        save_row.addWidget(self.status_label, stretch=1)
        save_btn = CustomButton("Save Settings")
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        outer.addLayout(save_row)

        self._load_clip_configs()

    # ------------------------------------------------------------ OBS group

    def paintEvent(self, event) -> None:
        # super() first, border second -- same reasoning as Editor's
        # own paintEvent (a real style could paint an opaque background
        # via the base class that would erase a border drawn first).
        super().paintEvent(event)
        # 3px, 15%-darker-than-itself page outline -- Settings has no
        # explicit background of its own, so this darkens the same
        # inherited app_background() Editor's outline uses too.
        theme = Theme(config_module.load().appearance)
        paint_page_outline(self, theme.app_background())

    def _build_obs_group(self) -> CustomGroupBox:
        group = CustomGroupBox("OBS Connection")
        form = group.make_layout(QFormLayout)

        self.obs_host_edit = CustomLineEdit(self._settings.obs.host)
        form.addRow("Host:", self.obs_host_edit)

        self.obs_port_spin = CustomSpinBox()
        self.obs_port_spin.setRange(1, 65535)
        self.obs_port_spin.setValue(self._settings.obs.port)
        form.addRow("Port:", self.obs_port_spin)

        pw_row = QHBoxLayout()
        self.obs_password_edit = CustomLineEdit(self._settings.obs.password)
        self.obs_password_edit.setEchoMode(QLineEdit.Password)
        show_btn = CustomButton("Show")
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda checked: self.obs_password_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        pw_row.addWidget(self.obs_password_edit)
        pw_row.addWidget(show_btn)
        form.addRow("Password:", pw_row)

        self.obs_wait_after_finish_spin = CustomDoubleSpinBox()
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

        test_btn = CustomButton("Test Connection")
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
            show_message(self, "OBS Connection", msg)
        except OBSError as e:
            show_message(self, "OBS Connection Failed", str(e))

    # ------------------------------------------------------------ clipping group

    def _build_clipping_group(self) -> CustomGroupBox:
        group = CustomGroupBox("Clip Capture")
        form = group.make_layout(QFormLayout)

        dir_row = QHBoxLayout()
        self.clips_dir_edit = CustomLineEdit(self._settings.clips_dir)
        dir_browse = CustomButton("Browse...")
        dir_browse.clicked.connect(self._browse_clips_dir)
        dir_row.addWidget(self.clips_dir_edit)
        dir_row.addWidget(dir_browse)
        form.addRow("Clips folder:", dir_row)

        sound_row = QHBoxLayout()
        self.default_sound_edit = CustomLineEdit(self._settings.default_sound_path)
        self.default_sound_edit.setPlaceholderText("(no default sound)")
        sound_browse = CustomButton("Browse...")
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
        advanced_sound_btn = CustomButton("Advanced Sound...")
        advanced_sound_btn.clicked.connect(self._open_advanced_sound_dialog)
        form.addRow("", advanced_sound_btn)

        return group

    # ------------------------------------------------------------ appearance group

    def _build_appearance_group(self) -> CustomGroupBox:
        group = CustomGroupBox("Appearance")
        form = group.make_layout(QFormLayout)
        a = self._settings.appearance

        # ---- UI update (Phase 1) ----
        self.rounded_corners_check = CustomCheckBox()
        self.rounded_corners_check.setChecked(a.rounded_corners_enabled)
        form.addRow("Rounded Corners:", self.rounded_corners_check)

        self.rounded_corner_radius_spin = CustomSpinBox()
        self.rounded_corner_radius_spin.setRange(0, 100)
        self.rounded_corner_radius_spin.setSuffix(" px")
        self.rounded_corner_radius_spin.setValue(a.rounded_corner_radius)
        form.addRow("Corner Radius:", self.rounded_corner_radius_spin)

        self.custom_buttons_check = CustomCheckBox()
        self.custom_buttons_check.setChecked(a.custom_buttons_enabled)
        self.custom_buttons_check.setToolTip(
            "Custom-painted buttons matching your KDE theme's colors, in "
            "place of plain native/KDE-styled buttons."
        )
        form.addRow("Custom Buttons:", self.custom_buttons_check)

        self.afterglow_theme_check = CustomCheckBox()
        self.afterglow_theme_check.setChecked(a.afterglow_theme_enabled)
        self.afterglow_theme_check.setToolTip(
            "Overrides Custom Buttons' color source with fixed colors instead "
            "of your live KDE theme -- edit the actual colors under Settings > "
            "Advanced."
        )
        form.addRow("Afterglow Theme:", self.afterglow_theme_check)

        self.ui_padding_spin = CustomSpinBox()
        self.ui_padding_spin.setRange(0, 100)
        self.ui_padding_spin.setSuffix(" px")
        self.ui_padding_spin.setValue(a.ui_padding)
        self.ui_padding_spin.setToolTip(
            "Shared spacing used everywhere -- between video cards, around a "
            "card's own inner boxes, and (later) between sidebar panels."
        )
        form.addRow("Padding:", self.ui_padding_spin)

        card_text_row = QHBoxLayout()
        self.card_text_color_edit = CustomLineEdit(a.card_text_color)
        card_text_pick = CustomButton("Pick...")
        card_text_pick.clicked.connect(lambda: self._pick_color(self.card_text_color_edit))
        card_text_row.addWidget(self.card_text_color_edit)
        card_text_row.addWidget(card_text_pick)
        form.addRow("Card Text Color:", card_text_row)

        card_text_outline_row = QHBoxLayout()
        self.card_text_outline_color_edit = CustomLineEdit(a.card_text_outline_color)
        card_text_outline_pick = CustomButton("Pick...")
        card_text_outline_pick.clicked.connect(lambda: self._pick_color(self.card_text_outline_color_edit))
        card_text_outline_row.addWidget(self.card_text_outline_color_edit)
        card_text_outline_row.addWidget(card_text_outline_pick)
        form.addRow("Card Text Outline Color:", card_text_outline_row)

        self.card_text_outline_width_spin = CustomDoubleSpinBox()
        self.card_text_outline_width_spin.setRange(0.0, 10.0)
        self.card_text_outline_width_spin.setSingleStep(0.5)
        self.card_text_outline_width_spin.setValue(a.card_text_outline_width)
        self.card_text_outline_width_spin.setToolTip(
            "Only affects the title -- the smaller info/date/tag-name lines "
            "always render fill-only (no stroke) regardless of this setting, "
            "since even the thinnest usable outline swallows their whole "
            "glyph at that small a font size. Verified directly: even the "
            "default of 3.0 already makes the title's fill color completely "
            "invisible at this app's current title font size -- lower this "
            "if you want the two-tone fill+outline look back."
        )
        form.addRow("Card Text Outline Width:", self.card_text_outline_width_spin)

        self.filter_outline_check = CustomCheckBox()
        self.filter_outline_check.setChecked(a.filter_outline_enabled)
        self.filter_outline_check.setToolTip(
            "Outlines each filter icon's own shape (not a square) in the Card "
            "Text Outline Color above, unless a filter has its own override "
            "color set in Settings > Filters."
        )
        form.addRow("Filter Outline:", self.filter_outline_check)

        self.resize_text_check = CustomCheckBox()
        self.resize_text_check.setChecked(a.resize_text_to_fit)
        self.resize_text_check.setToolTip(
            "Shrinks a clip's title font just enough to keep it on one line "
            "instead of wrapping to a second."
        )
        form.addRow("Resize Text to Fit:", self.resize_text_check)

        self.extended_dates_check = CustomCheckBox()
        self.extended_dates_check.setChecked(a.extended_dates)
        self.extended_dates_check.setToolTip(
            "Shows the full date and time (down to the second, whatever's "
            "in the video's own metadata) everywhere a video's date is "
            "shown, instead of just the date."
        )
        form.addRow("Extended Dates:", self.extended_dates_check)

        self.inactive_border_width_spin = CustomSpinBox()
        self.inactive_border_width_spin.setRange(0, 50)
        self.inactive_border_width_spin.setValue(a.inactive_border_width)
        form.addRow("Inactive Border Width:", self.inactive_border_width_spin)

        self.inactive_border_brightness_spin = CustomSpinBox()
        self.inactive_border_brightness_spin.setRange(0, 100)
        self.inactive_border_brightness_spin.setSuffix("%")
        self.inactive_border_brightness_spin.setValue(a.inactive_border_brightness)
        form.addRow("Inactive Border Brightness:", self.inactive_border_brightness_spin)

        self.active_border_width_spin = CustomSpinBox()
        self.active_border_width_spin.setRange(0, 50)
        self.active_border_width_spin.setValue(a.active_border_width)
        form.addRow("Active Border Width:", self.active_border_width_spin)

        self.active_border_brightness_spin = CustomSpinBox()
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
        self.library_border_brightness_mult_spin = CustomSpinBox()
        self.library_border_brightness_mult_spin.setRange(0, 200)
        self.library_border_brightness_mult_spin.setSuffix("%")
        self.library_border_brightness_mult_spin.setValue(a.library_border_brightness_multiplier)
        form.addRow("Library Border Brightness Multiplier:", self.library_border_brightness_mult_spin)

        self.editor_border_brightness_mult_spin = CustomSpinBox()
        self.editor_border_brightness_mult_spin.setRange(0, 200)
        self.editor_border_brightness_mult_spin.setSuffix("%")
        self.editor_border_brightness_mult_spin.setValue(a.editor_border_brightness_multiplier)
        form.addRow("Editor Border Brightness Multiplier:", self.editor_border_brightness_mult_spin)

        self.settings_border_brightness_mult_spin = CustomSpinBox()
        self.settings_border_brightness_mult_spin.setRange(0, 200)
        self.settings_border_brightness_mult_spin.setSuffix("%")
        self.settings_border_brightness_mult_spin.setValue(a.settings_border_brightness_multiplier)
        form.addRow("Settings Border Brightness Multiplier:", self.settings_border_brightness_mult_spin)

        # Shared across all three sidebar buttons (see
        # AppearanceSettings.sidebar_border_image_path's own comment for
        # why this one isn't per-button like the multipliers above).
        sidebar_image_row = QHBoxLayout()
        self.sidebar_border_image_edit = CustomLineEdit(a.sidebar_border_image_path)
        self.sidebar_border_image_edit.setPlaceholderText("(use the built-in gradient)")
        sidebar_image_browse = CustomButton("Browse...")
        sidebar_image_browse.clicked.connect(
            lambda: self._browse_border_image(self.sidebar_border_image_edit)
        )
        sidebar_image_row.addWidget(self.sidebar_border_image_edit)
        sidebar_image_row.addWidget(sidebar_image_browse)
        form.addRow("Sidebar Border Image:", sidebar_image_row)

        self.sidebar_border_hue_shift_spin = CustomSpinBox()
        self.sidebar_border_hue_shift_spin.setRange(0, 359)
        self.sidebar_border_hue_shift_spin.setSuffix("\u00b0")
        self.sidebar_border_hue_shift_spin.setValue(a.sidebar_border_hue_shift)
        form.addRow("Sidebar Border Hue Shift:", self.sidebar_border_hue_shift_spin)

        self.settings_border_combo = QComboBox()
        self.settings_border_combo.setStyleSheet(combo_box_stylesheet(a))
        self.settings_border_combo.addItem("Disabled", "disabled")
        self.settings_border_combo.addItem("Only when on the settings page", "only_settings")
        self.settings_border_combo.addItem("Always", "always")
        index = self.settings_border_combo.findData(a.settings_border_mode)
        self.settings_border_combo.setCurrentIndex(index if index >= 0 else 1)
        form.addRow("Border around Settings?", self.settings_border_combo)

        self.unedited_selected_border_width_spin = CustomSpinBox()
        # Range max used to be 50 while the actual default (see
        # config.py) had already been doubled past that several times
        # (72, then 144) -- QSpinBox.setValue() silently CLAMPS an
        # out-of-range value rather than erroring, so every time this
        # page was opened the field silently displayed 50 instead of
        # the real value, and clicking Save (for ANY reason, even an
        # unrelated field) would write that clamped 50 back over the
        # real setting. This is almost certainly the actual bug behind
        # "changing the thickness in the past doesn't seem to have
        # worked" -- found directly by comparing this range against
        # AppearanceSettings' own default, not guessed. Raised well
        # past the current default so the same mistake doesn't quietly
        # recur the next time this value needs to grow.
        self.unedited_selected_border_width_spin.setRange(0, 400)
        self.unedited_selected_border_width_spin.setValue(a.unedited_selected_border_width)
        form.addRow("Unedited/Selected Border Width:", self.unedited_selected_border_width_spin)

        self.unedited_highlight_brightness_spin = CustomSpinBox()
        self.unedited_highlight_brightness_spin.setRange(0, 100)
        self.unedited_highlight_brightness_spin.setSuffix("%")
        self.unedited_highlight_brightness_spin.setValue(a.unedited_highlight_brightness)
        form.addRow("Unedited Highlight Brightness:", self.unedited_highlight_brightness_spin)

        unedited_image_row = QHBoxLayout()
        self.unedited_border_image_edit = CustomLineEdit(a.unedited_border_image_path)
        self.unedited_border_image_edit.setPlaceholderText("(use the built-in gradient)")
        unedited_image_browse = CustomButton("Browse...")
        unedited_image_browse.clicked.connect(
            lambda: self._browse_border_image(self.unedited_border_image_edit)
        )
        unedited_image_row.addWidget(self.unedited_border_image_edit)
        unedited_image_row.addWidget(unedited_image_browse)
        form.addRow("Unedited Border Image:", unedited_image_row)

        self.unedited_border_hue_shift_spin = CustomSpinBox()
        self.unedited_border_hue_shift_spin.setRange(0, 359)
        self.unedited_border_hue_shift_spin.setSuffix("\u00b0")
        self.unedited_border_hue_shift_spin.setValue(a.unedited_border_hue_shift)
        form.addRow("Unedited Border Hue Shift:", self.unedited_border_hue_shift_spin)

        selected_image_row = QHBoxLayout()
        self.selected_border_image_edit = CustomLineEdit(a.selected_border_image_path)
        self.selected_border_image_edit.setPlaceholderText("(use the built-in gold/white gradient)")
        selected_image_browse = CustomButton("Browse...")
        selected_image_browse.clicked.connect(
            lambda: self._browse_border_image(self.selected_border_image_edit)
        )
        selected_image_row.addWidget(self.selected_border_image_edit)
        selected_image_row.addWidget(selected_image_browse)
        form.addRow("Selected Border Image:", selected_image_row)

        self.filter_icon_size_spin = CustomSpinBox()
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
        self.library_icon_size_spin = CustomSpinBox()
        self.library_icon_size_spin.setRange(10, 200)
        self.library_icon_size_spin.setSuffix("%")
        self.library_icon_size_spin.setValue(a.library_icon_size)
        form.addRow("Library Icon Size (sidebar):", self.library_icon_size_spin)

        self.editor_icon_size_spin = CustomSpinBox()
        self.editor_icon_size_spin.setRange(10, 200)
        self.editor_icon_size_spin.setSuffix("%")
        self.editor_icon_size_spin.setValue(a.editor_icon_size)
        form.addRow("Editor Icon Size (sidebar):", self.editor_icon_size_spin)

        self.settings_icon_size_spin = CustomSpinBox()
        self.settings_icon_size_spin.setRange(10, 200)
        self.settings_icon_size_spin.setSuffix("%")
        self.settings_icon_size_spin.setValue(a.settings_icon_size)
        form.addRow("Settings Icon Size (sidebar):", self.settings_icon_size_spin)

        self.saved_videos_icon_size_spin = CustomSpinBox()
        self.saved_videos_icon_size_spin.setRange(10, 200)
        self.saved_videos_icon_size_spin.setSuffix("%")
        self.saved_videos_icon_size_spin.setValue(a.saved_videos_icon_size)
        form.addRow("Saved Videos Icon Size (Library tab):", self.saved_videos_icon_size_spin)

        self.uploaded_videos_icon_size_spin = CustomSpinBox()
        self.uploaded_videos_icon_size_spin.setRange(10, 200)
        self.uploaded_videos_icon_size_spin.setSuffix("%")
        self.uploaded_videos_icon_size_spin.setValue(a.uploaded_videos_icon_size)
        form.addRow("Uploaded Videos Icon Size (Library tab):", self.uploaded_videos_icon_size_spin)

        # A combo box rather than a checkbox pair (Fullscreen + Maximized)
        # deliberately -- two independent checkboxes could both end up
        # checked at once, which has no sensible meaning. Moved here from
        # Clipping since it's a startup-appearance choice.
        self.startup_window_mode_combo = QComboBox()
        self.startup_window_mode_combo.setStyleSheet(combo_box_stylesheet(a))
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

    # ------------------------------------------------------------ Afterglow Theme group

    def _build_afterglow_theme_group(self) -> CustomGroupBox:
        group = CustomGroupBox("Afterglow Theme")
        form = group.make_layout(QFormLayout)
        a = self._settings.appearance

        accent_row, self.afterglow_accent_edit = self._build_color_row(a.afterglow_color_accent)
        form.addRow("Accent (buttons, card info box):", accent_row)

        card_bg_row, self.afterglow_card_bg_edit = self._build_color_row(a.afterglow_color_card_background)
        form.addRow("Card Background:", card_bg_row)

        app_bg_row, self.afterglow_app_bg_edit = self._build_color_row(a.afterglow_color_app_background)
        form.addRow("App Background:", app_bg_row)

        library_row, self.afterglow_library_edit = self._build_color_row(a.afterglow_color_library)
        form.addRow("Library Pages:", library_row)

        turquoise_row, self.afterglow_turquoise_edit = self._build_color_row(a.afterglow_color_turquoise)
        form.addRow("Local/Uploaded Tab Icons:", turquoise_row)

        note = QLabel(
            "These only take effect when both Custom Buttons and Afterglow "
            "Theme are on (Settings > General)."
        )
        note.setWordWrap(True)
        form.addRow(note)

        return group

    def _build_reset_group(self) -> CustomGroupBox:
        """Recovery tools for exactly the class of problem that's come
        up repeatedly: a color or setting default changes in a new
        build, but an already-saved config keeps showing the OLD
        value, and it can look indistinguishable from a real rendering
        bug from the outside. Added directly on Max's request --
        "before assuming there is a bug" -- as the first thing to try."""
        group = CustomGroupBox("Reset")
        layout = group.make_layout(QVBoxLayout)

        colors_btn = CustomButton("Revert to Default Colors")
        colors_btn.clicked.connect(self._revert_default_colors)
        layout.addWidget(colors_btn)

        settings_btn = CustomButton("Revert to Default Settings")
        settings_btn.clicked.connect(self._revert_default_settings)
        layout.addWidget(settings_btn)

        note = QLabel(
            "\"Revert to Default Colors\" resets just the Afterglow Theme "
            "and card-text colors above. \"Revert to Default Settings\" "
            "resets every appearance setting (padding, borders, rounding, "
            "etc., in addition to colors) -- reopen Settings afterward to "
            "see the reset values reflected everywhere."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        return group

    def _build_import_export_group(self) -> CustomGroupBox:
        """Export the ENTIRE current config (not just appearance) to an
        arbitrary file, or replace it wholesale from one -- per Max's
        request, for handing a "new default settings" file back to a
        future Claude session rather than describing every field by
        hand. Distinct from the Reset group above: Reset always goes to
        this BUILD's own code defaults; Import/Export moves a real,
        specific config file in and out of the app."""
        group = CustomGroupBox("Import / Export Settings")
        layout = group.make_layout(QVBoxLayout)

        export_btn = CustomButton("Export Settings...")
        export_btn.clicked.connect(self._export_settings)
        layout.addWidget(export_btn)

        import_btn = CustomButton("Import Settings...")
        import_btn.clicked.connect(self._import_settings)
        layout.addWidget(import_btn)

        note = QLabel(
            "Exports/imports the whole config file (all tabs, not just "
            "Advanced) as a single .toml file. Importing replaces every "
            "current setting and reopens this page to reflect it."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        return group

    def _build_performance_group(self) -> CustomGroupBox:
        group = CustomGroupBox("Performance")
        layout = group.make_layout(QVBoxLayout)

        self.offload_scan_checkbox = CustomCheckBox("Offload library scanning to the background daemon")
        self.offload_scan_checkbox.setChecked(self._settings.offload_library_scan_to_daemon)
        self.offload_scan_checkbox.toggled.connect(self._save)
        layout.addWidget(self.offload_scan_checkbox)

        note = QLabel(
            "When on, the app itself skips scanning the clips folder and "
            "cleaning up the library on every open/refresh -- the background "
            "daemon does that continuously instead (it's already running for "
            "hotkey capture), and the app just reads whatever it's already "
            "found. Fewer redundant filesystem scans overall. Takes effect "
            "immediately, no restart needed for either the app or the daemon."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.auto_copy_mp4_checkbox = CustomCheckBox("Auto Copy as MP4")
        self.auto_copy_mp4_checkbox.setChecked(self._settings.auto_copy_as_mp4)
        self.auto_copy_mp4_checkbox.toggled.connect(self._save)
        layout.addWidget(self.auto_copy_mp4_checkbox)

        mp4_note = QLabel(
            "When on, Copying a video whose file isn't already .mp4 puts a "
            ".mp4-named copy on the clipboard instead of the original "
            "extension -- a pure rename via a fresh copy, not a remux or "
            "re-encode of any kind. The original file in the library is "
            "never touched."
        )
        mp4_note.setWordWrap(True)
        layout.addWidget(mp4_note)
        return group

    def _export_settings(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Settings", "afterglow-settings.toml", "TOML Files (*.toml)"
        )
        if not path:
            return
        try:
            config_module.export_to_file(path)
        except OSError as exc:
            show_message(self, "Export Failed", f"Could not write settings to {path}:\n{exc}")

    def _import_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Settings", "", "TOML Files (*.toml)")
        if not path:
            return
        try:
            config_module.import_from_file(path)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            show_message(self, "Import Failed", f"Could not read settings from {path}:\n{exc}")
            return
        show_message(
            self, "Settings Imported",
            "Settings were imported successfully. Reopen Settings to see the new values everywhere.",
        )

    def _revert_default_colors(self) -> None:
        defaults = config_module.AppearanceSettings()
        self.afterglow_accent_edit.setText(defaults.afterglow_color_accent)
        self.afterglow_card_bg_edit.setText(defaults.afterglow_color_card_background)
        self.afterglow_app_bg_edit.setText(defaults.afterglow_color_app_background)
        self.afterglow_library_edit.setText(defaults.afterglow_color_library)
        self.afterglow_turquoise_edit.setText(defaults.afterglow_color_turquoise)
        self.card_text_color_edit.setText(defaults.card_text_color)
        self.card_text_outline_color_edit.setText(defaults.card_text_outline_color)
        self._save()

    def _revert_default_settings(self) -> None:
        settings = config_module.load()
        settings.appearance = config_module.AppearanceSettings()
        config_module.save(settings)
        self._settings = settings
        show_message(
            self, "Reverted",
            "All appearance settings have been reset to their defaults. "
            "Reopen Settings to see the reset values reflected in this page.",
        )

    def _build_color_row(self, initial_hex: str) -> tuple[QHBoxLayout, QLineEdit]:
        row = QHBoxLayout()
        edit = CustomLineEdit(initial_hex)
        edit.setMaxLength(9)  # "#RRGGBB" or "#AARRGGBB"
        pick_btn = CustomButton("Pick...")
        pick_btn.clicked.connect(lambda: self._pick_color(edit))
        row.addWidget(edit)
        row.addWidget(pick_btn)
        return row, edit

    def _pick_color(self, edit: QLineEdit) -> None:
        current = QColor(edit.text().strip())
        if not current.isValid():
            current = QColor("#ffffff")
        color = QColorDialog.getColor(current, self, "Choose Color")
        if color.isValid():
            edit.setText(color.name())

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

    def _build_clip_options_group(self) -> CustomGroupBox:
        group = CustomGroupBox("Clip Options")
        outer = group.make_layout(QVBoxLayout)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.addStretch(1)
        scroll.setWidget(self.rows_container)
        outer.addWidget(scroll, stretch=1)

        add_btn = CustomButton("+ Add Clip Option")
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
                show_message(self, "Invalid Clip Option", "Every clip option needs a name.")
                return
            key = fields["name"].lower()
            if key in seen_names:
                show_message(
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
        self._settings.offload_library_scan_to_daemon = self.offload_scan_checkbox.isChecked()
        self._settings.auto_copy_as_mp4 = self.auto_copy_mp4_checkbox.isChecked()

        a = self._settings.appearance
        a.rounded_corners_enabled = self.rounded_corners_check.isChecked()
        a.rounded_corner_radius = self.rounded_corner_radius_spin.value()
        a.custom_buttons_enabled = self.custom_buttons_check.isChecked()
        a.afterglow_theme_enabled = self.afterglow_theme_check.isChecked()
        a.ui_padding = self.ui_padding_spin.value()
        a.filter_outline_enabled = self.filter_outline_check.isChecked()
        a.resize_text_to_fit = self.resize_text_check.isChecked()
        a.extended_dates = self.extended_dates_check.isChecked()
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

        for label, edit in (
            ("Accent", self.afterglow_accent_edit),
            ("Card Background", self.afterglow_card_bg_edit),
            ("App Background", self.afterglow_app_bg_edit),
            ("Library Pages", self.afterglow_library_edit),
            ("Local/Uploaded Tab Icons", self.afterglow_turquoise_edit),
            ("Card Text Color", self.card_text_color_edit),
            ("Card Text Outline Color", self.card_text_outline_color_edit),
        ):
            if not QColor(edit.text().strip()).isValid():
                show_message(
                    self, "Invalid Color",
                    f"'{edit.text()}' isn't a valid color for {label} -- "
                    f"use a hex code like #257fff.",
                )
                return
        a.afterglow_color_accent = self.afterglow_accent_edit.text().strip()
        a.afterglow_color_card_background = self.afterglow_card_bg_edit.text().strip()
        a.afterglow_color_app_background = self.afterglow_app_bg_edit.text().strip()
        a.afterglow_color_library = self.afterglow_library_edit.text().strip()
        a.afterglow_color_turquoise = self.afterglow_turquoise_edit.text().strip()
        a.card_text_color = self.card_text_color_edit.text().strip()
        a.card_text_outline_color = self.card_text_outline_color_edit.text().strip()
        a.card_text_outline_width = self.card_text_outline_width_spin.value()

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
            show_message(self, "Save Failed", str(e))
            return

        self.status_label.setText("Saved.")

    def refresh_dynamic_lists(self) -> None:
        """Passthrough to the Filters sub-page -- see its own docstring
        for why this is needed (called by MainWindow on Settings nav)."""
        self.filters_settings_page.refresh_dynamic_lists()
