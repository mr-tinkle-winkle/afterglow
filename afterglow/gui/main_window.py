"""
Main window: left sidebar nav across the 3 pages -- Settings, Library,
Editor. All three are live now (no more "coming soon" placeholders).

Each page is constructed once here and lives inside the QStackedWidget for
the app's whole lifetime; switching nav only changes which one is visible.
This is also what makes the Editor page's "remember what I was last
editing" requirement work for free -- see editor_page.py's docstring.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QListWidget, QListWidgetItem,
    QStackedWidget,
)

from .settings_page import SettingsPage
from .library_page import LibraryPage
from .editor_page import EditorPage


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("afterglow")
        self.resize(1000, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)

        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(140)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self.nav_list)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        self.settings_page = SettingsPage()
        self.library_page = LibraryPage()
        self.editor_page = EditorPage()

        self._add_page("Settings", self.settings_page)
        self._add_page("Library", self.library_page)
        self._add_page("Editor", self.editor_page)

        # Double-click / context-menu "Edit" in the Library routes here to
        # the Editor page (and loads that video into it).
        self.library_page.edit_requested.connect(self._open_in_editor)

        self.nav_list.setCurrentRow(0)

    def _add_page(self, name: str, widget: QWidget) -> None:
        item = QListWidgetItem(name)
        self.nav_list.addItem(item)
        self.stack.addWidget(widget)

    def _on_nav_changed(self, index: int) -> None:
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        # Library reflects any edits/deletes made from the Editor page
        # (e.g. an Undo changing has_edit, or a delete elsewhere) whenever
        # it's navigated back to, rather than needing a manual refresh.
        if self.stack.widget(index) is self.library_page:
            self.library_page.refresh()

    def _open_in_editor(self, video_id: int) -> None:
        self.editor_page.load_video(video_id)
        self.nav_list.setCurrentRow(2)  # Editor tab index
