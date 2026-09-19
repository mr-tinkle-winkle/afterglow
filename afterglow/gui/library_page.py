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

from pathlib import Path
from PySide6.QtCore import Qt, Signal, QSize, QFileSystemWatcher, QTimer, QRectF
from PySide6.QtGui import QPainter, QPixmap, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit,
    QScrollArea, QLabel, QStackedWidget, QMessageBox,
    QStyle, QInputDialog, QDialog,
    QButtonGroup, QAbstractButton, QApplication,
)

from .. import library
from .. import config as config_module
from .. import db as db_module
from .video_card import VideoCard, THUMB_SIZE, FAVORITE_STAR
from .resources import resource_qpixmap
from .theme import Theme
from .rounded_rect import rounded_rect_path
from .custom_button import CustomButton
from .smooth_scroll_area import SmoothScrollArea
from .sort_popover import SortPopover
from .search_bubble import SearchBubble
from .pixmap_effects import tint_pixmap_cached
from .custom_group_box import CustomGroupBox
from .custom_radio_button import CustomRadioButton
from .scale_reveal import crossfade_to_index
from .page_outline import paint_page_outline, BORDER_WIDTH
from .custom_checkbox import CustomCheckBox

# Approximate on-screen width of one card (thumbnail + its own internal
# margins + the grid's inter-column spacing) -- used only to decide how
# many columns currently fit, not as an exact pixel layout.
_APPROX_CARD_WIDTH = THUMB_SIZE.width() + 24

FILTER_STATE_NONE = "none"
FILTER_STATE_INCLUDE = "include"
FILTER_STATE_EXCLUDE = "exclude"


class LibraryTabButton(QAbstractButton):
    """Local/Uploaded's own custom page-switch button -- replaces
    QTabWidget's default page-header behavior (asked to be replaced
    several times; see HANDOFF.md). Two independent icon sizes are now
    trivial (each button just gets its own set_icon_target_size() call)
    since there's no more QTabBar single-shared-iconSize limitation to
    work around via the old canvas-compositing trick.

    Also reused (unchanged) for the main sidebar's Library/Editor/
    Settings buttons -- see main_window.py -- per Max's direct request
    to replace those with "the same custom button type" as this one,
    dropping the old border-gradient/hue-shift/icon-darkening system
    entirely in favor of this simpler turquoise fill.

    Rounding matches the same "don't round a corner that's touching
    another element" rule used everywhere else: position='left' rounds
    only the left corners, 'right' only the right corners (for a pair
    that touch, like Local/Uploaded), and 'full' rounds all four (for
    a button that doesn't touch its neighbors, like the sidebar's three
    now that real padding sits between them)."""

    def __init__(self, icon_pixmap: QPixmap, position: str = "full", parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._icon_pixmap = icon_pixmap
        self._position = position  # 'left' | 'right' | 'full'
        self._icon_target_size = 32
        appearance = config_module.load().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)

    def set_icon_target_size(self, size: int) -> None:
        self._icon_target_size = max(1, size)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        pad = 16
        return QSize(self._icon_target_size + pad, self._icon_target_size + pad)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        rect = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 0
        is_left = self._position == "left"
        is_right = self._position == "right"
        round_left = is_left or self._position == "full"
        round_right = is_right or self._position == "full"

        bg = self._theme.turquoise()
        if not self.isChecked():
            bg = bg.darker(140)
        if self.isDown():
            bg = bg.darker(125)
        elif self.underMouse():
            bg = bg.lighter(112)

        if radius:
            path = rounded_rect_path(
                rect, radius,
                top_left=round_left, bottom_left=round_left,
                top_right=round_right, bottom_right=round_right,
            )
            painter.fillPath(path, bg)
        else:
            painter.fillRect(self.rect(), bg)

        scaled = self._icon_pixmap.scaled(
            self._icon_target_size, self._icon_target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class FilterCheckBox(CustomCheckBox):
    """A checkbox for one tag in the Filters page that also supports a
    third "block" state: right-clicking it (instead of left-clicking to
    include) excludes any clip carrying that tag. Custom-painted (see
    CustomCheckBox) rather than a native QCheckBox -- checkmark icon for
    "include", the x icon (Max: "for blocked in library filters and not
    anywhere else") for "block", empty box for neither. This is the ONE
    place the x icon is used; every other CustomCheckBox in the app only
    ever shows the checkmark or nothing."""

    state_changed = Signal(str, str)  # tag_name, new state

    def __init__(self, tag_name: str, parent=None, leading_icon=None):
        super().__init__(tag_name, parent, leading_icon=leading_icon)
        self._tag_name = tag_name
        self._state = FILTER_STATE_NONE
        self._x_icon = resource_qpixmap("x_icon.png")
        self.toggled.connect(self._on_toggled)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_right_click)

    def _icon_for_state(self):
        if self._state == FILTER_STATE_EXCLUDE:
            return self._x_icon
        if self._state == FILTER_STATE_INCLUDE:
            return self._checkmark
        return None

    def set_state(self, state: str) -> None:
        self._state = state
        self.blockSignals(True)
        self.setChecked(state == FILTER_STATE_INCLUDE)
        self.blockSignals(False)
        self._refresh_label()

    def _refresh_label(self) -> None:
        if self._state == FILTER_STATE_EXCLUDE:
            self.setText(f"{self._tag_name} (blocked)")
        else:
            self.setText(self._tag_name)
        self.update()

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
    preview_requested = Signal(object, object)  # video, neighbor_provider

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

        # Leading-edge debounce for refresh() -- see refresh()'s own
        # comment for why. A plain bool flag + a singleShot timer that
        # just clears it, rather than reusing the trailing-edge
        # QTimer.start()-restarts-the-countdown pattern the DB file
        # watcher uses further down (_refresh_debounce): that pattern
        # is right for "wait for a burst of background writes to settle
        # before reacting once", but wrong here -- a user clicking
        # Refresh wants the FIRST click to act immediately, not wait
        # 0.75s to see anything happen at all.
        self._refresh_debounce_active = False
        self._refresh_cooldown_timer = QTimer(self)
        self._refresh_cooldown_timer.setSingleShot(True)
        self._refresh_cooldown_timer.setInterval(750)
        self._refresh_cooldown_timer.timeout.connect(self._clear_refresh_cooldown)

        outer = QVBoxLayout(self)

        # Explicit margin reserving room for the 3px page outline
        # painted below -- NOT relying on whatever the ambient QStyle's
        # own default QLayout margin happens to be (often nonzero, but
        # not guaranteed, and can differ between this sandbox's default
        # offscreen-platform style and Max's real one). If a style ever
        # left zero margin here, child content would sit flush against
        # this widget's own edge and completely paint over the border
        # drawn beneath it in paintEvent -- exactly matching "the page
        # outlines don't seem to appear," reported directly. Top is 0
        # since this tab's own outline skips that edge anyway (flush
        # against the Library header above it).
        outer.setContentsMargins(BORDER_WIDTH, 0, BORDER_WIDTH, BORDER_WIDTH)

        # ---- grid ----
        # Search/Refresh/Sort now live once, shared, in LibraryPage's own
        # header row (alongside the Local/Uploaded page buttons) instead
        # of each tab having its own copy taking a whole separate row --
        # per Max's direct instruction that the old per-tab row was
        # "encroaching on the videos." This tab still owns all the
        # underlying STATE (search text, active/excluded tags, sort
        # order, highlight toggle) and the three popover-page builder
        # methods below -- LibraryPage just calls into whichever tab is
        # currently active rather than each tab having its own buttons.
        # self.search_edit is a plain, never-shown QLineEdit purely for
        # text storage + its existing textChanged wiring -- LibraryPage's
        # single shared SearchBubble syncs its own visible line edit's
        # text into whichever tab is currently active.
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._do_refresh)

        self.scroll = SmoothScrollArea()
        self.scroll.setWidgetResizable(True)
        # A horizontal scrollbar should never legitimately appear here --
        # the column count is computed to fit the available width (see
        # _relayout) -- but a one-off rounding difference between that
        # calculation and the grid's own actual spacing/margins could
        # still leave content a pixel or two wider than the viewport,
        # which is enough to trigger Qt's default ScrollBarAsNeeded
        # policy. Reported directly as appearing "sometimes." Turning it
        # off outright is simpler and more robust than chasing an exact
        # rounding fix: there's nothing meant to be reachable by
        # scrolling horizontally in this grid regardless.
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.grid_container = _SelectionClearingContainer()
        self.grid_container.background_clicked.connect(self._clear_selection)
        self.grid_layout = QGridLayout(self.grid_container)
        # Both driven by the shared "Padding" setting now (previously a
        # hardcoded 2px vertical-only value) -- "adjusts the pixels of
        # padding used everywhere ... such as between video cards", per
        # how this was actually asked for. Independent of the row-
        # stretch-absorption trick in _relayout() below, which handles
        # a completely different problem (leftover scroll-area slack),
        # so changing this doesn't risk reintroducing that.
        appearance = config_module.load().appearance
        self.grid_layout.setVerticalSpacing(appearance.ui_padding)
        self.grid_layout.setHorizontalSpacing(appearance.ui_padding)
        # Same padding value on the grid's own OUTER edges too, not just
        # between cards -- per Max's direct follow-up ("the padding
        # between videos should be applied to videos and the edges of
        # the library 'container'"). Previously this used whatever
        # QGridLayout's own default contentsMargins happened to be,
        # unrelated to the Padding setting at all.
        self.grid_layout.setContentsMargins(
            appearance.ui_padding, appearance.ui_padding, appearance.ui_padding, appearance.ui_padding
        )
        # The grid's own background -- a shade darker than the app-wide
        # background (Theme itself decides whether these are Afterglow's
        # fixed colors or a live KDE-palette equivalent, so this call
        # doesn't need its own separate on/off check).
        #
        # QPalette, not an unscoped setStyleSheet("background-color: ...")
        # -- an unscoped CSS property on a widget's stylesheet is a
        # well-known Qt gotcha: Qt's style engine treats it as applying
        # to the WHOLE subtree (effectively "* { ... }"), which can
        # bleed into descendant widgets' own painting, INCLUDING ones
        # with a fully custom paintEvent like VideoCard, on a real
        # compositor -- and this sandbox's offscreen platform plugin
        # doesn't reliably reproduce that same cascading behavior, so a
        # test here passing is not proof it's safe on a real display.
        # Setting the palette directly instead affects only this one
        # widget, with no cascading path at all.
        theme = Theme(appearance)
        library_bg = theme.library_background()
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        for widget in (self.scroll, self.scroll.viewport(), self.grid_container):
            widget.setAutoFillBackground(True)
            palette = widget.palette()
            palette.setColor(widget.backgroundRole(), library_bg)
            widget.setPalette(palette)
        self.scroll.setWidget(self.grid_container)
        outer.addWidget(self.scroll, stretch=1)

        self.empty_label = QLabel("No clips yet.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.empty_label)

        # Debounces refreshes triggered by a card's own signals
        # (tags_changed, renamed) -- these used to call self.refresh()
        # DIRECTLY, meaning every single filter toggle (or an inline
        # title-edit's own commit) rebuilt every card in the grid from
        # scratch, immediately, reported directly as real, noticeable
        # lag on every toggle "wherever you do it." A single toggle
        # still refreshes (this doesn't skip work, only coalesces
        # BURSTS of them -- e.g. toggling several filters in a row
        # while a menu stays open) into one rebuild shortly after the
        # last one, rather than one rebuild per individual toggle.
        self._card_signal_debounce = QTimer(self)
        self._card_signal_debounce.setSingleShot(True)
        self._card_signal_debounce.setInterval(150)
        self._card_signal_debounce.timeout.connect(self._on_card_signal_debounce_timeout)
        # Counts how many cards currently have a context menu open --
        # this is the actual root cause finally found for "the context
        # menu still closes when checking a box," after two earlier
        # fixes (both aimed at QMenu's OWN closing behavior) didn't
        # hold up: menu.exec() runs its own nested event loop, which
        # STILL processes timers -- so this debounce firing WHILE a
        # menu is open would rebuild the whole grid (destroying the
        # VideoCard the open menu is parented to), closing the menu as
        # a side effect of its own parent being destroyed, completely
        # independent of anything QMenu itself does. Rescheduling
        # instead of firing while any menu is open avoids that
        # entirely.
        self._menu_open_count = 0

        self.refresh()

    # ------------------------------------------------------------ filters menu

    def paintEvent(self, event) -> None:
        # super().paintEvent() FIRST, border SECOND -- reported as not
        # visible at all on a real machine despite passing every pixel
        # check in this sandbox. Likely cause: a real KDE/Plasma-
        # integrated Qt style can have WA_StyledBackground effectively
        # active (this sandbox's offscreen platform doesn't), in which
        # case QWidget's own base paintEvent() actually paints an
        # OPAQUE background via the current style -- if that ran AFTER
        # this border (the previous order), it would silently paint
        # right over it. Calling the base class first and drawing the
        # border on top guarantees the border is always the last thing
        # painted here, regardless of what the base class does on any
        # given platform/style.
        super().paintEvent(event)
        # 3px, 15%-darker-than-itself outline -- per Max's direct
        # request for every "page" (Local/Uploaded/Library/Editor/
        # Settings). Skips the TOP edge specifically: this tab's own
        # content area sits flush against the Library header (the
        # search/refresh/sort row and the Local/Uploaded page-switch
        # buttons) with no gap, so a full outline would visibly
        # double up or bleed into that seam -- "make sure this
        # doesn't bleed into the middle where they combine."
        theme = Theme(config_module.load().appearance)
        paint_page_outline(self, theme.library_background(), skip_top=True)

    def rebuild_sort_popover_pages(self, popover: SortPopover) -> None:
        """Fills `popover`'s three pages with THIS tab's current
        Filters/Sort By/Info state. Called lazily by LibraryPage right
        before showing its one shared SortPopover -- not automatically
        on every refresh() the way the old per-tab QMenu version was,
        since the popover only needs to reflect reality at the moment
        it's actually opened, and rebuilding it on every background
        refresh (e.g. from the DB file watcher) would just be wasted
        work most of the time nobody's even looking at it."""
        popover.set_page_widget(0, self._build_filters_page())
        popover.set_page_widget(1, self._build_sort_page())
        popover.set_page_widget(2, self._build_info_page())

    def _build_filters_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        favorite_checkbox = CustomCheckBox(f"{FAVORITE_STAR} Favorite")
        favorite_checkbox.setChecked(self._favorite_only)
        favorite_checkbox.toggled.connect(self._toggle_favorite_filter)
        layout.addWidget(favorite_checkbox)

        all_tags = library.all_known_tags()
        grouped, uncategorized = library.tags_grouped_by_category()
        tag_icon_paths = library.tag_icons()  # {tag_name: icon_path}, only for tags that have one set

        def _make_checkbox(tag: str, target_layout: QVBoxLayout) -> None:
            leading_icon = None
            icon_path = tag_icon_paths.get(tag)
            if icon_path and Path(icon_path).exists():
                pixmap = QPixmap(icon_path)
                if not pixmap.isNull():
                    leading_icon = pixmap
            checkbox = FilterCheckBox(tag, leading_icon=leading_icon)
            if tag in self._excluded_tags:
                checkbox.set_state(FILTER_STATE_EXCLUDE)
            elif tag in self._active_tags:
                checkbox.set_state(FILTER_STATE_INCLUDE)
            checkbox.state_changed.connect(self._on_filter_state_changed)
            target_layout.addWidget(checkbox)

        if not all_tags:
            no_tags_label = QLabel("(no tags yet)")
            no_tags_label.setStyleSheet("color: gray;")
            layout.addWidget(no_tags_label)

        # Categories render as their own group box (was a side-opening
        # submenu in the old QMenu version -- a fixed page has nowhere
        # for a submenu to open TO, so each category is just its own
        # labeled group instead, in the same vertical flow).
        for category_name, tag_names in grouped.items():
            group = CustomGroupBox(category_name)
            group_layout = group.make_layout(QVBoxLayout)
            for tag in tag_names:
                _make_checkbox(tag, group_layout)
            layout.addWidget(group)

        for tag in uncategorized:
            _make_checkbox(tag, layout)

        add_filter_btn = CustomButton("+ Add Filter")
        add_filter_btn.clicked.connect(self._add_new_filter)
        layout.addWidget(add_filter_btn)

        highlight_checkbox = CustomCheckBox("Highlight Unedited")
        highlight_checkbox.setChecked(self._highlight_unedited)
        highlight_checkbox.toggled.connect(self._toggle_highlight_unedited)
        layout.addWidget(highlight_checkbox)

        layout.addStretch(1)
        return self._wrap_scrollable(page)

    def _build_sort_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        group = QButtonGroup(page)
        # (label, sort_by constant) pairs, each immediately followed by
        # its inverse -- matches the requested ordering of each mode
        # next to its opposite.
        sort_options = [
            ("Creation date (newest first)", library.SORT_CREATED_NEWEST),
            ("Creation date (oldest first)", library.SORT_CREATED_OLDEST),
            ("Last modified (newest first)", library.SORT_MODIFIED_NEWEST),
            ("Last modified (oldest first)", library.SORT_MODIFIED_OLDEST),
            ("Name (A to Z)", library.SORT_NAME_A_TO_Z),
            ("Name (Z to A)", library.SORT_NAME_Z_TO_A),
            ("Video length (short to long)", library.SORT_LENGTH_SHORT_TO_LONG),
            ("Video length (long to short)", library.SORT_LENGTH_LONG_TO_SHORT),
        ]
        for label, sort_by in sort_options:
            radio = CustomRadioButton(label)
            radio.setChecked(sort_by == self._sort_by)
            radio.toggled.connect(lambda checked, s=sort_by: self._set_sort_by(s) if checked else None)
            group.addButton(radio)
            layout.addWidget(radio)

        layout.addStretch(1)
        return self._wrap_scrollable(page)

    def _build_info_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)

        card_info = config_module.load().card_info
        info_options = [
            ("Show Filters", "show_filters", card_info.show_filters),
            ("Show Video Length", "show_length", card_info.show_length),
            ("Show File Size", "show_file_size", card_info.show_file_size),
            ("Show Creation Date", "show_creation_date", card_info.show_creation_date),
            ("Show Action Buttons", "show_action_buttons", card_info.show_action_buttons),
        ]
        for label, field_name, checked in info_options:
            checkbox = CustomCheckBox(label)
            checkbox.setChecked(checked)
            checkbox.toggled.connect(lambda is_checked, f=field_name: self._set_card_info_field(f, is_checked))
            layout.addWidget(checkbox)

        layout.addStretch(1)
        return self._wrap_scrollable(page)

    @staticmethod
    def _wrap_scrollable(page: QWidget) -> QWidget:
        """Used to bound each SortPopover page to a fixed, capped
        height inside a scrolling viewport -- removed entirely per
        Max's own direct suggestion after the padding/cutoff issue
        this was involved in persisted even after the previous fix to
        it (computing each page's own actual content height instead of
        forcing a fixed 320px). The popover itself now simply grows to
        fit however tall a given page's content actually is, with no
        cap and no scrolling at all -- trading "a very long tag list
        could push the popover past the screen" for "there is no
        scrolling-related sizing bug left to have," which is the
        simpler, more reliable trade given the number of attempts the
        capped/scrolling version needed and still didn't fully
        resolve. Kept as a no-op passthrough (not deleted, and every
        call site unchanged) so a future session could reintroduce a
        cap here specifically if a genuinely huge tag list turns out
        to need one."""
        page.setAttribute(Qt.WA_TranslucentBackground, True)
        return page

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
        from .add_filter_dialog import AddFilterDialog
        dialog = AddFilterDialog(library.all_categories(), parent=self)
        if dialog.exec() == QDialog.Accepted:
            name, category_id = dialog.result_values()
            if name:
                library.create_tag(name)
                if category_id is not None:
                    tag_id = next((tid for tid, tname in library.all_tags_with_ids() if tname == name), None)
                    if tag_id is not None:
                        library.set_tag_category(tag_id, category_id)
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

    def _set_card_info_field(self, field_name: str, checked: bool) -> None:
        settings = config_module.load()
        setattr(settings.card_info, field_name, checked)
        config_module.save(settings)
        self.refresh()

    def _set_sort_by(self, sort_by: str) -> None:
        self._sort_by = sort_by
        self.refresh()

    # ------------------------------------------------------------ grid rendering

    def _on_context_menu_opened(self) -> None:
        self._menu_open_count += 1

    def _on_context_menu_closed(self) -> None:
        self._menu_open_count = max(0, self._menu_open_count - 1)
        # A toggle made just before the menu closed may have queued a
        # refresh that got rescheduled below while the menu was still
        # open -- fire it now rather than waiting out another full
        # interval for something the user is already done with.
        if self._menu_open_count == 0 and self._card_signal_debounce.isActive():
            self._card_signal_debounce.stop()
            self.refresh()

    def _on_card_signal_debounce_timeout(self) -> None:
        if self._menu_open_count > 0:
            # Don't rebuild the grid (destroying every VideoCard,
            # including whichever one a currently-open context menu is
            # parented to) while that menu is still open -- reschedule
            # instead of refreshing right now; _on_context_menu_closed
            # picks this up the moment the menu actually closes instead
            # of waiting out a full extra interval.
            self._card_signal_debounce.start()
            return
        self.refresh()

    def refresh(self) -> None:
        # Leading-edge debounce: the FIRST call in any 750ms window acts
        # immediately (a Refresh click should feel instant, not
        # laggy) -- every call after that, until the cooldown clears,
        # is silently dropped. This is a second, independent layer on
        # top of the hide()-before-deleteLater() fix below: that fix
        # makes a stale card disappear immediately once a refresh DOES
        # run, but doesn't stop a rapid burst of clicks from queuing up
        # many full rebuilds back to back in the first place -- on a
        # slow-enough machine (or a large-enough library -- rebuilding
        # is O(number of cards)), enough queued rebuilds can still make
        # things feel like they're "multiplying and messing up
        # scaling" even with that fix in place, simply because there's
        # more mid-rebuild time for it to happen in. Reported directly
        # as still happening.
        if self._refresh_debounce_active:
            return
        self._refresh_debounce_active = True
        self._refresh_cooldown_timer.start()
        self._do_refresh()

    def _clear_refresh_cooldown(self) -> None:
        self._refresh_debounce_active = False

    def _do_refresh(self) -> None:
        # Clear existing cards -- data may have changed (new/deleted
        # video, rename, tag change), so these are rebuilt from scratch
        # rather than reused. Resizing (_relayout below) is the cheaper
        # path that doesn't hit this.
        #
        # hide() before deleteLater(): removing a widget from a layout
        # via takeAt() stops the LAYOUT from managing it, but doesn't
        # hide it -- it stays visible, at wherever its last on-screen
        # position was, until the deferred deletion actually runs.
        # deleteLater()'s deletion is a low-priority event that Qt only
        # processes once the event queue is otherwise idle, so a burst
        # of refresh() calls arriving faster than that (e.g. spam-
        # clicking Refresh or the sidebar Library button) can stack up
        # several still-visible "orphaned" generations of old cards,
        # all overlapping the newest one -- this is confirmed to be
        # exactly what was reported as "spam-clicking the library
        # duplicates/messes up clip sizing": the stale cards were real,
        # still-alive, still-VISIBLE widgets sitting at old geometry,
        # not a duplicate library entry or a sizing calculation bug.
        # refresh()'s own debounce guard above is the OTHER half of
        # actually fixing this -- see its comment.
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
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
                neighbor_provider=self.neighbors,
            )
            card.edit_requested.connect(self.edit_requested.emit)
            card.preview_requested.connect(self.preview_requested.emit)
            card.deleted.connect(lambda _vid: self.refresh())
            card.tags_changed.connect(self._card_signal_debounce.start)
            card.renamed.connect(self._card_signal_debounce.start)
            card.context_menu_opened.connect(self._on_context_menu_opened)
            card.context_menu_closed.connect(self._on_context_menu_closed)
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


class LibraryPage(QWidget):
    edit_requested = Signal(int)  # bubbled up from either tab, for MainWindow to route to Editor
    preview_requested = Signal(object, object)  # video, neighbor_provider -- ditto, for the preview overlay

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(BORDER_WIDTH, BORDER_WIDTH, BORDER_WIDTH, BORDER_WIDTH)

        # Ingest/prune before the tabs build their initial grids, so the
        # very first render already reflects reality (manually-dropped-in
        # clips included) rather than showing stale/incomplete entries
        # until the next refresh. Skipped when offload_library_scan_to_
        # daemon is on -- the daemon does this continuously in the
        # background instead (see daemon.py), and the DB already
        # reflects reality by the time this GUI queries it, without
        # this process ALSO walking the filesystem redundantly.
        if not config_module.load().offload_library_scan_to_daemon:
            library.scan_and_ingest_new_videos()
            library.prune_missing_videos()
            library.remove_stray_orig_entries()
        # Repairs any video whose date got corrupted by the mass-
        # false-positive prune bug (see prune_missing_videos' and
        # repair_incorrect_creation_dates' own docstrings) -- once here
        # at startup regardless of the daemon-offload setting above
        # (this repairs EXISTING bad data already sitting in the DB,
        # it isn't part of the ongoing scan/prune/ingest cycle that
        # setting controls), and cheap/safe to call unconditionally --
        # already-correct videos are a no-op.
        library.repair_incorrect_creation_dates()

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
        self.local_tab.preview_requested.connect(self.preview_requested.emit)
        self.uploaded_tab.preview_requested.connect(self.preview_requested.emit)

        self._stack = QStackedWidget()
        self._stack.addWidget(self.local_tab)
        self._stack.addWidget(self.uploaded_tab)

        appearance = config_module.load().appearance

        # ---- header row: Local/Uploaded's own custom page buttons, the
        # empty space next to them, then the shared Search/Refresh/Sort
        # toolbar -- replaces BOTH the old QTabWidget default page-header
        # behavior (asked to be replaced with real custom headers several
        # times -- see HANDOFF.md) AND the old per-tab search/refresh/sort
        # row that took its own separate space above the grid, per Max's
        # direct instruction to put them "up top... in the empty space"
        # instead of "having their own little space that encroaches on
        # the videos."
        header = QHBoxLayout()
        header.setSpacing(0)

        self.local_btn = LibraryTabButton(resource_qpixmap("local_videos.png"), "left")
        self.local_btn.setToolTip("Local")
        self.uploaded_btn = LibraryTabButton(resource_qpixmap("uploaded_videos.png"), "right")
        self.uploaded_btn.setToolTip("Uploaded")
        self._page_button_group = QButtonGroup(self)
        self._page_button_group.setExclusive(True)
        self._page_button_group.addButton(self.local_btn, 0)
        self._page_button_group.addButton(self.uploaded_btn, 1)
        self.local_btn.setChecked(True)
        self._page_button_group.idClicked.connect(self._switch_page)
        header.addWidget(self.local_btn)
        header.addWidget(self.uploaded_btn)
        header.addStretch(1)

        # Search/Refresh/Sort are shared across both tabs now (one row,
        # not one per tab) -- each acts on whichever tab is CURRENTLY
        # showing (self._stack.currentWidget()), since Filters/Sort/Info
        # state, and the search text itself, still live per-tab (see
        # _VideoGridTab) even though the buttons/popups that control them
        # are shared UI. Icon-only (per Max's provided icon set) rather
        # than text-labeled placeholders now.
        #
        # Circular and 2x the previous implicit size, with real padding
        # between them, per Max's direct request -- these three (only
        # these three) use CustomButton.set_circular() rather than the
        # theme's normal rounded-corner-radius shape. TOOLBAR_BUTTON_
        # DIAMETER doubles what a default un-styled QToolButton's own
        # sizeHint() was landing on here before (roughly 36px for an
        # icon-sized button with no explicit size set at all).
        TOOLBAR_BUTTON_DIAMETER = 72
        TOOLBAR_BUTTON_SPACING = 16
        # Icons are retinted to the card text color (darkened 15%,
        # i.e. .darker(115) in Qt's own inverse-percentage convention
        # -- same idiom as every other "N% darker" spot in this
        # codebase) rather than shown in their own original artwork
        # colors, per Max's direct instruction. tint_pixmap_cached
        # preserves each icon's alpha/shape and just recolors it, so
        # they read as part of the same text system as everything else
        # on a card instead of standing out as separately-colored art.
        icon_tint = QColor(appearance.card_text_color).darker(115)
        self._search_icon = tint_pixmap_cached("search_icon", resource_qpixmap("search_icon.png"), icon_tint)
        self._search_icon_active = tint_pixmap_cached(
            "search_icon_active", resource_qpixmap("search_icon_active.png"), icon_tint
        )
        self.search_btn = CustomButton("Search")
        self.search_btn.setToolTip("Search")
        self.search_btn.set_icon_pixmap(self._search_icon)
        self.search_btn.set_circular(TOOLBAR_BUTTON_DIAMETER)
        self.search_btn.clicked.connect(self._toggle_search_bubble)
        header.addWidget(self.search_btn)
        header.addSpacing(TOOLBAR_BUTTON_SPACING)

        self.refresh_btn = CustomButton("Refresh")
        self.refresh_btn.setToolTip("Refresh")
        self.refresh_btn.set_icon_pixmap(
            tint_pixmap_cached("refresh_icon", resource_qpixmap("refresh_icon.png"), icon_tint)
        )
        self.refresh_btn.set_circular(TOOLBAR_BUTTON_DIAMETER)
        self.refresh_btn.clicked.connect(lambda: self._active_tab().refresh())
        header.addWidget(self.refresh_btn)
        header.addSpacing(TOOLBAR_BUTTON_SPACING)

        self.sort_btn = CustomButton("Sort")
        self.sort_btn.setToolTip("Sort")
        self.sort_btn.set_icon_pixmap(
            tint_pixmap_cached("sort_icon", resource_qpixmap("sort_icon.png"), icon_tint)
        )
        self.sort_btn.set_circular(TOOLBAR_BUTTON_DIAMETER)
        self.sort_btn.clicked.connect(self._open_sort_popover)
        header.addWidget(self.sort_btn)

        layout.addLayout(header)
        layout.addWidget(self._stack, stretch=1)

        # Shared popups -- one instance each, re-pointed at whichever tab
        # is currently active rather than one per tab (see module-level
        # comments on _VideoGridTab's own search_edit/rebuild_sort_
        # popover_pages for why this is safe: only one tab is ever
        # visible/interactive at a time).
        self.search_bubble = SearchBubble(self)
        self.search_bubble.line_edit.textChanged.connect(self._on_search_text_typed)
        self.search_bubble.search_confirmed.connect(self._on_search_confirmed)
        self.sort_popover = SortPopover(self)

        # Base size for the two page buttons' icons -- same "4.5x the
        # style's own default tab-bar icon size" starting point as
        # before, just no longer read off a QTabWidget's own style()
        # since there isn't one anymore.
        self._base_tab_icon_size = round(
            QApplication.style().pixelMetric(QStyle.PM_TabBarIconSize) * 4.5
        )
        self._current_tab_icon_size = self._base_tab_icon_size
        self._rebuild_tab_icon_sizes()

        # Shown when the Editor is opened with no video ever having been
        # selected -- MainWindow redirects here and calls
        # show_status_message() instead of leaving the Editor on its own
        # empty state.
        self.status_bar = QLabel("")
        self.status_bar.setAlignment(Qt.AlignCenter)
        self.status_bar.setStyleSheet("QLabel { background: palette(midlight); padding: 6px; }")
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

    # ------------------------------------------------------------ page switching

    def paintEvent(self, event) -> None:
        # super() first, border second -- see _VideoGridTab's own
        # paintEvent comment for why this order matters.
        super().paintEvent(event)
        theme = Theme(config_module.load().appearance)
        paint_page_outline(self, theme.library_background())

    def _active_tab(self) -> "_VideoGridTab":
        return self._stack.currentWidget()

    def _switch_page(self, index: int) -> None:
        crossfade_to_index(self._stack, index)
        self._sync_search_bubble_for_active_tab()

    # ------------------------------------------------------------ shared search bubble

    def _toggle_search_bubble(self) -> None:
        """Opens the comic-bubble-style search popup attached under the
        Search button (see SearchBubble). Qt.Popup already closes it on
        an outside click, so a second click on this same button (which
        counts as "outside" the popup, per Qt's own popup-grab
        handling) will typically be seen as already-hidden by the time
        this runs and just reopen it -- text isn't cleared either way,
        so repeated toggling picks up right where it left off."""
        if self.search_bubble.isVisible():
            self.search_bubble.hide()
        else:
            self._sync_search_bubble_for_active_tab()
            self.search_bubble.show_below(self.search_btn)

    def _on_search_text_typed(self, text: str) -> None:
        """Typing alone no longer runs the search (see SearchBubble's
        own docstring) -- this only updates the Search button's icon so
        it reflects what's currently TYPED, even before it's actually
        been confirmed. The active tab's own search text (and therefore
        the actual filtering) is only updated in _on_search_confirmed."""
        self._update_search_icon(text)

    def _on_search_confirmed(self) -> None:
        """Fired by SearchBubble.search_confirmed -- Enter pressed, or
        its confirm checkbox clicked. This is the ONE place that writes
        into the active tab's own search_edit, which is what actually
        drives that tab's existing textChanged -> _do_refresh wiring."""
        text = self.search_bubble.line_edit.text()
        self._active_tab().search_edit.setText(text)
        self._update_search_icon(text)

    def _sync_search_bubble_for_active_tab(self) -> None:
        text = self._active_tab().search_edit.text()
        self.search_bubble.line_edit.blockSignals(True)
        self.search_bubble.line_edit.setText(text)
        self.search_bubble.line_edit.blockSignals(False)
        self._update_search_icon(text)

    def _update_search_icon(self, text: str) -> None:
        """Swap the Search button's own icon for the "active search"
        variant Max provided whenever the active tab's search box holds
        actual text, per his direct instruction -- back to the plain
        icon once it's empty again."""
        self.search_btn.set_icon_pixmap(self._search_icon_active if text.strip() else self._search_icon)

    # ------------------------------------------------------------ shared sort popover

    def _open_sort_popover(self) -> None:
        self._active_tab().rebuild_sort_popover_pages(self.sort_popover)
        self.sort_popover.show_below(self.sort_btn)

    # ------------------------------------------------------------ misc

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
        out of the list instead of lingering as broken entries forever.
        Skipped when offload_library_scan_to_daemon is on -- see this
        class's __init__ for the full reasoning; the tabs' own
        refresh() below still runs either way, since that's just a DB
        re-query (cheap, no filesystem walk) that needs to happen
        regardless of who's doing the scanning."""
        if not config_module.load().offload_library_scan_to_daemon:
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

    def _rebuild_tab_icon_sizes(self) -> None:
        """(Re)size the Local/Uploaded page buttons' own icons at their
        independent sizes (Saved Videos Icon Size / Uploaded Videos Icon
        Size in Settings > General), against self._current_tab_icon_size
        as the 100% baseline. Called at construction and from
        apply_scale() whenever the baseline changes. No more shared-
        iconSize limitation to work around (see LibraryTabButton) --
        each button just gets its own target size directly."""
        appearance = config_module.load().appearance
        base = self._current_tab_icon_size
        local_target = max(1, round(base * appearance.saved_videos_icon_size / 100))
        uploaded_target = max(1, round(base * appearance.uploaded_videos_icon_size / 100))
        self.local_btn.set_icon_target_size(local_target)
        self.uploaded_btn.set_icon_target_size(uploaded_target)

    def apply_scale(self, factor: float) -> None:
        self.local_tab.apply_scale(factor)
        self.uploaded_tab.apply_scale(factor)
        # Was previously set once at construction and never touched
        # again, so it stayed fixed regardless of window size while
        # everything else scaled -- now rescaled live alongside them.
        size = max(round(self._base_tab_icon_size * factor), 8)
        self._current_tab_icon_size = size
        self._rebuild_tab_icon_sizes()
