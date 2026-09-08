"""
Settings > Filters: everything about how tags/filters are displayed and
auto-applied, split from the main SettingsPage (settings_page.py) since
it's a self-contained cluster of controls with its own save/load needs.

Three groups:
  - Tag icons: assign/clear an icon per tag, and rename a tag (moved
    here from a right-click-in-the-dropdown interaction -- see this
    module's note on FilterCheckBox in library_page.py for why).
  - Display: the three global display toggles/dropdown that control how
    those icons (and filter names) show up above library thumbnails.
  - Auto Add Filter: rules that auto-tag newly captured clips based on
    which application is open/focused at capture time (see
    autofilter.py for the detection side; this page only edits the
    rule list itself).
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, QPushButton,
    QFileDialog, QComboBox, QCheckBox, QScrollArea, QFrame, QLineEdit,
    QMessageBox, QTabWidget, QFormLayout,
)

from .. import library
from .. import config as config_module

_ICON_LOCATIONS = [
    ("Above", "above"),
    ("Below", "below"),
    ("Vertical Tiling Left", "vtile_left"),
    ("Vertical Tiling Right", "vtile_right"),
]


class _TagIconRow(QFrame):
    def __init__(self, tag_id: int, tag_name: str, icon_path: str | None, parent=None):
        super().__init__(parent)
        self.tag_id = tag_id
        self.tag_name = tag_name
        self.icon_path = icon_path or ""

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)

        self.name_label = QLabel(tag_name)
        row.addWidget(self.name_label, stretch=1)

        self.icon_preview = QLabel(self._icon_summary())
        self.icon_preview.setStyleSheet("color: gray;")
        row.addWidget(self.icon_preview)

        set_icon_btn = QPushButton("Set Icon...")
        set_icon_btn.clicked.connect(self._browse_icon)
        row.addWidget(set_icon_btn)

        clear_btn = QPushButton("Clear Icon")
        clear_btn.clicked.connect(self._clear_icon)
        row.addWidget(clear_btn)

        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._rename)
        row.addWidget(rename_btn)

    def _icon_summary(self) -> str:
        return self.icon_path.rsplit("/", 1)[-1] if self.icon_path else "(no icon)"

    def _browse_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Icon", "", "Images (*.png *.jpg *.jpeg *.svg *.ico);;All Files (*)"
        )
        if path:
            self.icon_path = path
            self.icon_preview.setText(self._icon_summary())

    def _clear_icon(self) -> None:
        self.icon_path = ""
        self.icon_preview.setText(self._icon_summary())

    def _rename(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(self, "Rename Filter", "Filter name:", text=self.tag_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == self.tag_name:
            return
        try:
            library.rename_tag(self.tag_id, new_name)
        except library.LibraryError as e:
            QMessageBox.warning(self, "Rename Failed", str(e))
            return
        self.tag_name = new_name
        self.name_label.setText(new_name)


class _AutoFilterRow(QFrame):
    def __init__(self, tag_name: str = "", app_match: str = "", mode: str = "open", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)

        self.tag_edit = QLineEdit(tag_name)
        self.tag_edit.setPlaceholderText("Filter name")
        row.addWidget(self.tag_edit, stretch=1)

        self.app_edit = QLineEdit(app_match)
        self.app_edit.setPlaceholderText("App/process name match")
        row.addWidget(self.app_edit, stretch=1)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("While app is open", "open")
        self.mode_combo.addItem("Only while app is focused", "focused")
        index = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(index if index >= 0 else 0)
        row.addWidget(self.mode_combo)

        self.remove_btn = QPushButton("Remove")
        row.addWidget(self.remove_btn)

    def to_rule(self) -> config_module.AutoFilterRule:
        return config_module.AutoFilterRule(
            tag_name=self.tag_edit.text().strip(),
            app_match=self.app_edit.text().strip(),
            mode=self.mode_combo.currentData(),
        )


class FiltersSettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = config_module.load()
        self._tag_rows: list[_TagIconRow] = []
        self._auto_rows: list[_AutoFilterRow] = []

        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)

        tabs.addTab(self._build_filters_tab(), "Filters")
        tabs.addTab(self._build_auto_filter_tab(), "Auto Add Filter")

    # ------------------------------------------------------------ Filters tab

    def _build_filters_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        display_group = QGroupBox("Display")
        form = QFormLayout(display_group)

        self.show_names_check = QCheckBox()
        self.show_names_check.setChecked(self._settings.filter_display.show_filter_names)
        form.addRow("Show Filter Names under Clips:", self.show_names_check)

        self.show_icons_check = QCheckBox()
        self.show_icons_check.setChecked(self._settings.filter_display.show_filter_icons)
        form.addRow("Show Filter Icons:", self.show_icons_check)

        self.icon_location_combo = QComboBox()
        for label, value in _ICON_LOCATIONS:
            self.icon_location_combo.addItem(label, value)
        index = self.icon_location_combo.findData(self._settings.filter_display.filter_icon_location)
        self.icon_location_combo.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Filter Icons Location:", self.icon_location_combo)

        layout.addWidget(display_group)

        icons_group = QGroupBox("Tag Icons")
        icons_layout = QVBoxLayout(icons_group)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        rows_container = QWidget()
        self.rows_layout = QVBoxLayout(rows_container)
        self.rows_layout.addStretch(1)
        scroll.setWidget(rows_container)
        icons_layout.addWidget(scroll, stretch=1)
        layout.addWidget(icons_group, stretch=1)

        self._reload_tag_rows()
        return page

    def _reload_tag_rows(self) -> None:
        for row in self._tag_rows:
            row.setParent(None)
            row.deleteLater()
        self._tag_rows = []
        icons = library.tag_icons()
        for tag_id, tag_name in library.all_tags_with_ids():
            row = _TagIconRow(tag_id, tag_name, icons.get(tag_name))
            self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
            self._tag_rows.append(row)

    # ------------------------------------------------------------ Auto Add Filter tab

    def _build_auto_filter_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        info = QLabel(
            "Automatically apply a filter to newly captured clips based on "
            "which application is running. \"While app is open\" applies "
            "whenever a matching process is running at all; \"Only while "
            "app is focused\" applies only if that application had focus "
            "at the moment of capture."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: gray;")
        layout.addWidget(info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        rows_container = QWidget()
        self.auto_rows_layout = QVBoxLayout(rows_container)
        self.auto_rows_layout.addStretch(1)
        scroll.setWidget(rows_container)
        layout.addWidget(scroll, stretch=1)

        add_btn = QPushButton("+ Add Rule")
        add_btn.clicked.connect(lambda: self._add_auto_row())
        layout.addWidget(add_btn)

        for rule in self._settings.auto_filters:
            self._add_auto_row(rule.tag_name, rule.app_match, rule.mode)

        return page

    def _add_auto_row(self, tag_name: str = "", app_match: str = "", mode: str = "open") -> None:
        row = _AutoFilterRow(tag_name, app_match, mode)
        row.remove_btn.clicked.connect(lambda: self._remove_auto_row(row))
        self.auto_rows_layout.insertWidget(self.auto_rows_layout.count() - 1, row)
        self._auto_rows.append(row)

    def _remove_auto_row(self, row: "_AutoFilterRow") -> None:
        self._auto_rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    # ------------------------------------------------------------ save

    def save(self) -> None:
        """Called by SettingsPage's own Save button -- this page has no
        separate save button of its own, to keep one consistent "Save
        Settings" action for the whole Settings area."""
        settings = config_module.load()
        settings.filter_display.show_filter_names = self.show_names_check.isChecked()
        settings.filter_display.show_filter_icons = self.show_icons_check.isChecked()
        settings.filter_display.filter_icon_location = self.icon_location_combo.currentData()
        settings.auto_filters = [
            row.to_rule() for row in self._auto_rows if row.to_rule().tag_name
        ]
        config_module.save(settings)

        for row in self._tag_rows:
            library.set_tag_icon(row.tag_id, row.icon_path or None)

        self._settings = settings
