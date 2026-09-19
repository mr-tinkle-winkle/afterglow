"""
One clickable card in the Library grid: thumbnail + title below it, with a
right-click context menu (Edit / Rename / Favorite / Upload / Filters /
Copy / Delete). Multi-select aware: right-clicking a card that's part of
the current multi-selection applies these to the whole selection, not
just the one that was clicked.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
import tempfile
import shutil

from PySide6.QtCore import Qt, Signal, QSize, QRectF, QTimer, QVariantAnimation, QEvent
from PySide6.QtGui import QPixmap, QPainter, QColor, QIcon, QFontMetrics, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QMenu, QMessageBox, QLineEdit,
    QHBoxLayout, QInputDialog, QWidgetAction, QDialog, QApplication,
)

from .. import library, thumbnails, config as config_module
from .resources import resource_qpixmap
from .pixmap_effects import resolve_border_pixmap, hue_shift_pixmap_cached, silhouette_outline_pixmap_cached
from .rounded_rect import rounded_rect_path, round_pixmap_corners
from .theme import Theme
from .outlined_label import OutlinedLabel
from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .custom_line_edit import CustomLineEdit

THUMB_SIZE = QSize(400, 224)  # 16:9, doubled from the original 200x112
FAVORITE_STAR = "\u2605"  # "★"

# UI Update Phase 2 (card restructure): the outer card is a "background"
# box; the gap between its own edge and its two children (the video
# box, the info box), the gap between those two children, AND the info
# box's own internal margin for ITS children are all driven by the
# single "Padding" setting (AppearanceSettings.ui_padding, read fresh
# per-card at construction) rather than separate hardcoded constants --
# "adjusts the pixels of padding used everywhere", per how this was
# actually asked for. Generous enough by default that a sliver of the
# background portrusion stays visible on every side of the card, per
# Max's ask, regardless of where you look.

# 3x the original 18px icon size, per request -- ICON_SPACING between
# each. When more filter icons are on one video than fit at that size
# within the thumbnail's own width (horizontal rows) or height
# (vertical tiling), _icon_size_for_count scales all of them down
# together to fit, rather than letting the row overflow the card.
BASE_ICON_SIZE = 54
ICON_SPACING = 4
MIN_ICON_SIZE = 16


def _icon_size_for_count(count: int, available: int, base_size: int = BASE_ICON_SIZE) -> int:
    if count <= 0:
        return base_size
    natural_total = count * base_size + (count - 1) * ICON_SPACING
    if natural_total <= available:
        return base_size
    fitted = (available - (count - 1) * ICON_SPACING) // count
    return max(fitted, MIN_ICON_SIZE)


def _format_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _format_file_size(path: str) -> str | None:
    try:
        size_bytes = Path(path).stat().st_size
    except OSError:
        return None
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return None


def _format_date(created_at: str) -> str | None:
    try:
        dt = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    # Reads the setting fresh each call (same pattern as every other
    # appearance-driven paint/format helper in this codebase) rather
    # than threading a parameter through every call site -- this is
    # called from both VideoCard's own info box and the video
    # previewer's header, and both should reflect the setting equally.
    if config_module.load().appearance.extended_dates:
        return dt.strftime("%b %d, %Y %I:%M:%S %p")
    return dt.strftime("%b %d, %Y")


def _placeholder_pixmap() -> QPixmap:
    pixmap = QPixmap(THUMB_SIZE)
    pixmap.fill(QColor("#2a2a2a"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#888"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "No preview")
    painter.end()
    return pixmap


class _FilterIconLabel(QLabel):
    """One filter icon rendered over/under a card's thumbnail. Hovering
    shows the filter's name; left-click and right-click mirror the
    Filters dropdown's own include/block gestures (see FilterCheckBox
    in library_page.py) rather than requiring the dropdown to be opened
    just to toggle one filter you can already see on the card."""

    left_clicked = Signal(str)   # tag_name -- toggle "filter for this"
    right_clicked = Signal(str)  # tag_name -- toggle "block this"

    def __init__(self, tag_name: str, pixmap: QPixmap, size: int, parent=None):
        super().__init__(parent)
        self._tag_name = tag_name
        if not pixmap.isNull():
            self.setPixmap(pixmap.scaled(QSize(size, size), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setToolTip(tag_name)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.left_clicked.emit(self._tag_name)
        elif event.button() == Qt.RightButton:
            self.right_clicked.emit(self._tag_name)
        super().mousePressEvent(event)


class _InfoBox(QWidget):
    """UI Update Phase 2: the inner "info" box holding title + info/date
    lines + below-location filter icons + tag names -- painted with its
    own rounded, theme-colored background (Afterglow Theme's accent
    color, same as most buttons -- see Theme.accent()'s docstring),
    sitting BEHIND its own children. Children are inset from its edges
    by INFO_BOX_PADDING, comfortably clear of the corner radius, so no
    per-pixel child masking is needed for the rounding to look right --
    nothing ever reaches into the curved area to begin with."""

    def __init__(self, appearance: "config_module.AppearanceSettings", parent=None):
        super().__init__(parent)
        self._appearance = appearance
        self._theme = Theme(appearance)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            appearance.ui_padding, appearance.ui_padding, appearance.ui_padding, appearance.ui_padding
        )
        layout.setSpacing(2)
        self.content_layout = layout
        self._bg_cache: QPixmap | None = None

    def paintEvent(self, event) -> None:
        # Cached rather than rebuilt (rounded-rect path construction +
        # fill) on every repaint -- this box's own appearance never
        # changes after construction, only its SIZE (once, when the
        # layout first settles), so there's nothing to gain by redoing
        # this work on every scroll-triggered repaint. See video_card.py's
        # matching comment on VideoCard's own background cache for the
        # full reasoning (reported directly as low scroll frame rate).
        if self._bg_cache is None or self._bg_cache.size() != self.size():
            pixmap = QPixmap(self.size())
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            if self._appearance.rounded_corners_enabled:
                painter.setClipPath(rounded_rect_path(QRectF(self.rect()), self._appearance.rounded_corner_radius))
            painter.fillRect(self.rect(), self._theme.accent())
            painter.end()
            self._bg_cache = pixmap
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._bg_cache)
        painter.end()
        super().paintEvent(event)


def _menu_stylesheet(appearance) -> str:
    """QSS reskin for QMenu -- background/text/hover colors matching the
    app's own theme, plus rounded corners and an accent border, instead
    of native/KDE menu chrome. Applied to every QMenu (and each nested
    submenu, since Qt does NOT cascade a parent QMenu's stylesheet down
    into its child QMenus automatically) used for the right-click
    context menu and the Filters submenu -- both reported directly as
    "not custom". QSS is the standard, supported way to reskin QMenu's
    look without losing its own submenu/keyboard-navigation/hover
    machinery, which would be substantial to rebuild from scratch."""
    return f"""
        QMenu {{
            background-color: {appearance.afterglow_color_card_background};
            color: {appearance.card_text_color};
            border: 1px solid {appearance.afterglow_color_accent};
            border-radius: 8px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 6px 24px 6px 12px;
            border-radius: 6px;
        }}
        QMenu::item:selected {{
            background-color: {appearance.afterglow_color_accent};
        }}
        QMenu::separator {{
            height: 1px;
            background: {appearance.afterglow_color_accent};
            margin: 4px 8px;
        }}
    """


class _NonClosingMenu(QMenu):
    """A QMenu that doesn't close itself when the click landed on a
    QWidgetAction's own embedded widget (a CustomCheckBox, here) --
    used for the right-click context menu itself, the Filters
    submenu, and its category sub-menus, so toggling several
    checkboxes (filters, or the "Edited" checkbox) in one visit
    doesn't require reopening the menu after each one. The checkbox
    itself already receives and handles the click perfectly normally
    (Qt delivers mouse events directly to whichever real widget is
    under the cursor, completely independent of QMenu's own mouse
    handling) -- this only skips QMenu's OWN reaction of closing
    itself afterward for that case, leaving every other kind of click
    (a plain QAction, clicking outside any item) to close the menu
    exactly as before.

    Checks BOTH self.activeAction() (Qt's own hover-tracked "current"
    action) AND self.actionAt(event.pos()) (a plain position lookup,
    independent of hover-tracking state entirely) -- reported directly
    that checking activeAction() alone still wasn't reliably catching
    this, so actionAt() is a second, hover-independent way to reach
    the same conclusion. Overrides mousePressEvent too, not just
    mouseReleaseEvent, in case whatever was still causing the close
    was reacting to the press half of the click rather than (or in
    addition to) the release."""

    def _is_widget_action_click(self, pos) -> bool:
        action = self.activeAction() or self.actionAt(pos)
        return isinstance(action, QWidgetAction)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._suppress_next_hide = False

    def hideEvent(self, event) -> None:
        # A SECOND, independent layer on top of the mousePressEvent/
        # mouseReleaseEvent overrides below -- reported directly, a
        # THIRD time, that the menu still closes on a checkbox click
        # despite those. QMenu almost certainly has some internal
        # closing mechanism that doesn't route through a Python
        # subclass's mousePressEvent/mouseReleaseEvent overrides at
        # all (the same category of PySide6 limitation already
        # confirmed for CustomGroupBox's setLayout() override -- an
        # internal C++-side call not dispatching to the Python
        # override). Rather than keep guessing WHICH internal call is
        # responsible, this reacts to the OUTCOME instead: whatever
        # triggered it, if the menu is trying to hide right after a
        # widget-action click, un-hide it immediately by re-showing at
        # its own current position. _suppress_next_hide is set the
        # moment a press lands on a widget action and cleared right
        # after being consumed here, so this never blocks a REAL close
        # (clicking outside, pressing Escape, choosing a plain action).
        if self._suppress_next_hide:
            self._suppress_next_hide = False
            event.ignore()
            pos = self.pos()
            self.show()
            self.move(pos)
            return
        super().hideEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._is_widget_action_click(event.pos()):
            self._suppress_next_hide = True
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._is_widget_action_click(event.pos()):
            return
        super().mouseReleaseEvent(event)


class VideoCard(QWidget):
    edit_requested = Signal(int)      # video_id
    deleted = Signal(int)             # video_id
    upload_requested = Signal(int)    # video_id
    tags_changed = Signal()           # tag added/removed -- parent should refresh filter list
    renamed = Signal()                # title changed -- parent should refresh (search may no longer match)
    filter_left_clicked = Signal(str)   # tag_name, from clicking an icon on the card itself
    filter_right_clicked = Signal(str)  # tag_name, ditto (block)
    clicked = Signal(int, object)       # video_id, Qt.KeyboardModifiers -- parent handles selection
    preview_requested = Signal(object, object)  # video, neighbor_provider -- bubbles up to MainWindow
    context_menu_opened = Signal()  # a menu.exec() is about to block -- parent should pause any grid rebuild
    context_menu_closed = Signal()  # that exec() returned -- safe to rebuild again

    def __init__(self, video: "library.Video", parent=None, highlight_enabled: bool = True,
                 font_scale: float = 1.0, get_selected_ids=None, ensure_selected=None,
                 neighbor_provider=None):
        super().__init__(parent)
        self.video_id = video.id
        self._video = video
        self._preview_pending = False
        self._title_edit = None
        self._highlight_enabled = highlight_enabled
        settings = config_module.load()
        self._appearance = settings.appearance
        # A custom image (Settings > General) takes the place of the
        # built-in gradient for the unedited-clip highlight -- one
        # shared setting for this border type (see
        # AppearanceSettings.unedited_border_image_path's own comment).
        # hue_shift_pixmap_cached rather than the plain version: this
        # constructor runs once per VIDEO in the grid, so without the
        # cache a non-default hue shift's cost would multiply by however
        # many cards are on screen instead of happening once.
        _highlight_base = resolve_border_pixmap(
            self._appearance.unedited_border_image_path,
            resource_qpixmap("unedited_highlight_gradient.png"),
        )
        self._highlight_pixmap = hue_shift_pixmap_cached(
            self._appearance.unedited_border_image_path or "unedited_highlight_gradient.png",
            _highlight_base, self._appearance.unedited_border_hue_shift,
        )
        # Selection border: a bundled gold/white gradient by default
        # (selected_border_gradient.png), overridable the same way as
        # the unedited highlight above -- no hue-shift for this one,
        # since that wasn't asked for.
        self._selected_border_pixmap = resolve_border_pixmap(
            self._appearance.selected_border_image_path,
            resource_qpixmap("selected_border_gradient.png"),
        )
        self._selected = False
        self._bg_cache: QPixmap | None = None
        # Fade state for set_selected()'s transition -- see its own
        # comment. _fade_from holds the pre-change cached pixmap while
        # a transition is in progress (None once settled); progress is
        # the 0->1 blend amount toward whatever _bg_cache currently is.
        self._fade_from: QPixmap | None = None
        self._selection_fade_progress = 1.0
        self._selection_fade_anim: QVariantAnimation | None = None
        # Both optional and both supplied together by _VideoGridTab (see
        # its refresh()) -- let the right-click context menu act on the
        # WHOLE current multi-selection instead of just this one card.
        # get_selected_ids: () -> set[int], the tab's current selection.
        # ensure_selected: (int) -> None, called first on right-click so
        # right-clicking a card that ISN'T part of the current selection
        # replaces the selection with just that card first (standard
        # file-manager convention), rather than leaving some unrelated
        # other selection in place while acting on the newly-clicked one.
        self._get_selected_ids = get_selected_ids
        self._ensure_selected = ensure_selected
        # (int) -> (Video | None, Video | None) -- passed straight
        # through to VideoPreviewDialog for its own prev/next arrows,
        # same callable shape as MainWindow's Editor neighbor_provider
        # (see LibraryPage.neighbors_for), just reused here for the
        # preview dialog instead of the Editor.
        self._neighbor_provider = neighbor_provider
        self._theme = Theme(self._appearance)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(
            self._appearance.ui_padding, self._appearance.ui_padding,
            self._appearance.ui_padding, self._appearance.ui_padding,
        )
        outer_layout.setSpacing(self._appearance.ui_padding)

        display_settings = settings.filter_display
        info_settings = settings.card_info
        icons = library.tag_icons()
        self._tag_outline_colors = library.tag_outline_colors()
        show_filters = info_settings.show_filters
        matching = [(t, icons[t]) for t in video.tags if t in icons]

        # ---- video box: thumbnail + above/vtile-location filter icons ----
        # Row/column existence below is gated ONLY on the global
        # settings (show_filter_icons + location), never on whether
        # THIS particular video happens to have any matching tags --
        # see _build_icon_row's own comment for why that distinction is
        # what actually makes every card come out the same size ("all
        # clips should be the same size" -- previously a video with 0
        # matching tags just skipped the row entirely, making its card
        # shorter/narrower than one with tags, even under identical
        # settings).
        if show_filters and display_settings.show_filter_icons and \
                display_settings.filter_icon_location == "above":
            outer_layout.addWidget(self._build_icon_row(matching, vertical=False))

        thumb_row = QHBoxLayout()
        if show_filters and display_settings.show_filter_icons and \
                display_settings.filter_icon_location == "vtile_left":
            thumb_row.addWidget(self._build_icon_row(matching, vertical=True))

        # video_box wraps thumb_label with a fixed margin equal to the
        # border width -- gives paintEvent an actual gap to draw the
        # unedited-highlight's "border for the video" into (see its own
        # docstring/comment there). video_box.geometry() is VideoCard-
        # relative directly (widgets added to a nested LAYOUT, as
        # opposed to a nested WIDGET, are still direct children of
        # whichever widget owns the outer layout -- layouts aren't
        # QWidgets and can't be parents), so no coordinate mapping is
        # needed when painting against it later.
        video_border_width = self._appearance.unedited_selected_border_width
        self.video_box = QWidget()
        self.video_box.setFixedSize(
            THUMB_SIZE.width() + 2 * video_border_width,
            THUMB_SIZE.height() + 2 * video_border_width,
        )
        video_box_layout = QVBoxLayout(self.video_box)
        video_box_layout.setContentsMargins(
            video_border_width, video_border_width, video_border_width, video_border_width
        )
        video_box_layout.setSpacing(0)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(THUMB_SIZE)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setPixmap(self._load_pixmap(video))
        video_box_layout.addWidget(self.thumb_label)
        thumb_row.addWidget(self.video_box)

        if show_filters and display_settings.show_filter_icons and \
                display_settings.filter_icon_location == "vtile_right":
            thumb_row.addWidget(self._build_icon_row(matching, vertical=True))
        outer_layout.addLayout(thumb_row)

        # ---- info box: title, info/date lines, below-location icons, tag names ----
        self.info_box = _InfoBox(self._appearance)
        info_layout = self.info_box.content_layout

        self.title_label = OutlinedLabel()
        # Word wrap is now permanently off (was previously toggled by
        # Resize Text to Fit) -- a wrapped, variable-line-count title
        # was the single biggest source of card-to-card height
        # variance. set_font_scale() below always elides an overlong
        # title to a single line as a hard backstop, on top of Resize
        # Text to Fit's existing font-shrinking (which still runs
        # first, for readability, when that setting's on) -- so every
        # card's title row is exactly one line tall, always, regardless
        # of how long any given video's title is.
        self.title_label.setWordWrap(False)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setFixedWidth(THUMB_SIZE.width())
        self.title_label.set_colors(
            self._appearance.card_text_color, self._appearance.card_text_outline_color,
            outline_width=self._appearance.card_text_outline_width,
        )
        # 1.875x the app's actual default label size -- was 2.5x, then
        # asked to be brought down to 75% of that (2.5 * 0.75 = 1.875).
        # Additionally scaled by font_scale to track the window's overall
        # size (see MainWindow.resizeEvent / LibraryPage.apply_scale) --
        # base_pt is cached so later font_scale changes (via
        # set_font_scale) don't compound on top of an already-scaled
        # value.
        self._base_title_pt = self.title_label.font().pointSizeF()
        if self._base_title_pt <= 0:  # some platforms report pixel-based fonts instead
            self._base_title_pt = 9.0
        self._base_title_pt *= 1.875
        self._current_font_scale = font_scale
        self._full_title_text = self._title_text(video)
        info_layout.addWidget(self.title_label)

        # Info line(s), per the Library's "Info" dropdown: length + file
        # size on one line (size after length), creation date on its
        # own line under that -- both above the filters section, in
        # that fixed order, each independently toggleable. Always
        # created (with a placeholder space if this particular video
        # has nothing to show) whenever its setting is on, same
        # same-size-regardless-of-per-video-data reasoning as the icon
        # row above.
        #
        # SMALL_TEXT_OUTLINE_SCALE: the info/date/tag lines below are
        # all fixed at 10px (see each one's own setStyleSheet call),
        # much smaller than the title -- using the SAME outline width
        # on both made the smaller text's outline look proportionally
        # much thicker relative to its own glyph strokes, reported
        # directly. Scaled down rather than given a totally separate
        # setting, so a single "Text Outline Width" slider still
        # controls both, just proportionally.
        SMALL_TEXT_OUTLINE_SCALE = 0.5
        small_text_outline_width = self._appearance.card_text_outline_width * SMALL_TEXT_OUTLINE_SCALE

        if info_settings.show_length or info_settings.show_file_size:
            parts = []
            if info_settings.show_length:
                duration_text = _format_duration(video.duration_sec)
                if duration_text:
                    parts.append(duration_text)
            if info_settings.show_file_size:
                size_text = _format_file_size(video.path)
                if size_text:
                    parts.append(size_text)
            info_label = OutlinedLabel(" \u2022 ".join(parts) if parts else " ")
            info_label.setStyleSheet("font-size: 10px;")
            info_label.setAlignment(Qt.AlignCenter)
            # Uses the SAME card_text_outline_width as the title now --
            # the old outline_width=0 (fill-only) fallback here was
            # working around a real limitation in the OLD single-pass
            # stroke+fill technique (the stroke ate inward into the
            # fill from both sides, swallowing it entirely at small
            # font sizes). OutlinedLabel now draws the outline and fill
            # as two SEPARATE passes -- fill always renders the
            # complete, untouched glyph shape on top, so it never
            # disappears regardless of outline width; verified directly
            # even at the full default width (3.0) on 10px text, the
            # fill stays clearly present. Scaled DOWN from that full
            # width now (see SMALL_TEXT_OUTLINE_SCALE above), not
            # dropped back to 0 -- this text still gets a real, just
            # proportionally thinner, outline.
            info_label.set_colors(
                self._appearance.card_text_color, self._appearance.card_text_outline_color,
                outline_width=small_text_outline_width,
            )
            info_layout.addWidget(info_label)

        if info_settings.show_creation_date:
            date_text = _format_date(video.created_at) or " "
            date_label = OutlinedLabel(date_text)
            date_label.setStyleSheet("font-size: 10px;")
            date_label.setAlignment(Qt.AlignCenter)
            date_label.set_colors(
                self._appearance.card_text_color, self._appearance.card_text_outline_color,
                outline_width=small_text_outline_width,
            )
            info_layout.addWidget(date_label)

        # Filters section: "below"-location icons, then tag-name text --
        # both come after the title (and after the optional length/
        # size/date lines above), replacing where tag-name text used to
        # sit right under the title before length/size/date existed.
        if show_filters:
            if display_settings.show_filter_icons and display_settings.filter_icon_location == "below":
                info_layout.addWidget(self._build_icon_row(matching, vertical=False))

            if display_settings.show_filter_names:
                tag_label = OutlinedLabel(", ".join(video.tags) if video.tags else " ")
                tag_label.setWordWrap(False)  # same single-line-always reasoning as the title
                tag_label.setStyleSheet("font-size: 10px;")
                tag_label.setAlignment(Qt.AlignCenter)
                tag_label.set_colors(
                    self._appearance.card_text_color, self._appearance.card_text_outline_color,
                    outline_width=small_text_outline_width,
                )
                info_layout.addWidget(tag_label)

        # Action-buttons row: Edit/Copy/Filters/Delete as their own
        # clickable buttons on the card itself, below Filters -- an
        # alternative to reaching them via right-click, on by default.
        # Acts on THIS card's video only (not the current multi-
        # selection), unlike the right-click menu's bulk actions --
        # these are per-card buttons, not a selection-wide action.
        if info_settings.show_action_buttons:
            info_layout.addWidget(self._build_action_buttons_row())

        outer_layout.addWidget(self.info_box)

        # Without this, extra vertical space the grid gives this card
        # (e.g. because another card in the same row is taller, due to
        # having tags and this one not) gets distributed by the layout
        # instead of landing predictably at the bottom -- which is what
        # made the title look like it sat "a percent of the way down"
        # rather than snug under the thumbnail. Pinning the stretch to
        # the bottom keeps thumbnail/title/tags packed together
        # regardless of how tall the card ends up being.
        outer_layout.addStretch(1)

        self.set_font_scale(font_scale)  # sets the title's actual (elided) text too

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _title_text(self, video: "library.Video") -> str:
        return f"{FAVORITE_STAR} {video.title}" if video.favorite else video.title

    def _build_icon_row(self, matching: list[tuple[str, str]], vertical: bool) -> QWidget:
        available = THUMB_SIZE.height() if vertical else THUMB_SIZE.width()
        icon_size = _icon_size_for_count(len(matching), available, self._appearance.filter_icon_size)
        # Reserved space is the BASE setting value, not the count-adjusted
        # icon_size above -- using icon_size here would make the row
        # itself shorter/narrower on a card with many tags (since more
        # tags -> smaller icons -> if reserved space shrunk to match,
        # the row would too), reintroducing exactly the kind of
        # per-video size variance normalizing this was supposed to fix.
        # icon_size still controls how big the icons actually render
        # WITHIN this constant reserved space.
        reserved = self._appearance.filter_icon_size

        container = QWidget()
        row_layout = QVBoxLayout(container) if vertical else QHBoxLayout(container)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(ICON_SPACING)
        # Stretches on both ends center the icons within the row/column
        # rather than left/top-aligning them.
        row_layout.addStretch(1)
        for tag_name, path in matching:
            # Scaled DOWN to a STABLE reference size FIRST, then
            # outlined -- not the count-adjusted, per-video icon_size,
            # and not the icon's native resolution either.
            #
            # Native resolution was the ORIGINAL bug here: a
            # user-provided icon file can be arbitrarily large (a
            # 512x512 PNG isn't unusual), and outlining at that
            # resolution before scaling down shrinks a 2px outline
            # proportionally along with everything else -- e.g. roughly
            # 0.2px on a 512px source scaled down to a 54px icon,
            # imperceptible. Reported directly as "practically
            # invisible".
            #
            # But using icon_size directly for the CACHE KEY (an
            # earlier version of this fix did) turned out to be a
            # SEPARATE, worse bug: icon_size is count-adjusted per
            # video (_icon_size_for_count shrinks it as a video's own
            # matching-tag count grows), so two videos with different
            # tag counts get DIFFERENT icon_size values for the exact
            # same underlying icon file -- meaning the cache almost
            # never actually hit across different cards, and the
            # expensive per-pixel outline computation (see
            # silhouette_outline_pixmap's own docstring) re-ran for
            # nearly every card in the library instead of once per
            # unique icon. Directly responsible for the app going from
            # opening instantly to taking 15-30 seconds. Outlining at
            # the STABLE `reserved` size instead (the base setting,
            # identical for every card regardless of that card's own
            # tag count) means the cache key is finally stable across
            # cards too -- `_FilterIconLabel`'s own subsequent `.scaled()`
            # call handles any further per-video downscaling from there,
            # which is cheap (a single native scale, not a per-pixel
            # Python loop) regardless of how many times it happens.
            icon_pixmap = QPixmap(path).scaled(
                QSize(reserved, reserved), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            if self._appearance.filter_outline_enabled:
                outline_hex = self._tag_outline_colors.get(tag_name, self._appearance.card_text_outline_color)
                icon_pixmap = silhouette_outline_pixmap_cached(
                    f"{path}@{reserved}", icon_pixmap, QColor(outline_hex), width=3
                )
            icon_label = _FilterIconLabel(tag_name, icon_pixmap, icon_size, container)
            icon_label.left_clicked.connect(self.filter_left_clicked.emit)
            icon_label.right_clicked.connect(self.filter_right_clicked.emit)
            row_layout.addWidget(icon_label)
        row_layout.addStretch(1)
        if vertical:
            container.setFixedWidth(reserved)
        else:
            container.setFixedHeight(reserved)
        return container

    def set_font_scale(self, factor: float) -> None:
        """Live-updatable independent of set_highlight_enabled -- called
        by the Library's window-size-based scaling (see
        LibraryPage.apply_scale) without needing to rebuild the card."""
        self._current_font_scale = factor
        font = self.title_label.font()
        target_pt = self._base_title_pt * factor
        max_width = THUMB_SIZE.width() - 8  # small margin, matches layout's own content margins

        # Reserve the title row's height at the TARGET (un-shrunk) size
        # BEFORE any Resize-Text-to-Fit shrinking below -- see this same
        # note in __init__ for why decoupling the two matters (a card
        # whose specific title needed shrinking would otherwise end up
        # with a shorter row than one that didn't).
        target_font_metrics = self.title_label.font()
        target_font_metrics.setPointSizeF(target_pt)
        self.title_label.setFixedHeight(QFontMetrics(target_font_metrics).height())

        if self._appearance.resize_text_to_fit:
            # Shrink (never grow past target_pt) until the title's
            # single-line width fits the card -- word-wrap has been
            # permanently off since the card-size-normalization change
            # (see __init__), so an overlong title needs to shrink
            # instead of wrapping to a second line.
            from PySide6.QtGui import QFontMetricsF
            pt = target_pt
            while pt > 6.0:
                font.setPointSizeF(pt)
                if QFontMetricsF(font).horizontalAdvance(self._full_title_text) <= max_width:
                    break
                pt -= 0.5
            font.setPointSizeF(pt)
        else:
            font.setPointSizeF(target_pt)
        self.title_label.setFont(font)
        # Elide as a hard backstop regardless of Resize Text to Fit --
        # guarantees a constant single-line title height on every card
        # no matter how long any given video's title is, or how far
        # shrinking above got before giving up at the 6pt floor. This
        # (plus _build_icon_row's fixed reservation) is what actually
        # makes "all clips the same size" true, rather than just
        # "usually similar."
        elided = QFontMetrics(font).elidedText(self._full_title_text, Qt.ElideRight, max_width)
        self.title_label.setText(elided)

    def set_highlight_enabled(self, enabled: bool) -> None:
        """Called live by the Library's "Highlight Unedited" toggle --
        no need to rebuild/recreate cards, just repaint them."""
        self._highlight_enabled = enabled
        self.update()

    def set_selected(self, selected: bool) -> None:
        """Called by the grid's selection handling (_VideoGridTab) --
        a selected card's outer ring is layered on top of whatever the
        thumbnail's own border already shows (see _render_background),
        not a replacement for it. Fades the outer-ring change in over
        the previous render rather than snapping straight to it --
        reported directly as looking better ("fade... instead of
        snapping"). Captures whatever's CURRENTLY cached as the fade's
        starting frame before flipping state, so this works the same
        whether toggling into OR out of selection."""
        if selected != self._selected:
            self._fade_from = self._bg_cache
            self._selected = selected
            self._bg_cache = None  # forces a fresh render at the new state on next paint
            # Set synchronously, not left to the animation's own first
            # tick -- QVariantAnimation doesn't guarantee delivering
            # valueChanged(0.0) synchronously within start() itself (it
            # can defer to the next timer tick), so without this, a
            # paintEvent that happens to run before that first tick
            # would still see the OLD progress value (1.0, fully
            # settled) left over from whatever the last completed fade
            # was, and skip the blend entirely for that one frame.
            self._selection_fade_progress = 0.0
            self._start_selection_fade()
            self.update()

    def _start_selection_fade(self) -> None:
        if self._selection_fade_anim is not None:
            self._selection_fade_anim.stop()
        anim = QVariantAnimation(self)
        anim.setDuration(200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.valueChanged.connect(self._on_selection_fade_value)
        anim.finished.connect(self._on_selection_fade_finished)
        self._selection_fade_anim = anim
        anim.start()

    def _on_selection_fade_value(self, value) -> None:
        self._selection_fade_progress = float(value)
        self.update()

    def _on_selection_fade_finished(self) -> None:
        self._fade_from = None
        self._selection_fade_progress = 1.0
        self.update()

    def _should_show_highlight(self) -> bool:
        # video.has_edit already IS a per-video "has this been trimmed
        # yet" flag, tracked in the DB and kept correct automatically by
        # the existing edit/undo/prune/rescan logic -- a separate
        # tracked list of "unedited" clip paths would just be a second,
        # independently-maintainable copy of the exact same fact, with
        # its own chance to drift out of sync. Using the field that
        # already exists gets identical visible behavior for free.
        return self._highlight_enabled and not self._video.has_edit

    def paintEvent(self, event) -> None:
        # Cached rather than rebuilt (several rounded-rect paths, a
        # gradient-pixmap draw, a multiply-blend composite) on every
        # repaint -- reported directly as low frame rate while
        # scrolling despite the scroll MOVEMENT itself being smooth,
        # which points at per-frame repaint cost rather than the scroll
        # animation. None of this actually changes except on a real
        # resize, a selection change, or a highlight-enabled toggle --
        # all three are covered by cache_key below, so scrolling itself
        # (which changes only the widget's POSITION, not its size or
        # state) now just blits one already-rendered pixmap instead of
        # redoing this whole paintEvent's work every frame.
        cache_key = (self.size().width(), self.size().height(), self._selected, self._should_show_highlight())
        if self._bg_cache is None or self._bg_cache_key != cache_key:
            self._bg_cache = self._render_background(cache_key)
            self._bg_cache_key = cache_key
        painter = QPainter(self)
        if self._fade_from is not None and self._selection_fade_progress < 1.0:
            # Cross-fades the OLD (pre-selection-change) rendering into
            # the new one over set_selected()'s own animation, rather
            # than snapping straight to the new state -- both are
            # already-cached pixmaps, so this is just two cheap blits
            # per frame (one at partial opacity), not a re-render.
            painter.drawPixmap(0, 0, self._fade_from)
            painter.setOpacity(self._selection_fade_progress)
            painter.drawPixmap(0, 0, self._bg_cache)
            painter.setOpacity(1.0)
        else:
            painter.drawPixmap(0, 0, self._bg_cache)
        painter.end()
        super().paintEvent(event)

    def _render_background(self, cache_key) -> QPixmap:
        _width, _height, selected, show_highlight = cache_key
        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        outer_rect = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 0
        border_width = self._appearance.unedited_selected_border_width

        if selected:
            # Selection stays a ring around the WHOLE outer card (unlike
            # the unedited highlight below, this one was NOT redefined
            # to also become a background wash behind everything --
            # only the unedited highlight was, per Max's own
            # clarification). Same stretch-then-inset technique as
            # before, now respecting the outer box's rounded shape.
            if radius:
                painter.setClipPath(rounded_rect_path(outer_rect, radius))
            painter.drawPixmap(self.rect(), self._selected_border_pixmap)
            painter.setClipping(False)
            inner_rect = outer_rect.adjusted(border_width, border_width, -border_width, -border_width)
            if radius:
                # A plain fillRect(inner_rect, ...) would leave the
                # inset area's own corners sharp even though the outer
                # ring is rounded -- clip to a (correspondingly smaller)
                # rounded path instead of just filling the rect outright.
                painter.setClipPath(rounded_rect_path(inner_rect, max(0.0, radius - border_width)))
                painter.fillRect(self.rect(), self._theme.card_background())
                painter.setClipping(False)
            else:
                painter.fillRect(inner_rect, self._theme.card_background())
        else:
            # Plain background portrusion -- rounded, theme-colored.
            # This is what's visible in the padding gaps around the
            # video box and info box (see their own comments) -- always
            # at least a sliver of it showing, everywhere on the card,
            # regardless of content. NO highlight wash here anymore --
            # per Max, after actually seeing it rendered: the unedited
            # highlight should be JUST a border around the video
            # thumbnail, not something that takes over the whole card's
            # own background color (this used to also do the latter).
            if radius:
                painter.setClipPath(rounded_rect_path(outer_rect, radius))
            painter.fillRect(self.rect(), self._theme.card_background())
            painter.setClipping(False)

        # Video-thumbnail border: the unedited-highlight gradient for an
        # unedited video (with highlighting enabled and the card not
        # selected), otherwise a plain accent()-colored fill (matches
        # the info box's own color) -- mutually exclusive, so every card gets exactly one border
        # treatment around its thumbnail, never both and never neither.
        # Both branches fill the SAME video_box margin region (radius,
        # position, and thickness all identical -- the only difference
        # is a stretched gradient image vs. a flat color), which is
        # what actually guarantees the two look consistent rather than
        # needing their geometry kept in sync by hand.
        video_rect = QRectF(self.video_box.geometry())
        if video_rect.width() > 0 and video_rect.height() > 0:
            # NOT capped to border_width -- an earlier version of this
            # line did `min(radius, border_width)`, which with the
            # default 24px corner radius vs. a 9px border width capped
            # the thumbnail's own rounding down to just 9px, visibly
            # less rounded than the rest of the card (outer box, info
            # box) and easily read as "not rounded at all" at normal
            # viewing size -- reported directly from a screenshot.
            # rounded_rect_path() already clamps to half of whichever
            # of the rect's own width/height is smaller, which is the
            # only clamp actually needed to keep the shape valid.
            video_radius = radius
            if show_highlight:
                # Was `if not selected and show_highlight:` -- selection
                # used to unconditionally override the highlight border
                # with the plain accent() fill, even for an UNEDITED
                # video, which is exactly the reported bug ("selecting
                # a video replaces the thumbnail outline with the
                # default one, even if it's unedited"). Selection is
                # meant to be a separate, ADDITIONAL indicator (the
                # outer ring, built above -- unchanged) layered on TOP
                # of whatever the thumbnail's own border already is,
                # not something that overrides what that border means.
                # Whether THIS specific border shows the highlight
                # gradient now depends purely on the video's own edited
                # state, exactly like it does when nothing is selected.
                if video_radius:
                    painter.setClipPath(rounded_rect_path(video_rect, video_radius))
                painter.drawPixmap(video_rect, self._highlight_pixmap, QRectF(self._highlight_pixmap.rect()))
                darken_factor = 1 - (self._appearance.unedited_highlight_brightness / 100.0)
                if darken_factor > 0:
                    gray = round(255 * (1 - darken_factor))
                    painter.setCompositionMode(QPainter.CompositionMode_Multiply)
                    painter.fillRect(video_rect, QColor(gray, gray, gray))
                    painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
                painter.setClipping(False)
            else:
                # FILLS the same video_box margin region the gradient
                # branch above fills, with the info-box color
                # (Theme.accent()) as a flat color instead of a
                # stretched gradient image -- was app_background()
                # until Max asked directly for this to match the info
                # box's own color instead. NOT a thin stroke drawn at
                # video_box's own OUTER edge (an earlier version of
                # this did that, and it was wrong: video_box's outer
                # edge sits border_width pixels away from the
                # thumbnail's actual edge, so a stroke drawn there left
                # a visible gap of plain card_background() color
                # showing through in between. Reported directly as "a
                # few pixels off, showing the video card color in
                # between". Using the exact same fill-then-let-thumb_
                # label-cover-the-center technique as the gradient
                # branch guarantees this one matches it in BOTH
                # position and width automatically, rather than needing
                # to keep two separate geometries in sync by hand.
                if video_radius:
                    painter.setClipPath(rounded_rect_path(video_rect, video_radius))
                    painter.fillRect(video_rect, self._theme.accent())
                    painter.setClipping(False)
                else:
                    painter.fillRect(video_rect, self._theme.accent())

        painter.end()
        return pixmap

    def _load_pixmap(self, video: "library.Video") -> QPixmap:
        thumb_path = thumbnails.get_thumbnail(video.id, Path(video.path))
        if thumb_path is None:
            pixmap = _placeholder_pixmap()
        else:
            pixmap = QPixmap(str(thumb_path))
            if pixmap.isNull():
                pixmap = _placeholder_pixmap()
            else:
                pixmap = pixmap.scaled(THUMB_SIZE, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 0
        return round_pixmap_corners(pixmap, radius)

    def mousePressEvent(self, event) -> None:
        # If a title edit is in progress AND this click isn't on the
        # edit box itself, commit it explicitly before anything else --
        # a click elsewhere ON THIS SAME CARD (the thumbnail, an action
        # button) is handled entirely by this card's own click logic
        # below/elsewhere, which doesn't naturally shift Qt's own
        # focus away from the still-focused title edit the way clicking
        # some COMPLETELY unrelated widget would. Without this, "click
        # off to save" only worked for clicks that happened to land on
        # something that takes real Qt focus -- reported directly as
        # not working reliably. Mapped via GLOBAL coordinates (not a
        # direct geometry comparison) since self._title_edit's parent
        # isn't necessarily `self` itself -- there can be intermediate
        # layout container widgets -- so its geometry() alone isn't in
        # the same coordinate space as this event's own pos().
        if self._title_edit is not None:
            global_pos = self.mapToGlobal(event.pos())
            local_to_edit = self._title_edit.mapFromGlobal(global_pos)
            if not self._title_edit.rect().contains(local_to_edit):
                self._commit_title_edit()

        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.video_id, event.modifiers())
            # A plain (unmodified) left-click directly on the thumbnail
            # opens the preview player, "like Medal" -- but Ctrl/Shift
            # clicks (multi-select) never do, and this is delayed
            # rather than immediate so a DOUBLE-click (which still
            # opens the Editor, unchanged -- see mouseDoubleClickEvent)
            # doesn't ALSO flash the preview open first. Qt has no
            # single "click vs double-click" event of its own; this
            # delay-then-cancel-if-a-second-click-arrives approach is
            # the standard way to disambiguate the two.
            no_modifiers = event.modifiers() == Qt.NoModifier
            on_thumbnail = self.thumb_label.geometry().contains(event.pos())
            if no_modifiers and on_thumbnail:
                self._preview_pending = True
                QTimer.singleShot(250, self._open_preview_if_still_pending)
            event.accept()
            return
        super().mousePressEvent(event)

    def _open_preview_if_still_pending(self) -> None:
        if self._preview_pending:
            self._preview_pending = False
            self._open_preview_dialog(self._video)

    def _open_preview_dialog(self, video: "library.Video") -> None:
        # Bubbles up rather than constructing anything here directly --
        # the actual overlay needs to be a child of MainWindow's central
        # widget (see video_preview_dialog.py's own module docstring for
        # why a separate top-level window was the wrong approach), which
        # this card has no direct reference to.
        self.preview_requested.emit(video, self._neighbor_provider)

    def mouseDoubleClickEvent(self, event) -> None:
        self._preview_pending = False  # cancel the pending single-click preview -- see mousePressEvent
        self.edit_requested.emit(self.video_id)

    def _show_context_menu(self, pos) -> None:
        if self._ensure_selected:
            self._ensure_selected(self.video_id)
        target_ids = set(self._get_selected_ids()) if self._get_selected_ids else set()
        if self.video_id not in target_ids:
            # No selection wiring, or the current selection somehow
            # doesn't include the card that was actually right-clicked
            # (shouldn't happen once _ensure_selected has run, but don't
            # silently act on the wrong videos if it does) -- fall back
            # to acting on just this one card.
            target_ids = {self.video_id}
        multi = len(target_ids) > 1
        count_suffix = f" ({len(target_ids)})" if multi else ""

        menu = QMenu(self)
        menu.setStyleSheet(_menu_stylesheet(self._appearance))
        # Edit/Rename only make sense for exactly one video at a time --
        # hidden rather than shown-but-disabled for a multi-selection.
        edit_action = None
        rename_action = None
        if not multi:
            edit_action = menu.addAction("Edit")
            rename_action = menu.addAction("Rename")

        target_videos = [library.get_video(vid) for vid in target_ids]
        all_favorited = all(v.favorite for v in target_videos)
        favorite_action = menu.addAction("Unfavorite" if all_favorited else "Favorite")
        upload_action = menu.addAction(f"Upload{count_suffix}")

        # A plain QAction now, not a QMenu submenu -- see filters_popup.py's
        # own module docstring for why: four separate QMenu-level fixes for
        # "stays open while toggling several checkboxes" didn't hold up, so
        # this now opens a genuine Qt.Popup widget (FiltersPopup) instead,
        # positioned where the submenu used to appear, right after this
        # main menu closes normally (which is fine -- the interactive part
        # that needed to stay open moves to that separate popup, not this
        # one-shot "open Filters" click).
        filters_action = menu.addAction("Filters...")

        # A plain checkable action, not a custom checkbox -- unlike
        # Filters (where staying open to toggle several tags in one visit
        # genuinely matters), "Edited" is a single one-shot toggle, and
        # the menu closing right after it -- completely normal QAction
        # behavior -- is exactly as expected, the same as Favorite just
        # above.
        all_edited = all(v.has_edit for v in target_videos)
        edited_action = menu.addAction(f"Edited{count_suffix}")
        edited_action.setCheckable(True)
        edited_action.setChecked(all_edited)

        copy_action = menu.addAction(f"Copy{count_suffix}")
        delete_action = menu.addAction(f"Delete{count_suffix}")

        self.context_menu_opened.emit()
        chosen = menu.exec(self.mapToGlobal(pos))
        self.context_menu_closed.emit()
        if edit_action is not None and chosen == edit_action:
            self.edit_requested.emit(self.video_id)
        elif rename_action is not None and chosen == rename_action:
            self._rename()
        elif chosen == favorite_action:
            self._bulk_set_favorite(target_ids, not all_favorited)
        elif chosen == upload_action:
            for vid in target_ids:
                self.upload_requested.emit(vid)
        elif chosen == filters_action:
            self._open_filters_popup(target_ids, target_videos, self.mapToGlobal(pos))
        elif chosen == edited_action:
            for vid in target_ids:
                if edited_action.isChecked():
                    library.mark_as_edited(vid)
                else:
                    library.mark_as_unedited(vid)
            self.tags_changed.emit()  # reuses this signal purely to trigger a refresh -- nothing tag-related actually changed
        elif chosen == copy_action:
            self._bulk_copy_to_clipboard(target_ids)
        elif chosen == delete_action:
            self._bulk_delete(target_ids)

    def _build_action_buttons_row(self) -> QWidget:
        """Edit/Copy/Filters/Delete, in that order, as real buttons on
        the card -- icons Max provided (pencil/copy/funnel/trash),
        replacing the old text labels. Each acts on just THIS card's
        video, reusing the exact same handler methods the right-click
        menu's single-video actions use, so there's one source of
        truth for what each action actually does.

        Styled distinctly from every other CustomButton in the app --
        filled with the card TEXT color (not the usual accent), with
        its own stroked outline using the card text's own outline
        color, "similar to the text" -- and roughly twice the size of
        a default CustomButton (48px tall, vs. the 24px used
        elsewhere). Icons are shown at their own original colors (not
        retinted to match text, unlike the Search/Refresh/Sort toolbar
        icons) -- these are full-color, individually-branded action
        icons, not monochrome ones meant to blend into the text
        system."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)
        actions = [
            ("edit_icon.png", "Edit", lambda: self.edit_requested.emit(self.video_id)),
            ("copy_icon.png", "Copy", lambda: self._bulk_copy_to_clipboard({self.video_id})),
            ("filters_icon.png", "Filters", self._open_filters_menu_for_self),
            ("delete_icon.png", "Delete", lambda: self._bulk_delete({self.video_id})),
        ]
        for icon_name, tooltip, handler in actions:
            btn = CustomButton()
            btn.setToolTip(tooltip)
            btn.set_icon_pixmap(resource_qpixmap(icon_name))
            # Circular (not just tall-and-wide, per the report: they
            # were rendering as tall rectangles that didn't fit their
            # own icons, since a QHBoxLayout stretches each button to
            # fill the row's own width rather than keeping them square)
            # -- same set_circular() the Search/Refresh/Sort header
            # buttons already use. Slightly larger than the old 48px
            # minimum height (56px), per Max's own "slightly increase
            # the size" -- CustomButton.paintEvent already scales the
            # icon to fill most of whatever shape it's drawing (a true
            # circle now, instead of a mostly-empty rectangle), so
            # there was nothing separate to fix for "barely visible"
            # once the shape itself was corrected.
            btn.set_circular(56)
            btn.set_fill_color(self._appearance.card_text_color)
            btn.set_outline(self._appearance.card_text_outline_color, self._appearance.card_text_outline_width)
            btn.clicked.connect(handler)
            layout.addWidget(btn)
        return row

    def _open_filters_menu_for_self(self) -> None:
        """The "Filters" action button opens the SAME FiltersPopup the
        right-click menu's Filters entry does, scoped to this one
        video only."""
        self._open_filters_popup({self.video_id}, [self._video], self.mapToGlobal(self.rect().center()))

    def _open_filters_popup(self, target_ids: set[int], target_videos: list["library.Video"],
                             global_pos) -> None:
        """A genuine Qt.Popup widget (see filters_popup.py's own module
        docstring for the full story of why this replaced a QMenu
        submenu) listing every known tag, grouped into the same
        categories, each as a checkbox: checked when EVERY video in
        target_ids already has that tag, toggling adds/removes it
        across all of them at once."""
        from .filters_popup import FiltersPopup
        popup = FiltersPopup(
            target_ids, target_videos,
            on_tags_changed=self.tags_changed.emit,
            on_create_new_filter=self._create_new_filter,
            parent=self,
        )
        self.context_menu_opened.emit()
        popup.closed.connect(self.context_menu_closed.emit)
        popup.show_near(global_pos)

    def _create_new_filter(self) -> None:
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
                # Doesn't apply it to anything or update the currently-open
                # submenu in place (matches the Library Filters dropdown's
                # own "+ Add Filter", which is the same one-shot behavior) --
                # it'll show up next time a filters menu is opened, once
                # tags_changed has propagated through to a refresh().
                self.tags_changed.emit()

    def _bulk_set_favorite(self, target_ids: set[int], favorite: bool) -> None:
        for vid in target_ids:
            library.set_favorite(vid, favorite)
        self.tags_changed.emit()  # parent refresh -- title/star + filters-menu-relevant either way

    def _bulk_copy_to_clipboard(self, target_ids: set[int]) -> None:
        """Same file-clipboard mechanism as the single-card Copy (see
        its docstring) but with one URL per selected video, so a paste
        into a file manager or chat app attaches/drops all of them at
        once."""
        from PySide6.QtCore import QUrl, QMimeData
        from PySide6.QtWidgets import QApplication

        auto_mp4 = config_module.load().auto_copy_as_mp4
        urls = []
        missing = []
        for vid in target_ids:
            video = library.get_video(vid)
            path = Path(video.path)
            if not path.exists():
                missing.append(str(path))
                continue
            urls.append(QUrl.fromLocalFile(str(self._resolve_copy_path(path, auto_mp4))))
        if missing:
            QMessageBox.warning(
                self, "Copy Failed",
                "File(s) not found:\n" + "\n".join(missing),
            )
        if urls:
            mime = QMimeData()
            mime.setUrls(urls)
            QApplication.clipboard().setMimeData(mime)

    @staticmethod
    def _resolve_copy_path(path: Path, auto_mp4: bool) -> Path:
        """Returns the path that should actually go on the clipboard
        for Copy -- the original, unless "Auto Copy as MP4" is on AND
        the file isn't already .mp4, in which case a fresh copy of the
        exact same bytes is made in the system temp directory under a
        .mp4 name instead, and THAT path is returned. A pure rename
        via a copy, not a remux/re-encode of any kind -- the library's
        own tracked file at `path` is never touched, since repointing
        the clipboard at a renamed version of it directly would mean
        either mutating the library's own path bookkeeping or lying
        about what file is actually still there. Deliberately not
        guaranteed to produce genuinely valid, standards-conformant MP4
        if the underlying container/codec really isn't MP4-compatible
        -- it's exactly what "no remuxing or encoding, just a rename"
        asked for, accepting that tradeoff on purpose. Repeated copies
        of the same video overwrite the same temp path rather than
        accumulating a new file every time."""
        if not auto_mp4 or path.suffix.lower() == ".mp4":
            return path
        temp_path = Path(tempfile.gettempdir()) / (path.stem + ".mp4")
        shutil.copyfile(path, temp_path)
        return temp_path

    def _bulk_delete(self, target_ids: set[int]) -> None:
        if len(target_ids) == 1:
            video = library.get_video(next(iter(target_ids)))
            message = f"Delete '{video.title}'? This removes the file from disk and can't be undone."
        else:
            message = (
                f"Delete {len(target_ids)} videos? This removes the files from disk "
                "and can't be undone."
            )
        reply = QMessageBox.question(
            self, "Delete Video" if len(target_ids) == 1 else "Delete Videos",
            message, QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        for vid in target_ids:
            library.delete_video(vid)
        # One emit regardless of how many were deleted -- the connected
        # slot (_VideoGridTab.refresh, via a lambda that ignores its
        # argument) does a single full refresh either way.
        self.deleted.emit(self.video_id)

    def _copy_to_clipboard(self) -> None:
        """Copies the clip FILE to the system clipboard -- same as
        Ctrl+C on a file in a file manager -- rather than copying any
        text, so pasting into a chat app (Discord, etc.) attaches the
        actual clip. There's no in-app paste target; this is purely so
        the file ends up wherever the system clipboard's normal
        paste-a-file behavior takes it."""
        from PySide6.QtCore import QUrl, QMimeData
        from PySide6.QtWidgets import QApplication

        path = Path(self._video.path)
        if not path.exists():
            QMessageBox.warning(self, "Copy Failed", f"File not found: {path}")
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        QApplication.clipboard().setMimeData(mime)

    def _rename(self) -> None:
        """Triggered by the context menu's Rename action -- edits the
        title INLINE on the card itself instead of opening a separate
        dialog window, per Max's direct request (same technique as the
        video previewer's own click-to-edit title). Autosaves the
        current text once a second while editing, in addition to
        committing on Enter or clicking away (both go through
        CustomLineEdit's editingFinished)."""
        if self._title_edit is not None:
            return  # already editing
        info_layout = self.title_label.parentWidget().layout()
        index = info_layout.indexOf(self.title_label)
        self.title_label.hide()

        self._title_edit = CustomLineEdit(self._video.title)
        self._title_edit.setFixedWidth(self.title_label.width())
        self._title_edit.editingFinished.connect(self._commit_title_edit)
        info_layout.insertWidget(index, self._title_edit)
        self._title_edit.setFocus()
        self._title_edit.selectAll()

        self._last_saved_title = self._video.title
        self._title_autosave_timer = QTimer(self)
        self._title_autosave_timer.setInterval(1000)
        self._title_autosave_timer.timeout.connect(self._autosave_title)
        self._title_autosave_timer.start()

        # App-wide, not just this card's own mousePressEvent -- reported
        # directly that "click off to save" only worked for a click on
        # THIS SAME card, not a different card or empty background.
        # Neither of those naturally routes through this card's own
        # click handling at all (Qt delivers a mouse press to whichever
        # widget the cursor is actually over, not to every OTHER widget
        # in the app), so a per-card check could never catch them --
        # only a genuinely app-wide filter sees every click everywhere.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.MouseButtonPress and self._title_edit is not None:
            global_pos = event.globalPosition().toPoint() if hasattr(event, "globalPosition") else event.globalPos()
            local_to_edit = self._title_edit.mapFromGlobal(global_pos)
            if not self._title_edit.rect().contains(local_to_edit):
                self._commit_title_edit()
        return super().eventFilter(obj, event)

    def _autosave_title(self) -> None:
        """Runs once a second while editing -- persists the CURRENT
        text to the DB without emitting `renamed` (which would trigger
        a full grid refresh via _VideoGridTab.refresh() and destroy
        this very edit widget mid-keystroke). Only the final commit
        (_commit_title_edit, below) emits that, once editing is
        actually done."""
        if self._title_edit is None:
            return
        current = self._title_edit.text().strip()
        if current and current != self._last_saved_title:
            self._video = library.rename_video(self.video_id, title=current)
            self._last_saved_title = current

    def _commit_title_edit(self) -> None:
        if self._title_edit is None:
            return
        QApplication.instance().removeEventFilter(self)
        self._title_autosave_timer.stop()
        new_title = self._title_edit.text().strip()
        if new_title and new_title != self._video.title:
            self._video = library.rename_video(self.video_id, title=new_title)

        info_layout = self._title_edit.parentWidget().layout()
        index = info_layout.indexOf(self._title_edit)
        info_layout.removeWidget(self._title_edit)
        self._title_edit.deleteLater()
        self._title_edit = None

        self._full_title_text = self._title_text(self._video)
        self.set_font_scale(self._current_font_scale)  # re-fit/re-elide with the (possibly new) text
        info_layout.insertWidget(index, self.title_label)
        self.title_label.show()
        self.renamed.emit()
