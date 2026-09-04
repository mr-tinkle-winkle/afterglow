"""
Main window: left sidebar nav across the 3 pages -- Settings, Library,
Editor. All three are live now (no more "coming soon" placeholders).

Each page is constructed once here and lives inside the QStackedWidget for
the app's whole lifetime; switching nav only changes which one is visible.
This is also what makes the Editor page's "remember what I was last
editing" requirement work for free -- see editor_page.py's docstring.

The sidebar is icon-only: Library and Editor are the two primary nav
buttons (stretched to fill most of the sidebar's height, so they scale
with the window), with Settings demoted to a small gear button pinned to
the bottom rather than a third nav-list entry -- Library is the page
you land on and return to, Settings is something you dip into
occasionally.
"""
from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QToolButton,
    QButtonGroup, QStackedWidget, QSizePolicy,
)

from .settings_page import SettingsPage
from .library_page import LibraryPage
from .editor_page import EditorPage
from .resources import resource_qicon

# Indices into self.stack -- fixed at construction time (see __init__).
_SETTINGS_INDEX = 0
_LIBRARY_INDEX = 1
_EDITOR_INDEX = 2


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("afterglow")
        self.resize(1000, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)

        # ---- sidebar: Library + Editor (icon-only, fill the height),
        # gear (Settings) pinned to the bottom ----
        sidebar = QWidget()
        sidebar.setFixedWidth(72)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        sidebar_layout.setSpacing(4)
        layout.addWidget(sidebar)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.library_nav_btn = self._make_nav_button("library.png", "Library")
        self.editor_nav_btn = self._make_nav_button("editor.png", "Editor")
        self.settings_nav_btn = self._make_nav_button("settings.png", "Settings")

        # Library and Editor stretch to fill most of the sidebar's
        # vertical space; the gear stays a fixed small size at the bottom.
        sidebar_layout.addWidget(self.library_nav_btn, stretch=1)
        sidebar_layout.addWidget(self.editor_nav_btn, stretch=1)
        sidebar_layout.addStretch(0)
        self.settings_nav_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.settings_nav_btn.setFixedHeight(48)
        sidebar_layout.addWidget(self.settings_nav_btn)

        self.nav_group.addButton(self.library_nav_btn, _LIBRARY_INDEX)
        self.nav_group.addButton(self.editor_nav_btn, _EDITOR_INDEX)
        self.nav_group.addButton(self.settings_nav_btn, _SETTINGS_INDEX)
        self.nav_group.idClicked.connect(self._on_nav_clicked)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        self.settings_page = SettingsPage()
        self.library_page = LibraryPage()
        self.editor_page = EditorPage()

        # Insertion order must match _SETTINGS_INDEX / _LIBRARY_INDEX /
        # _EDITOR_INDEX above.
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.library_page)
        self.stack.addWidget(self.editor_page)

        # Double-click / context-menu "Edit" in the Library routes here to
        # the Editor page (and loads that video into it).
        self.library_page.edit_requested.connect(self._open_in_editor)

        self.library_nav_btn.setChecked(True)
        self.stack.setCurrentIndex(_LIBRARY_INDEX)

    def _make_nav_button(self, icon_name: str, tooltip: str) -> QToolButton:
        btn = QToolButton()
        btn.setIcon(resource_qicon(icon_name))
        btn.setIconSize(QSize(32, 32))
        btn.setToolTip(tooltip)
        btn.setCheckable(True)
        btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        btn.setAutoRaise(True)
        return btn

    def _on_nav_clicked(self, index: int) -> None:
        if index == _EDITOR_INDEX and self.editor_page.current_video_id is None:
            # Nothing has ever been loaded into the Editor -- go to the
            # Library instead and prompt there, rather than showing the
            # Editor's own empty state. Re-check Library so the nav
            # buttons stay in sync with what's actually on screen.
            self.library_nav_btn.setChecked(True)
            index = _LIBRARY_INDEX
            self.library_page.show_status_message("Select a video.")

        self.stack.setCurrentIndex(index)
        # Library reflects any edits/deletes made from the Editor page
        # (e.g. an Undo changing has_edit, or a delete elsewhere) whenever
        # it's navigated back to, rather than needing a manual refresh.
        if index == _LIBRARY_INDEX:
            self.library_page.refresh()

    def _open_in_editor(self, video_id: int) -> None:
        self.editor_page.load_video(video_id)
        self.editor_nav_btn.setChecked(True)
        self.stack.setCurrentIndex(_EDITOR_INDEX)
