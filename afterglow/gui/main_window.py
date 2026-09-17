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

The sidebar's width and each nav icon's size both scale with the window
rather than staying fixed -- a fixed 72px sidebar with fixed 32px icons
looked proportionally too small once the window was large/fullscreen.

All three nav buttons are now `LibraryTabButton` -- "the same custom
button type as switching between local/uploaded videos in the library,"
per Max's direct request -- rather than the old `_ScalingIconButton`
(border-gradient images, hue shift, per-button brightness multipliers,
icon darkening, a pulse animation) that lived here through many past
sessions. That whole system is gone now, not just visually hidden: no
border ring at all, every button fully rounded on all four corners
(there's no touching seam anymore either, now that real padding sits
between them -- see SIDEBAR_SPACING below), and the same simple
turquoise-fill-dims-when-inactive look the Library page's own Local/
Uploaded buttons have.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QButtonGroup, QStackedWidget, QSizePolicy,
)

from .. import config as config_module
from .settings_page import SettingsPage
from .library_page import LibraryPage, LibraryTabButton
from .video_preview_dialog import VideoPreviewOverlay
from .editor_page import EditorPage
from .resources import resource_qpixmap
from .scaling import compute_scale
from .theme import Theme
from .scale_reveal import crossfade_to_index
from .page_outline import paint_page_outline

# Indices into self.stack -- fixed at construction time (see __init__).
_SETTINGS_INDEX = 0
_LIBRARY_INDEX = 1
_EDITOR_INDEX = 2

SIDEBAR_WIDTH_FRACTION = 0.07  # of the whole window's width
SIDEBAR_MIN_WIDTH = 64
SIDEBAR_MAX_WIDTH = 140

# Padding around/between the sidebar's own three buttons -- NOT a fixed
# constant, but the exact same appearance.ui_padding used for the
# Library grid's own card-to-card spacing, per Max's direct request
# ("ensure that the padding between them is the same padding between
# videos"). Read once at construction; a live-settings-change mid-
# session isn't retrofitted here any more than the sidebar's other
# construction-time values are (see this module's own docstring).


class _Sidebar(QWidget):
    """Plain QWidget subclass purely so it can paint its own 3px,
    15%-darker-than-itself outline -- "sidebar" is one of the page
    elements Max asked for this on, alongside Local/Uploaded/Library/
    Editor/Settings. No explicit background of its own (inherits
    MainWindow's central-widget app_background()), same basis as
    Editor/Settings' own outlines."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # first -- see EditorPage's own paintEvent comment for why
        theme = Theme(config_module.load().appearance)
        paint_page_outline(self, theme.app_background())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("afterglow")
        # 16:9-ish and reasonably large by default, matching the
        # 1920x1080 reference-scale baseline in scaling.py -- the
        # previous 1000x700 (a boxier ~10:7 ratio) was what read as
        # "opens vertically maximized and horizontally somewhat slim"
        # on a widescreen display, since a non-widescreen-ish window
        # size looks comparatively narrow next to a fullscreen-shaped
        # taskbar/monitor.
        self.resize(1600, 900)

        appearance = config_module.load().appearance
        theme = Theme(appearance)

        central = QWidget()
        self.setCentralWidget(central)
        # App-wide background -- always wired in (same pattern as
        # VideoCard's card_background()/accent(): Theme itself decides
        # whether this is Afterglow's fixed color or a live KDE-palette
        # equivalent, this call doesn't need its own separate check).
        #
        # QPalette, not setStyleSheet("background-color: ...") -- an
        # unscoped stylesheet property is a well-known Qt gotcha where
        # the style engine applies it across the WHOLE descendant
        # subtree (effectively "* { ... }"), which can bleed into other
        # widgets' own custom painting on a real compositor even though
        # they have nothing to do with this one -- and this sandbox's
        # offscreen platform doesn't reliably reproduce that same
        # cascading, so testing clean here isn't proof it's safe on a
        # real display. central being the single ancestor of literally
        # everything else in the app makes this the highest-risk place
        # in the whole codebase for that mistake specifically. Setting
        # the palette directly instead affects only this one widget.
        central.setAutoFillBackground(True)
        central_palette = central.palette()
        central_palette.setColor(central.backgroundRole(), theme.app_background())
        central.setPalette(central_palette)
        layout = QHBoxLayout(central)

        # ---- sidebar: Library + Editor (icon-only, fill the height),
        # gear (Settings) pinned to the bottom ----
        self.sidebar = _Sidebar()
        self.sidebar.setFixedWidth(round(self.width() * SIDEBAR_WIDTH_FRACTION))
        sidebar_layout = QVBoxLayout(self.sidebar)
        # Same padding as the Library grid's own card-to-card spacing,
        # not a separate hardcoded constant -- see module docstring.
        sidebar_padding = appearance.ui_padding
        sidebar_layout.setContentsMargins(
            sidebar_padding, sidebar_padding, sidebar_padding, sidebar_padding
        )
        sidebar_layout.setSpacing(sidebar_padding)
        layout.addWidget(self.sidebar)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        # All three fully rounded now ('full' -- no touching-seam corner
        # skipping needed anymore, now that real padding separates them)
        # and no border-gradient system at all -- see module docstring.
        self.library_nav_btn = LibraryTabButton(resource_qpixmap("library.png"), "full")
        self.library_nav_btn.setToolTip("Library")
        self.editor_nav_btn = LibraryTabButton(resource_qpixmap("editor.png"), "full")
        self.editor_nav_btn.setToolTip("Editor")
        self.settings_nav_btn = LibraryTabButton(resource_qpixmap("settings.png"), "full")
        self.settings_nav_btn.setToolTip("Settings")

        # Library and Editor stretch to fill most of the sidebar's
        # vertical space; the gear stays a fixed small size at the bottom
        # -- just tall enough for its icon (same size as the other two)
        # plus a little padding, set alongside the sidebar width below.
        self.library_nav_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.editor_nav_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        sidebar_layout.addWidget(self.library_nav_btn, stretch=1)
        sidebar_layout.addWidget(self.editor_nav_btn, stretch=1)
        self.settings_nav_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
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
        # Left-click on a card's thumbnail -- opens the preview overlay
        # (see video_preview_dialog.py's own module docstring for why
        # this is an embedded overlay, not a separate top-level window).
        self.library_page.preview_requested.connect(self._show_preview_overlay)
        self._preview_overlay = None
        # Prev/Next arrows in the Editor -- see EditorPage.set_neighbor_provider
        # and LibraryPage.neighbors_for's own docstrings for how this stays
        # live rather than being a one-time snapshot of the video list.
        self.editor_page.set_neighbor_provider(self.library_page.neighbors_for)

        self.library_nav_btn.setChecked(True)
        self.stack.setCurrentIndex(_LIBRARY_INDEX)

    def _show_preview_overlay(self, video, neighbor_provider) -> None:
        # Replaces whichever overlay might already be open rather than
        # stacking a second one on top -- "even allows you to open two"
        # was a real bug in the old separate-top-level-window version,
        # which had nothing here to prevent exactly that.
        if self._preview_overlay is not None:
            self._preview_overlay.close_overlay(immediate=True)
        overlay = VideoPreviewOverlay(video, neighbor_provider=neighbor_provider, parent=self.centralWidget())
        overlay.setGeometry(self.centralWidget().rect())
        overlay.closed.connect(self._on_preview_overlay_closed)
        overlay.show()
        overlay.raise_()
        self._preview_overlay = overlay

    def _on_preview_overlay_closed(self) -> None:
        self._preview_overlay = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._preview_overlay is not None:
            self._preview_overlay.setGeometry(self.centralWidget().rect())
        width = round(self.width() * SIDEBAR_WIDTH_FRACTION)
        width = max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, width))
        self.sidebar.setFixedWidth(width)

        # Icon size for all three nav buttons scales off the sidebar's
        # own width, same "just enough padding" idea the old
        # icon_size_for_width() used -- width is always the limiting
        # dimension here since the sidebar is tall and narrow.
        appearance = config_module.load().appearance
        icon_padding = 14
        base_icon_size = max(width - icon_padding, 8)
        self.library_nav_btn.set_icon_target_size(round(base_icon_size * appearance.library_icon_size / 100))
        self.editor_nav_btn.set_icon_target_size(round(base_icon_size * appearance.editor_icon_size / 100))
        settings_icon_size = round(base_icon_size * appearance.settings_icon_size / 100)
        self.settings_nav_btn.set_icon_target_size(settings_icon_size)
        # Settings' fixed height is derived from its own icon size plus
        # a little padding -- "just enough padding" rather than a fixed
        # box regardless of how big its icon actually is.
        self.settings_nav_btn.setFixedHeight(settings_icon_size + 12)

        # "The current scale is great for fullscreen -- scale everything
        # based on the window size." 1920x1080 is treated as the 1.0x
        # baseline (see scaling.py) that the various hardcoded sizes
        # above were tuned against, so this scales the Editor's
        # trim-timeline/volume-bar sizing and the Library cards' title
        # font up or down together as the window resizes, rather than
        # them staying fixed while just the sidebar/its icons scaled.
        scale = compute_scale(self.width(), self.height())
        self.editor_page.apply_scale(scale)
        self.library_page.apply_scale(scale)

    def _on_nav_clicked(self, index: int) -> None:
        if index == _LIBRARY_INDEX:
            # Direct click on Library (not the Editor-redirect case
            # below, which sets its own message right after this) --
            # clear any "Select a video." prompt left over from an
            # earlier redirect, since it no longer applies once the
            # user has actively chosen to look at the Library.
            self.library_page.clear_status_message()

        if index == _SETTINGS_INDEX:
            # Settings is built once at startup (like the other two
            # pages) and never rebuilt on nav -- refresh whatever it
            # shows that can change while the app's been running
            # (filters created/renamed from the Library since launch).
            # Deferred via singleShot(0), NOT called directly here --
            # this used to run BEFORE crossfade_to_index below, meaning
            # the page switch itself couldn't even START rendering
            # until this finished. Still reported as a perceptible
            # delay ("still page switch lag") even after last round's
            # fix to crossfade_to_index itself, which only addressed
            # ONE source of blocking (the old pre-switch snapshot grab)
            # -- this synchronous refresh call was a SECOND, independent
            # one sitting right next to it. singleShot(0, ...) schedules
            # it to run on the next event-loop iteration instead of
            # blocking this one, so Qt gets a chance to actually PAINT
            # the already-switched page first.
            QTimer.singleShot(0, self.settings_page.refresh_dynamic_lists)

        if index == _EDITOR_INDEX and self.editor_page.current_video_id is None:
            # Nothing has ever been loaded into the Editor -- go to the
            # Library instead and prompt there, rather than showing the
            # Editor's own empty state. Re-check Library so the nav
            # buttons stay in sync with what's actually on screen.
            self.library_nav_btn.setChecked(True)
            index = _LIBRARY_INDEX
            self.library_page.show_status_message("Select a video.")

        crossfade_to_index(self.stack, index)
        # Deliberately NOT refreshing the Library here anymore -- it
        # used to call library_page.refresh() (a full filesystem scan +
        # every VideoCard rebuilt from scratch) on every single switch
        # TO Library, even via the deferred singleShot(0) from last
        # round's fix. That deferral only moved WHEN the block happened,
        # not whether it happened -- rebuilding potentially hundreds of
        # cards is real, unavoidable CPU work regardless of scheduling,
        # and still read as "lag" once it actually ran. Per Max's own
        # suggestion ("maybe just leave the pages loaded after switching
        # off of them"): the Library page now simply stays exactly as
        # it was the last time anything actually changed it -- the
        # existing DB-file-watcher (_on_db_file_changed, further down in
        # library_page.py) still refreshes it automatically whenever the
        # daemon or the Editor actually writes to the database, and the
        # Library's own Refresh button is still right there for a
        # manual one. Switching TO Library is now just a plain,
        # instant page switch, nothing more.
        if index == _LIBRARY_INDEX:
            QTimer.singleShot(0, self.library_page.refresh)

    def _open_in_editor(self, video_id: int) -> None:
        self.editor_page.load_video(video_id)
        self.editor_nav_btn.setChecked(True)
        crossfade_to_index(self.stack, _EDITOR_INDEX)
