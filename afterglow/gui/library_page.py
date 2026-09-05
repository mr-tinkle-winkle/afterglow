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

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QToolButton,
    QMenu, QScrollArea, QLabel, QTabWidget, QMessageBox, QWidgetAction,
    QCheckBox, QStyle,
)

from .. import library
from .video_card import VideoCard, THUMB_SIZE
from .resources import resource_qicon

# Approximate on-screen width of one card (thumbnail + its own internal
# margins + the grid's inter-column spacing) -- used only to decide how
# many columns currently fit, not as an exact pixel layout.
_APPROX_CARD_WIDTH = THUMB_SIZE.width() + 24


class _VideoGridTab(QWidget):
    edit_requested = Signal(int)

    def __init__(self, uploaded_only: bool, local_only: bool, parent=None):
        super().__init__(parent)
        self._uploaded_only = uploaded_only
        self._local_only = local_only
        self._active_tags: set[str] = set()
        # Persisted across resizes so a window resize can just re-flow
        # the existing cards into a new column count instead of
        # re-querying the DB and rebuilding every VideoCard from scratch
        # (which refresh() -- called on actual data changes -- still
        # does).
        self._cards: list[VideoCard] = []
        self._current_columns = 1

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

        # Clear existing cards -- data may have changed (new/deleted
        # video, rename, tag change), so these are rebuilt from scratch
        # rather than reused. Resizing (_relayout below) is the cheaper
        # path that doesn't hit this.
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._cards = []

        videos = library.list_videos(
            tag_filter=list(self._active_tags) or None,
            uploaded_only=self._uploaded_only,
            local_only=self._local_only,
            search=self.search_edit.text().strip() or None,
        )

        self.empty_label.setVisible(len(videos) == 0)
        self.scroll.setVisible(len(videos) > 0)

        for video in videos:
            card = VideoCard(video)
            card.edit_requested.connect(self.edit_requested.emit)
            card.deleted.connect(lambda _vid: self.refresh())
            card.tags_changed.connect(self.refresh)
            card.renamed.connect(self.refresh)
            card.upload_requested.connect(self._handle_upload_request)
            self._cards.append(card)

        self._relayout(self._columns_for_width(self.scroll.viewport().width()))

    def _columns_for_width(self, width: int) -> int:
        return max(1, width // _APPROX_CARD_WIDTH)

    def _relayout(self, columns: int) -> None:
        """Re-flow the existing card widgets into `columns` columns,
        without recreating or re-querying them. Cheap enough to call on
        every resize that actually changes the column count."""
        for card in self._cards:
            self.grid_layout.removeWidget(card)
        for index, card in enumerate(self._cards):
            row, col = divmod(index, columns)
            self.grid_layout.addWidget(card, row, col, Qt.AlignTop)
        self._current_columns = columns

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        columns = self._columns_for_width(self.scroll.viewport().width())
        if columns != self._current_columns and self._cards:
            self._relayout(columns)

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

        # Ingest/prune before the tabs build their initial grids, so the
        # very first render already reflects reality (manually-dropped-in
        # clips included) rather than showing stale/incomplete entries
        # until the next refresh.
        library.scan_and_ingest_new_videos()
        library.prune_missing_videos()

        self.tabs = QTabWidget()
        self.local_tab = _VideoGridTab(uploaded_only=False, local_only=True)
        self.uploaded_tab = _VideoGridTab(uploaded_only=True, local_only=False)
        self.local_tab.edit_requested.connect(self.edit_requested.emit)
        self.uploaded_tab.edit_requested.connect(self.edit_requested.emit)

        # Icon-only tabs (no text) -- the floppy disk / wifi icons stand in
        # for Local / Uploaded. Explicitly sized to 3x the style's own
        # default tab-bar icon size (queried at runtime rather than
        # assumed, since it's style/platform-dependent) -- at the default
        # size these were reportedly unreadable.
        default_icon_size = self.tabs.style().pixelMetric(QStyle.PM_TabBarIconSize)
        self.tabs.setIconSize(QSize(default_icon_size * 3, default_icon_size * 3))
        self.tabs.addTab(self.local_tab, resource_qicon("local_videos.png"), "")
        self.tabs.addTab(self.uploaded_tab, resource_qicon("uploaded_videos.png"), "")
        self.tabs.setTabToolTip(0, "Local")
        self.tabs.setTabToolTip(1, "Uploaded")
        layout.addWidget(self.tabs)

        # Shown when the Editor is opened with no video ever having been
        # selected -- MainWindow redirects here and calls
        # show_status_message() instead of leaving the Editor on its own
        # empty state.
        self.status_bar = QLabel("")
        self.status_bar.setAlignment(Qt.AlignCenter)
        self.status_bar.setStyleSheet("background: palette(midlight); padding: 6px;")
        self.status_bar.setVisible(False)
        layout.addWidget(self.status_bar)

        # Once an actual video is picked to edit, any "Select a video."
        # prompt no longer applies.
        self.edit_requested.connect(lambda _video_id: self.clear_status_message())

    def show_status_message(self, text: str) -> None:
        self.status_bar.setText(text)
        self.status_bar.setVisible(True)

    def clear_status_message(self) -> None:
        self.status_bar.setVisible(False)

    def refresh(self) -> None:
        """Called by MainWindow whenever the Library page becomes visible,
        so edits/deletes made from the Editor page are reflected, and any
        clip files removed outside the app (deleted manually, etc.) drop
        out of the list instead of lingering as broken entries forever."""
        newly_added = library.scan_and_ingest_new_videos()
        if newly_added:
            print(f"Picked up {len(newly_added)} video{'s' if len(newly_added) != 1 else ''} "
                  f"found in the clips folder that weren't in the library yet.")
        removed_ids = library.prune_missing_videos()
        if removed_ids:
            print(f"Removed {len(removed_ids)} library entr{'y' if len(removed_ids) == 1 else 'ies'} "
                  f"whose file no longer exists on disk.")
        self.local_tab.refresh()
        self.uploaded_tab.refresh()
