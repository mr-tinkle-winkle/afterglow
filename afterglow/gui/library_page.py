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
from PySide6.QtGui import QActionGroup
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
        self._sort_by: str = library.DEFAULT_SORT
        self._highlight_unedited: bool = True
        self._font_scale: float = 1.0
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

        self.refresh_btn = QToolButton()
        self.refresh_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_btn.setToolTip("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        top_row.addWidget(self.refresh_btn)

        self.filters_btn = QToolButton()
        self.filters_btn.setText("Filters")
        self.filters_btn.setPopupMode(QToolButton.InstantPopup)
        top_row.addWidget(self.filters_btn)

        self.sort_btn = QToolButton()
        self.sort_btn.setText("Sort By:")
        self.sort_btn.setPopupMode(QToolButton.InstantPopup)
        self._build_sort_menu()
        top_row.addWidget(self.sort_btn)
        outer.addLayout(top_row)

        # ---- grid ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        # Tightened from the 6px default -- this is on top of the
        # row-stretch fix in _relayout() below, which addresses the much
        # larger gap that was actually coming from leftover scroll-area
        # space being split across rows rather than from this spacing
        # value itself.
        self.grid_layout.setVerticalSpacing(2)
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

        menu.addSeparator()
        highlight_checkbox = QCheckBox("Highlight Unedited", menu)
        highlight_checkbox.setChecked(self._highlight_unedited)
        highlight_checkbox.toggled.connect(self._toggle_highlight_unedited)
        highlight_action = QWidgetAction(menu)
        highlight_action.setDefaultWidget(highlight_checkbox)
        menu.addAction(highlight_action)

        self.filters_btn.setMenu(menu)

    def _toggle_highlight_unedited(self, checked: bool) -> None:
        self._highlight_unedited = checked
        # Live-updates existing cards in place rather than rebuilding the
        # grid -- there's nothing else that needs to change.
        for card in self._cards:
            card.set_highlight_enabled(checked)

    def apply_scale(self, factor: float) -> None:
        # Cheap live update (font-only, no thumbnail regen/DB requery) --
        # thumbnails stay a fixed size for now to avoid touching the
        # thumbnail cache's own sizing assumptions.
        self._font_scale = factor
        for card in self._cards:
            card.set_font_scale(factor)

    def _toggle_tag(self, tag: str, checked: bool) -> None:
        if checked:
            self._active_tags.add(tag)
        else:
            self._active_tags.discard(tag)
        self.refresh()

    # ------------------------------------------------------------ sort menu

    def _build_sort_menu(self) -> None:
        # Built once (unlike the filters menu, which depends on which
        # tags currently exist) -- the sort options themselves never
        # change, only which one is checked.
        menu = QMenu(self.sort_btn)
        group = QActionGroup(menu)
        group.setExclusive(True)

        # (label, sort_by constant) pairs, each immediately followed by
        # its inverse -- matches the requested ordering of each mode next
        # to its opposite.
        options = [
            ("Creation date (newest first)", library.SORT_CREATED_NEWEST),
            ("Creation date (oldest first)", library.SORT_CREATED_OLDEST),
            ("Last modified (newest first)", library.SORT_MODIFIED_NEWEST),
            ("Last modified (oldest first)", library.SORT_MODIFIED_OLDEST),
            ("Name (A to Z)", library.SORT_NAME_A_TO_Z),
            ("Name (Z to A)", library.SORT_NAME_Z_TO_A),
            ("Video length (short to long)", library.SORT_LENGTH_SHORT_TO_LONG),
            ("Video length (long to short)", library.SORT_LENGTH_LONG_TO_SHORT),
        ]
        for label, sort_by in options:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(sort_by == self._sort_by)
            action.triggered.connect(lambda checked, s=sort_by: self._set_sort_by(s))
            group.addAction(action)
        self.sort_btn.setMenu(menu)

    def _set_sort_by(self, sort_by: str) -> None:
        self._sort_by = sort_by
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
            sort_by=self._sort_by,
        )

        self.empty_label.setVisible(len(videos) == 0)
        self.scroll.setVisible(len(videos) > 0)

        for video in videos:
            card = VideoCard(video, highlight_enabled=self._highlight_unedited, font_scale=self._font_scale)
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

        # Without an absorbing row after the real content, QGridLayout
        # splits any leftover vertical space (the scroll area's viewport
        # is usually much taller than a row or two of cards) PROPORTION-
        # ALLY ACROSS the actual content rows -- stretching them apart
        # with large gaps between rows instead of packing them together.
        # Reset a generous range first (a previous, larger row count may
        # have left a stretch factor on a row index that's no longer the
        # last one), then give exactly the row just past the real content
        # all the stretch, so it absorbs 100% of the slack and the real
        # rows stay packed at their natural height.
        row_count = -(-len(self._cards) // columns) if self._cards else 0  # ceiling division
        for stale_row in range(max(row_count + 2, 8)):
            self.grid_layout.setRowStretch(stale_row, 0)
        self.grid_layout.setRowStretch(row_count, 1)

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
        library.remove_stray_orig_entries()

        self.tabs = QTabWidget()
        self.local_tab = _VideoGridTab(uploaded_only=False, local_only=True)
        self.uploaded_tab = _VideoGridTab(uploaded_only=True, local_only=False)
        self.local_tab.edit_requested.connect(self.edit_requested.emit)
        self.uploaded_tab.edit_requested.connect(self.edit_requested.emit)

        # Icon-only tabs (no text) -- the floppy disk / wifi icons stand in
        # for Local / Uploaded. 4.5x the style's own default tab-bar icon
        # size: originally set to 3x (default_icon_size * 3), then asked
        # to be 1.5x that current size on top -- 3 * 1.5 = 4.5x the
        # original style default, queried at runtime rather than assumed.
        default_icon_size = self.tabs.style().pixelMetric(QStyle.PM_TabBarIconSize)
        tab_icon_size = round(default_icon_size * 4.5)
        self.tabs.setIconSize(QSize(tab_icon_size, tab_icon_size))
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
        stray_orig_ids = library.remove_stray_orig_entries()
        if stray_orig_ids:
            print(f"Removed {len(stray_orig_ids)} .orig backup file{'s' if len(stray_orig_ids) != 1 else ''} "
                  f"that had been mistakenly listed as library entries.")
        self.local_tab.refresh()
        self.uploaded_tab.refresh()

    def apply_scale(self, factor: float) -> None:
        self.local_tab.apply_scale(factor)
        self.uploaded_tab.apply_scale(factor)
