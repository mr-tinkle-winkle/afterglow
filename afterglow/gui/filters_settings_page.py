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
    QMessageBox, QTabWidget, QFormLayout, QInputDialog, QToolButton,
    QMenu, QWidgetAction,
)

from .. import library
from .. import config as config_module
from .. import autofilter

_ICON_LOCATIONS = [
    ("Above", "above"),
    ("Below", "below"),
    ("Vertical Tiling Left", "vtile_left"),
    ("Vertical Tiling Right", "vtile_right"),
]

_NEW_CATEGORY_DATA = "__new_category__"


class _TagIconRow(QFrame):
    def __init__(self, tag_id: int, tag_name: str, icon_path: str | None,
                 category_id: int | None, categories: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.tag_id = tag_id
        self.tag_name = tag_name
        self.icon_path = icon_path or ""

        row = QHBoxLayout(self)
        row.setContentsMargins(2, 2, 2, 2)

        self.name_label = QLabel(tag_name)
        row.addWidget(self.name_label, stretch=1)

        self.category_combo = QComboBox()
        self._populate_category_combo(categories, category_id)
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        row.addWidget(self.category_combo)

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

    def _populate_category_combo(self, categories: list[tuple[int, str]], selected_id: int | None) -> None:
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("(no category)", None)
        for cat_id, cat_name in categories:
            self.category_combo.addItem(cat_name, cat_id)
        self.category_combo.addItem("+ New Category...", _NEW_CATEGORY_DATA)
        index = self.category_combo.findData(selected_id)
        self.category_combo.setCurrentIndex(index if index >= 0 else 0)
        self.category_combo.blockSignals(False)

    def _on_category_changed(self, _index: int) -> None:
        data = self.category_combo.currentData()
        if data == _NEW_CATEGORY_DATA:
            name, ok = QInputDialog.getText(self, "New Category", "Category name:")
            name = name.strip()
            if not ok or not name:
                # Revert to "(no category)" rather than leaving the
                # "+ New Category..." entry selected.
                self.category_combo.setCurrentIndex(0)
                return
            new_id = library.create_category(name)
            library.set_tag_category(self.tag_id, new_id)
            self._populate_category_combo(library.all_categories(), new_id)
        else:
            library.set_tag_category(self.tag_id, data)

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


class _MultiFilterSelectButton(QToolButton):
    """A dropdown of every existing filter, each with its own checkbox,
    letting an Auto Add Filter rule apply several filters at once (the
    "filter(s) of choice" for that rule) -- replaces a single free-text
    filter-name field."""

    def __init__(self, selected: list[str], parent=None):
        super().__init__(parent)
        self.setPopupMode(QToolButton.InstantPopup)
        self._selected: list[str] = list(selected)
        self.refresh_options()

    def refresh_options(self) -> None:
        menu = QMenu(self)
        all_tags = library.all_known_tags()
        if not all_tags:
            action = menu.addAction("(no filters yet)")
            action.setEnabled(False)
        for tag in all_tags:
            checkbox = QCheckBox(tag, menu)
            checkbox.setChecked(tag in self._selected)
            checkbox.toggled.connect(lambda checked, t=tag: self._toggle(t, checked))
            action = QWidgetAction(menu)
            action.setDefaultWidget(checkbox)
            menu.addAction(action)
        self.setMenu(menu)
        self._update_text()

    def _toggle(self, tag: str, checked: bool) -> None:
        if checked and tag not in self._selected:
            self._selected.append(tag)
        elif not checked and tag in self._selected:
            self._selected.remove(tag)
        self._update_text()

    def _update_text(self) -> None:
        self.setText(", ".join(self._selected) if self._selected else "Choose filter(s)...")

    @property
    def selected_tags(self) -> list[str]:
        return list(self._selected)


class _AutoFilterRow(QFrame):
    def __init__(self, tag_names: list[str] | None = None, app_match: str = "",
                 mode: str = "open", parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)

        self.filter_select = _MultiFilterSelectButton(tag_names or [])
        row.addWidget(self.filter_select, stretch=1)

        # Editable combo, not a plain text field: it doubles as a
        # dropdown of currently-running process names (so a match
        # string can be picked with a click instead of typed blind) and
        # a normal typable box, since the app you want to match might
        # not be running yet when this row is being set up.
        self.app_combo = QComboBox()
        self.app_combo.setEditable(True)
        self.app_combo.setPlaceholderText("App/process name match")
        self._refresh_running_apps(keep_text=app_match)
        row.addWidget(self.app_combo, stretch=1)

        refresh_apps_btn = QPushButton("\u21bb")  # "↻"
        refresh_apps_btn.setToolTip("Refresh running app list")
        refresh_apps_btn.setFixedWidth(28)
        refresh_apps_btn.clicked.connect(lambda: self._refresh_running_apps())
        row.addWidget(refresh_apps_btn)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("While app is open", "open")
        self.mode_combo.addItem("Only while app is focused", "focused")
        index = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(index if index >= 0 else 0)
        row.addWidget(self.mode_combo)

        self.remove_btn = QPushButton("Remove")
        row.addWidget(self.remove_btn)

    def _refresh_running_apps(self, keep_text: str | None = None) -> None:
        current_text = keep_text if keep_text is not None else self.app_combo.currentText()
        self.app_combo.clear()
        self.app_combo.addItems(autofilter.list_running_process_display_names())
        self.app_combo.setCurrentText(current_text)  # still typable -- doesn't have to match a list entry

    def to_rule(self) -> config_module.AutoFilterRule:
        return config_module.AutoFilterRule(
            tag_names=self.filter_select.selected_tags,
            app_match=self.app_combo.currentText().strip(),
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
        category_ids = library.tag_category_ids()
        categories = library.all_categories()
        for tag_id, tag_name in library.all_tags_with_ids():
            row = _TagIconRow(
                tag_id, tag_name, icons.get(tag_name), category_ids.get(tag_id), categories,
            )
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
            self._add_auto_row(rule.tag_names, rule.app_match, rule.mode)

        return page

    def _add_auto_row(self, tag_names: list[str] | None = None, app_match: str = "", mode: str = "open") -> None:
        row = _AutoFilterRow(tag_names, app_match, mode)
        row.remove_btn.clicked.connect(lambda: self._remove_auto_row(row))
        self.auto_rows_layout.insertWidget(self.auto_rows_layout.count() - 1, row)
        self._auto_rows.append(row)

    def _remove_auto_row(self, row: "_AutoFilterRow") -> None:
        self._auto_rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def refresh_dynamic_lists(self) -> None:
        """Re-reads the current set of filters from the DB -- called
        whenever Settings becomes visible again (see MainWindow), since
        this page (like the others) is built once at startup and would
        otherwise keep showing whatever filters existed at launch time
        in the tag-icon list and the Auto Add Filter multi-select
        dropdowns."""
        self._reload_tag_rows()
        for row in self._auto_rows:
            row.filter_select.refresh_options()

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
            row.to_rule() for row in self._auto_rows if row.to_rule().tag_names
        ]
        config_module.save(settings)

        for row in self._tag_rows:
            library.set_tag_icon(row.tag_id, row.icon_path or None)

        self._settings = settings
