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

from PySide6.QtCore import Qt, Signal, QSize, QFileSystemWatcher, QTimer
from PySide6.QtGui import QActionGroup, QPainter, QIcon, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit, QToolButton,
    QMenu, QScrollArea, QLabel, QTabWidget, QTabBar, QMessageBox, QWidgetAction,
    QCheckBox, QStyle, QInputDialog, QPushButton,
)

from .. import library
from .. import config as config_module
from .. import db as db_module
from .video_card import VideoCard, THUMB_SIZE, FAVORITE_STAR
from .resources import resource_qpixmap
from .pulse_animation import PulseAnimator

# Approximate on-screen width of one card (thumbnail + its own internal
# margins + the grid's inter-column spacing) -- used only to decide how
# many columns currently fit, not as an exact pixel layout.
_APPROX_CARD_WIDTH = THUMB_SIZE.width() + 24

FILTER_STATE_NONE = "none"
FILTER_STATE_INCLUDE = "include"
FILTER_STATE_EXCLUDE = "exclude"


def _composite_tab_icon(pixmap: QPixmap, target_size: int, canvas_size: int) -> QIcon:
    """Scale `pixmap` to fit within target_size x target_size (preserving
    aspect ratio), then center it on a transparent canvas_size x
    canvas_size canvas and wrap that in a QIcon.

    Why: QTabBar exposes only ONE shared iconSize for the whole bar, so
    the Local and Uploaded tabs can't just each call setIconSize with
    their own value -- but Qt's icon painting scales a QIcon's pixmap to
    fit the tab bar's iconSize, so as long as BOTH tabs' underlying
    pixmaps are exactly canvas_size already, no further scaling happens
    at paint time and each tab's own (possibly smaller) icon content
    stays at its own intended size, just centered within the same
    bounding box the other tab's icon also occupies."""
    scaled = pixmap.scaled(target_size, target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    canvas = QPixmap(canvas_size, canvas_size)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap((canvas_size - scaled.width()) // 2, (canvas_size - scaled.height()) // 2, scaled)
    painter.end()
    return QIcon(canvas)


class FilterCheckBox(QCheckBox):
    """A checkbox for one tag in the Filters dropdown that also supports
    a third "block" state: right-clicking it (instead of left-clicking
    to include) excludes any clip carrying that tag. Qt's QCheckBox has
    no native tri-state visual for "blocked" (its own tristate mode is
    for a hierarchical "some children checked" meaning, not this), so
    the excluded state is shown via a crossed-out box glyph + red text
    on the label rather than the checkbox's own indicator."""

    state_changed = Signal(str, str)  # tag_name, new state

    def __init__(self, tag_name: str, parent=None):
        super().__init__(tag_name, parent)
        self._tag_name = tag_name
        self._state = FILTER_STATE_NONE
        self.toggled.connect(self._on_toggled)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_right_click)

    def set_state(self, state: str) -> None:
        self._state = state
        self.blockSignals(True)
        self.setChecked(state == FILTER_STATE_INCLUDE)
        self.blockSignals(False)
        self._refresh_label()

    def _refresh_label(self) -> None:
        if self._state == FILTER_STATE_EXCLUDE:
            self.setText(f"\u2612 {self._tag_name} (blocked)")
            self.setStyleSheet("color: #d9534f;")
        else:
            self.setText(self._tag_name)
            self.setStyleSheet("")

    def _on_toggled(self, checked: bool) -> None:
        self._state = FILTER_STATE_INCLUDE if checked else FILTER_STATE_NONE
        self._refresh_label()
        self.state_changed.emit(self._tag_name, self._state)

    def _on_right_click(self, _pos) -> None:
        self._state = (
            FILTER_STATE_NONE if self._state == FILTER_STATE_EXCLUDE else FILTER_STATE_EXCLUDE
        )
        self.blockSignals(True)
        self.setChecked(False)
        self.blockSignals(False)
        self._refresh_label()
        self.state_changed.emit(self._tag_name, self._state)


class _SelectionClearingContainer(QWidget):
    """The grid's own container widget (holding the QGridLayout of
    cards) -- a left-click that lands on it directly, rather than on a
    card, means empty space was clicked (gaps between rows/columns,
    or below the last row), which conventionally clears the current
    multi-selection."""

    background_clicked = Signal()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.background_clicked.emit()
        super().mousePressEvent(event)


class _VideoGridTab(QWidget):
    edit_requested = Signal(int)

    def __init__(self, uploaded_only: bool, local_only: bool, parent=None):
        super().__init__(parent)
        self._uploaded_only = uploaded_only
        self._local_only = local_only
        self._active_tags: set[str] = set()
        self._excluded_tags: set[str] = set()
        self._favorite_only: bool = False
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
        # Multi-select state: which video ids are selected, and the
        # "anchor" index (position in self._cards) that shift-click
        # range-selects from -- standard file-manager convention: plain
        # click replaces the selection and moves the anchor here;
        # ctrl-click toggles just this one and also moves the anchor;
        # shift-click selects the contiguous range from the anchor to
        # here, replacing the selection, without moving the anchor
        # (so repeated shift-clicks keep extending/shrinking from the
        # same starting point).
        self._selected_ids: set[int] = set()
        self._selection_anchor_index: int | None = None

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

        self.info_btn = QToolButton()
        self.info_btn.setText("Info")
        self.info_btn.setPopupMode(QToolButton.InstantPopup)
        self._build_info_menu()
        top_row.addWidget(self.info_btn)
        outer.addLayout(top_row)

        # ---- grid ----
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.grid_container = _SelectionClearingContainer()
        self.grid_container.background_clicked.connect(self._clear_selection)
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

        # Built-in "Favorite" filter, pinned above every user tag.
        favorite_checkbox = QCheckBox(f"{FAVORITE_STAR} Favorite", menu)
        favorite_checkbox.setChecked(self._favorite_only)
        favorite_checkbox.toggled.connect(self._toggle_favorite_filter)
        favorite_action = QWidgetAction(menu)
        favorite_action.setDefaultWidget(favorite_checkbox)
        menu.addAction(favorite_action)
        menu.addSeparator()

        all_tags = library.all_known_tags()
        grouped, uncategorized = library.tags_grouped_by_category()

        def _make_checkbox(tag: str, target_menu: QMenu) -> None:
            checkbox = FilterCheckBox(tag, target_menu)
            if tag in self._excluded_tags:
                checkbox.set_state(FILTER_STATE_EXCLUDE)
            elif tag in self._active_tags:
                checkbox.set_state(FILTER_STATE_INCLUDE)
            checkbox.state_changed.connect(self._on_filter_state_changed)
            action = QWidgetAction(target_menu)
            action.setDefaultWidget(checkbox)
            target_menu.addAction(action)

        if not all_tags:
            no_tags_action = menu.addAction("(no tags yet)")
            no_tags_action.setEnabled(False)

        # Categories render as submenus that open to the side, like
        # folders -- each one is its own QMenu added via addMenu(),
        # which is what gives the side-opening-submenu behavior for
        # free rather than needing to build that interaction by hand.
        for category_name, tag_names in grouped.items():
            category_menu = QMenu(category_name, menu)
            for tag in tag_names:
                _make_checkbox(tag, category_menu)
            menu.addMenu(category_menu)

        for tag in uncategorized:
            _make_checkbox(tag, menu)

        menu.addSeparator()
        add_filter_btn = QPushButton("+ Add Filter")
        add_filter_btn.setFlat(True)
        add_filter_btn.clicked.connect(self._add_new_filter)
        add_filter_action = QWidgetAction(menu)
        add_filter_action.setDefaultWidget(add_filter_btn)
        menu.addAction(add_filter_action)

        menu.addSeparator()
        highlight_checkbox = QCheckBox("Highlight Unedited", menu)
        highlight_checkbox.setChecked(self._highlight_unedited)
        highlight_checkbox.toggled.connect(self._toggle_highlight_unedited)
        highlight_action = QWidgetAction(menu)
        highlight_action.setDefaultWidget(highlight_checkbox)
        menu.addAction(highlight_action)

        self.filters_btn.setMenu(menu)

    def _toggle_favorite_filter(self, checked: bool) -> None:
        self._favorite_only = checked
        self.refresh()

    def _on_filter_state_changed(self, tag: str, state: str) -> None:
        self._active_tags.discard(tag)
        self._excluded_tags.discard(tag)
        if state == FILTER_STATE_INCLUDE:
            self._active_tags.add(tag)
        elif state == FILTER_STATE_EXCLUDE:
            self._excluded_tags.add(tag)
        self.refresh()

    def _on_icon_left_clicked(self, tag: str) -> None:
        # Toggle: clicking a filter icon that's already an active
        # include-filter clears it, same as unchecking it in the
        # dropdown would.
        new_state = FILTER_STATE_NONE if tag in self._active_tags else FILTER_STATE_INCLUDE
        self._on_filter_state_changed(tag, new_state)

    def _on_icon_right_clicked(self, tag: str) -> None:
        new_state = FILTER_STATE_NONE if tag in self._excluded_tags else FILTER_STATE_EXCLUDE
        self._on_filter_state_changed(tag, new_state)

    def _add_new_filter(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Filter", "Filter name:")
        name = name.strip()
        if ok and name:
            library.create_tag(name)
            self.refresh()

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

    # ------------------------------------------------------------ info menu

    def _build_info_menu(self) -> None:
        """What's shown on each card besides the thumbnail/title itself:
        filters, video length, file size, creation date -- persisted to
        config (see config.CardInfoSettings) since this is a display
        preference, not a per-session filter selection."""
        menu = QMenu(self.info_btn)
        card_info = config_module.load().card_info

        options = [
            ("Show Filters", "show_filters", card_info.show_filters),
            ("Show Video Length", "show_length", card_info.show_length),
            ("Show File Size", "show_file_size", card_info.show_file_size),
            ("Show Creation Date", "show_creation_date", card_info.show_creation_date),
        ]
        for label, field_name, checked in options:
            checkbox = QCheckBox(label, menu)
            checkbox.setChecked(checked)
            checkbox.toggled.connect(lambda is_checked, f=field_name: self._set_card_info_field(f, is_checked))
            action = QWidgetAction(menu)
            action.setDefaultWidget(checkbox)
            menu.addAction(action)

        self.info_btn.setMenu(menu)

    def _set_card_info_field(self, field_name: str, checked: bool) -> None:
        settings = config_module.load()
        setattr(settings.card_info, field_name, checked)
        config_module.save(settings)
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
        # The video list is about to be requeried (new order, possibly
        # missing/added ids from filters or a search) -- last session's
        # selection has no reliable meaning against it, so start clean
        # rather than risk selected_ids referencing ids no longer shown.
        self._selected_ids = set()
        self._selection_anchor_index = None

        videos = library.list_videos(
            tag_filter=list(self._active_tags) or None,
            tag_exclude=list(self._excluded_tags) or None,
            favorite_only=self._favorite_only,
            uploaded_only=self._uploaded_only,
            local_only=self._local_only,
            search=self.search_edit.text().strip() or None,
            sort_by=self._sort_by,
        )

        self.empty_label.setVisible(len(videos) == 0)
        self.scroll.setVisible(len(videos) > 0)

        for video in videos:
            card = VideoCard(
                video, highlight_enabled=self._highlight_unedited, font_scale=self._font_scale,
                get_selected_ids=lambda: self._selected_ids,
                ensure_selected=self._ensure_selected_for_context_menu,
            )
            card.edit_requested.connect(self.edit_requested.emit)
            card.deleted.connect(lambda _vid: self.refresh())
            card.tags_changed.connect(self.refresh)
            card.renamed.connect(self.refresh)
            card.upload_requested.connect(self._handle_upload_request)
            card.filter_left_clicked.connect(self._on_icon_left_clicked)
            card.filter_right_clicked.connect(self._on_icon_right_clicked)
            card.clicked.connect(self._on_card_clicked)
            self._cards.append(card)

        self._relayout(self._columns_for_width(self.scroll.viewport().width()))

    # ------------------------------------------------------------ selection

    def _on_card_clicked(self, video_id: int, modifiers) -> None:
        try:
            index = next(i for i, c in enumerate(self._cards) if c.video_id == video_id)
        except StopIteration:
            return  # card was clicked but is no longer in _cards (shouldn't happen)

        if modifiers & Qt.ShiftModifier and self._selection_anchor_index is not None:
            lo, hi = sorted((self._selection_anchor_index, index))
            self._selected_ids = {self._cards[i].video_id for i in range(lo, hi + 1)}
            # Anchor deliberately NOT moved -- lets a further shift-click
            # extend/shrink the range from the same starting point.
        elif modifiers & Qt.ControlModifier:
            if video_id in self._selected_ids:
                self._selected_ids.discard(video_id)
            else:
                self._selected_ids.add(video_id)
            self._selection_anchor_index = index
        else:
            self._selected_ids = {video_id}
            self._selection_anchor_index = index
        self._apply_selection_visuals()

    def _clear_selection(self) -> None:
        if self._selected_ids:
            self._selected_ids = set()
            self._selection_anchor_index = None
            self._apply_selection_visuals()

    def _ensure_selected_for_context_menu(self, video_id: int) -> None:
        """Called by a VideoCard right before it builds its context
        menu. Standard file-manager convention: right-clicking a card
        that's already part of the current multi-selection leaves that
        selection intact (so the menu acts on all of it); right-clicking
        one that ISN'T replaces the selection with just that card (so
        the menu doesn't act on some unrelated older selection)."""
        if video_id not in self._selected_ids:
            try:
                index = next(i for i, c in enumerate(self._cards) if c.video_id == video_id)
            except StopIteration:
                index = None
            self._selected_ids = {video_id}
            self._selection_anchor_index = index
            self._apply_selection_visuals()

    def _apply_selection_visuals(self) -> None:
        for card in self._cards:
            card.set_selected(card.video_id in self._selected_ids)

    def neighbors(self, video_id: int) -> tuple["library.Video | None", "library.Video | None"]:
        """(previous, next) video relative to video_id in this tab's
        CURRENT card order -- i.e. whatever this tab is presently
        sorted/filtered/searched by, since self._cards is exactly that
        (rebuilt by refresh() from the same library.list_videos() call
        that order comes from). Used by the Editor's Prev/Next arrows.
        Returns (None, None) if video_id isn't currently in this tab at
        all (e.g. it was deleted, or a filter/search since applied
        excludes it)."""
        ids = [c.video_id for c in self._cards]
        if video_id not in ids:
            return None, None
        index = ids.index(video_id)
        prev_video = self._cards[index - 1]._video if index > 0 else None
        next_video = self._cards[index + 1]._video if index < len(ids) - 1 else None
        return prev_video, next_video

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


class _PulsingTabBar(QTabBar):
    """A QTabBar that pulses its icon(s) on press/release and eases to a
    slightly smaller size on hover, matching the sidebar nav buttons'
    click/hover feel. QTabBar only exposes ONE iconSize for the whole
    bar (not per-tab), so both Local/Uploaded icons move together
    rather than just the one actually clicked/hovered -- an accepted
    simplification given there are only ever the two of them, both
    visible at once."""

    def __init__(self, on_press, on_release, on_hover_enter, on_hover_leave, parent=None):
        super().__init__(parent)
        self._on_press = on_press
        self._on_release = on_release
        self._on_hover_enter = on_hover_enter
        self._on_hover_leave = on_hover_leave
        self._frozen_size_hint: QSize | None = None

    def freeze_size_hint(self) -> None:
        """Capture sizeHint() at the current (un-animated) icon size and
        report that fixed value from sizeHint() from then on, regardless
        of the pulse animation's per-frame setIconSize() calls.
        QTabWidget's own internal layout sizes the tab bar vs. the page
        content below it using the tab bar's sizeHint() -- NOT its
        actual on-screen height -- so setFixedHeight() alone (which only
        constrains the bar's own rendered size) wasn't enough: the
        content area's height/position still visibly shifted every
        animation frame, tracking sizeHint()'s shrink/grow instead."""
        self._frozen_size_hint = QTabBar.sizeHint(self)

    def sizeHint(self) -> QSize:
        if self._frozen_size_hint is not None:
            return self._frozen_size_hint
        return super().sizeHint()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.tabAt(event.pos()) != -1:
            self._on_press()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._on_release(self.underMouse())
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:
        self._on_hover_enter()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._on_hover_leave()
        super().leaveEvent(event)


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
        self._tab_pulse = PulseAnimator(
            get_base_size=lambda: self._current_tab_icon_size,
            apply_size=lambda size: self.tabs.setIconSize(QSize(size, size)),
        )
        self.tabs.setTabBar(_PulsingTabBar(
            self._tab_pulse.press, self._tab_pulse.release,
            self._tab_pulse.hover_enter, self._tab_pulse.hover_leave,
        ))
        self.local_tab = _VideoGridTab(uploaded_only=False, local_only=True)
        self.uploaded_tab = _VideoGridTab(uploaded_only=True, local_only=False)
        # Tracks which tab most recently asked to open a video in the
        # Editor -- neighbors_for() below uses it to answer "prev/next
        # relative to THIS tab's current order", since a video could in
        # principle appear reachable from either tab's own edit_requested
        # depending on which one the person actually clicked from.
        self._last_edit_tab: "_VideoGridTab | None" = None
        self.local_tab.edit_requested.connect(
            lambda vid: self._on_tab_edit_requested(self.local_tab, vid)
        )
        self.uploaded_tab.edit_requested.connect(
            lambda vid: self._on_tab_edit_requested(self.uploaded_tab, vid)
        )

        # Icon-only tabs (no text) -- the floppy disk / wifi icons stand in
        # for Local / Uploaded. Base size is 4.5x the style's own default
        # tab-bar icon size: originally set to 3x (default_icon_size * 3),
        # then asked to be 1.5x that current size on top -- 3 * 1.5 = 4.5x
        # the original style default, queried at runtime rather than
        # assumed. Stored so apply_scale() below can rescale it later --
        # this wasn't being done at all before, so the tab icons stayed
        # fixed regardless of window size while everything else around
        # them scaled. The two tabs' actual on-screen icon sizes can now
        # differ (Saved Videos Icon Size / Uploaded Videos Icon Size in
        # Settings > General) despite QTabBar's single shared iconSize --
        # see _composite_tab_icon's docstring for how.
        self._base_tab_icon_size = round(
            self.tabs.style().pixelMetric(QStyle.PM_TabBarIconSize) * 4.5
        )
        self._current_tab_icon_size = self._base_tab_icon_size
        self.tabs.addTab(self.local_tab, "")
        self.tabs.addTab(self.uploaded_tab, "")
        self._rebuild_tab_icons()
        self.tabs.setTabToolTip(0, "Local")
        self.tabs.setTabToolTip(1, "Uploaded")
        self._fix_tab_bar_height()
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

        # Live refresh: the hotkey-triggered clip pipeline (trim + Auto
        # Add Filter tagging) runs in a completely separate daemon
        # process (see daemon.py), not this GUI -- so there's no
        # in-process signal to connect to when a new clip finishes.
        # Watching the DB file itself for changes is the cross-process
        # signal instead: the daemon's LAST write for a given clip is
        # always its tag inserts (after the video row itself), so by
        # the time this fires, the clip's filters are already applied
        # too, matching "refresh once it's fully done" rather than
        # refreshing the moment the file appears but before it's
        # tagged. Requires the default rollback-journal mode (not WAL,
        # which this app doesn't use) -- WAL writes go to a separate
        # -wal sidecar file most of the time, which a watch on the main
        # .db file alone would largely miss.
        self._db_watcher = QFileSystemWatcher(self)
        if db_module.DB_PATH.exists():
            self._db_watcher.addPath(str(db_module.DB_PATH))
        self._db_watcher.fileChanged.connect(self._on_db_file_changed)
        # Debounced rather than refreshing on every individual fileChanged
        # signal: one clip capture is actually several writes in quick
        # succession (the video row, then one insert per applied auto-tag),
        # each of which would otherwise trigger its own separate refresh.
        self._refresh_debounce = QTimer(self)
        self._refresh_debounce.setSingleShot(True)
        self._refresh_debounce.setInterval(400)
        self._refresh_debounce.timeout.connect(self.refresh)

    def _on_db_file_changed(self, path: str) -> None:
        # Some editors/writers replace rather than modify a watched file,
        # which silently drops it from QFileSystemWatcher's internal list
        # -- re-adding it defensively after every change keeps the watch
        # alive even if SQLite's actual on-disk write pattern ever changes
        # (e.g. a future switch to WAL mode's checkpoint-and-replace).
        if path not in self._db_watcher.files() and db_module.DB_PATH.exists():
            self._db_watcher.addPath(path)
        self._refresh_debounce.start()  # (re)start -- coalesces a burst of writes into one refresh

    def show_status_message(self, text: str) -> None:
        self.status_bar.setText(text)
        self.status_bar.setVisible(True)

    def clear_status_message(self) -> None:
        self.status_bar.setVisible(False)

    def _on_tab_edit_requested(self, tab: "_VideoGridTab", video_id: int) -> None:
        self._last_edit_tab = tab
        self.edit_requested.emit(video_id)

    def neighbors_for(self, video_id: int) -> tuple["library.Video | None", "library.Video | None"]:
        """(previous, next) video relative to video_id, according to
        whichever tab's edit_requested most recently fired for it -- see
        _VideoGridTab.neighbors() for what "relative to" actually means.
        Passed to EditorPage as its neighbor_provider (see main_window.py)
        and queried fresh on every Editor refresh, not just once when
        Edit was first clicked."""
        if self._last_edit_tab is None:
            return None, None
        return self._last_edit_tab.neighbors(video_id)

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

    def _rebuild_tab_icons(self) -> None:
        """(Re)composite the Local/Uploaded tab icons at their own
        independent sizes (Saved Videos Icon Size / Uploaded Videos Icon
        Size in Settings > General), against self._current_tab_icon_size
        as the 100% baseline -- see _composite_tab_icon's docstring for
        how two different sizes coexist despite QTabBar's single shared
        iconSize. Called at construction and from apply_scale() whenever
        the baseline changes; NOT called by the click-pulse animation
        itself, which only calls tabs.setIconSize() directly to scale
        the already-composited icons uniformly (see PulseAnimator's
        apply_size callback below)."""
        appearance = config_module.load().appearance
        base = self._current_tab_icon_size
        local_target = max(1, round(base * appearance.saved_videos_icon_size / 100))
        uploaded_target = max(1, round(base * appearance.uploaded_videos_icon_size / 100))
        shared = max(local_target, uploaded_target, 1)
        self.tabs.setIconSize(QSize(shared, shared))
        self.tabs.setTabIcon(0, _composite_tab_icon(resource_qpixmap("local_videos.png"), local_target, shared))
        self.tabs.setTabIcon(1, _composite_tab_icon(resource_qpixmap("uploaded_videos.png"), uploaded_target, shared))

    def _fix_tab_bar_height(self) -> None:
        """Lock the tab bar's own height to its natural size at the
        current (un-animated) icon size, so the click-pulse's per-frame
        setIconSize() calls -- which would otherwise shrink/grow the
        tab bar itself, since QTabBar derives its height from icon
        size -- only change how big the icon renders inside a
        constant-height bar, instead of pushing the search bar and
        video grid below it up and down. Re-called from apply_scale()
        whenever the base icon size legitimately changes; the pulse
        animation itself never touches this.

        freeze_size_hint() first: setFixedHeight() alone constrains the
        bar's own rendered height, but QTabWidget's internal layout
        positions the page content below the bar using the bar's
        sizeHint() (not its actual height), which the animation's
        setIconSize() calls still changed every frame -- see
        freeze_size_hint()'s docstring."""
        bar = self.tabs.tabBar()
        bar.freeze_size_hint()
        bar.setFixedHeight(bar.sizeHint().height())

    def apply_scale(self, factor: float) -> None:
        self.local_tab.apply_scale(factor)
        self.uploaded_tab.apply_scale(factor)
        # Was previously set once at construction and never touched
        # again, so it stayed fixed regardless of window size while
        # everything else scaled -- now rescaled live alongside them.
        size = max(round(self._base_tab_icon_size * factor), 8)
        self._current_tab_icon_size = size
        self._rebuild_tab_icons()
        self._fix_tab_bar_height()
