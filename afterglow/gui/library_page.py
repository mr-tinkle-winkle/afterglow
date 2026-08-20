"""
The Library page: two sub-tabs (Local / Uploaded), each with a search bar,
a Filters dropdown (multi-select over all known tags, ANDed), and a
thumbnail grid of matching videos.

Local and Uploaded share almost all of their behavior (same search/filter/
grid mechanics), differing only in which videos they show (local_only vs
uploaded_only) and that Uploaded has no real content yet since YouTube
upload isn't implemented -- so both are built from one reusable
_VideoGridTab, parameterized by that filter, rather than duplicating the
grid/search/filter wiring twice.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QToolButton,
    QMenu, QScrollArea, QLabel, QTabWidget, QMessageBox, QWidgetAction,
    QCheckBox,
)

from .. import library
from .video_card import VideoCard

GRID_COLUMNS = 4


class _VideoGridTab(QWidget):
    edit_requested = Signal(int)

    def __init__(self, uploaded_only: bool, local_only: bool, parent=None):
        super().__init__(parent)
        self._uploaded_only = uploaded_only
        self._local_only = local_only
        self._active_tags: set[str] = set()

        outer = QVBoxLayout(self)

        # ---- search + filters row ----
        top_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search title or description...")
        self.search_edit.textChanged.connect(self.refresh)
        top_row.addWidget(self.search_edit, stretch=1)

        self.filters_btn = QToolButton()
        self.filters_btn.setText("Filters")
        self.filters_btn.setPopupMode(QToolButton.InstantPopup)
        top_row.addWidget(self.filters_btn)
        outer.addLayout(top_row)

        # ---- grid ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.scroll.setWidget(self.grid_container)
        outer.addWidget(self.scroll, stretch=1)

        self.empty_label = QLabel("No clips yet.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.empty_label)

        self.refresh()

    # ------------------------------------------------------------ filters menu

    def _rebuild_filters_menu(self) -> None:
        menu = QMenu(self.filters_btn)
        all_tags = library.all_known_tags()
        if not all_tags:
            no_tags_action = menu.addAction("(no tags yet)")
            no_tags_action.setEnabled(False)
        for tag in all_tags:
            checkbox = QCheckBox(tag, menu)
            checkbox.setChecked(tag in self._active_tags)
            checkbox.toggled.connect(lambda checked, t=tag: self._toggle_tag(t, checked))
            action = QWidgetAction(menu)
            action.setDefaultWidget(checkbox)
            menu.addAction(action)
        self.filters_btn.setMenu(menu)

    def _toggle_tag(self, tag: str, checked: bool) -> None:
        if checked:
            self._active_tags.add(tag)
        else:
            self._active_tags.discard(tag)
        self.refresh()

    # ------------------------------------------------------------ grid rendering

    def refresh(self) -> None:
        self._rebuild_filters_menu()

        # Clear existing cards.
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        videos = library.list_videos(
            tag_filter=list(self._active_tags) or None,
            uploaded_only=self._uploaded_only,
            local_only=self._local_only,
            search=self.search_edit.text().strip() or None,
        )

        self.empty_label.setVisible(len(videos) == 0)
        self.scroll.setVisible(len(videos) > 0)

        for index, video in enumerate(videos):
            card = VideoCard(video)
            card.edit_requested.connect(self.edit_requested.emit)
            card.deleted.connect(lambda _vid: self.refresh())
            card.tags_changed.connect(self.refresh)
            card.upload_requested.connect(self._handle_upload_request)
            row, col = divmod(index, GRID_COLUMNS)
            self.grid_layout.addWidget(card, row, col)

    def _handle_upload_request(self, video_id: int) -> None:
        QMessageBox.information(
            self, "Not Implemented Yet",
            "YouTube upload is coming in the next build phase (OAuth setup "
            "isn't wired up yet).",
        )


class LibraryPage(QWidget):
    edit_requested = Signal(int)  # bubbled up from either tab, for MainWindow to route to Editor

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self.local_tab = _VideoGridTab(uploaded_only=False, local_only=True)
        self.uploaded_tab = _VideoGridTab(uploaded_only=True, local_only=False)
        self.local_tab.edit_requested.connect(self.edit_requested.emit)
        self.uploaded_tab.edit_requested.connect(self.edit_requested.emit)

        self.tabs.addTab(self.local_tab, "Local")
        self.tabs.addTab(self.uploaded_tab, "Uploaded")
        layout.addWidget(self.tabs)

    def refresh(self) -> None:
        """Called by MainWindow whenever the Library page becomes visible,
        so edits/deletes made from the Editor page are reflected."""
        self.local_tab.refresh()
        self.uploaded_tab.refresh()
