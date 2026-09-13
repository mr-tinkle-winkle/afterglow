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

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QMenu, QMessageBox, QLineEdit,
    QPushButton, QHBoxLayout, QInputDialog, QCheckBox, QWidgetAction,
)

from .. import library, thumbnails, config as config_module
from .resources import resource_qpixmap
from .pixmap_effects import resolve_border_pixmap, hue_shift_pixmap_cached

THUMB_SIZE = QSize(400, 224)  # 16:9, doubled from the original 200x112
FAVORITE_STAR = "\u2605"  # "★"

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


class VideoCard(QWidget):
    edit_requested = Signal(int)      # video_id
    deleted = Signal(int)             # video_id
    upload_requested = Signal(int)    # video_id
    tags_changed = Signal()           # tag added/removed -- parent should refresh filter list
    renamed = Signal()                # title changed -- parent should refresh (search may no longer match)
    filter_left_clicked = Signal(str)   # tag_name, from clicking an icon on the card itself
    filter_right_clicked = Signal(str)  # tag_name, ditto (block)
    clicked = Signal(int, object)       # video_id, Qt.KeyboardModifiers -- parent handles selection

    def __init__(self, video: "library.Video", parent=None, highlight_enabled: bool = True,
                 font_scale: float = 1.0, get_selected_ids=None, ensure_selected=None):
        super().__init__(parent)
        self.video_id = video.id
        self._video = video
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        display_settings = settings.filter_display
        info_settings = settings.card_info
        icons = library.tag_icons()
        show_filters = info_settings.show_filters
        matching = [(t, icons[t]) for t in video.tags if t in icons]

        if show_filters and display_settings.show_filter_icons and matching and \
                display_settings.filter_icon_location == "above":
            layout.addWidget(self._build_icon_row(matching, vertical=False))

        thumb_row = QHBoxLayout()
        if show_filters and display_settings.show_filter_icons and matching and \
                display_settings.filter_icon_location == "vtile_left":
            thumb_row.addWidget(self._build_icon_row(matching, vertical=True))

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(THUMB_SIZE)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setPixmap(self._load_pixmap(video))
        thumb_row.addWidget(self.thumb_label)

        if show_filters and display_settings.show_filter_icons and matching and \
                display_settings.filter_icon_location == "vtile_right":
            thumb_row.addWidget(self._build_icon_row(matching, vertical=True))
        layout.addLayout(thumb_row)

        self.title_label = QLabel(self._title_text(video))
        self.title_label.setWordWrap(not self._appearance.resize_text_to_fit)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setFixedWidth(THUMB_SIZE.width())
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
        self.set_font_scale(font_scale)
        layout.addWidget(self.title_label)

        # Info line(s), per the Library's "Info" dropdown: length + file
        # size on one line (size after length), creation date on its
        # own line under that -- both above the filters section, in
        # that fixed order, each independently toggleable.
        info_parts = []
        if info_settings.show_length:
            duration_text = _format_duration(video.duration_sec)
            if duration_text:
                info_parts.append(duration_text)
        if info_settings.show_file_size:
            size_text = _format_file_size(video.path)
            if size_text:
                info_parts.append(size_text)
        if info_parts:
            info_label = QLabel(" \u2022 ".join(info_parts))
            info_label.setStyleSheet("color: gray; font-size: 10px;")
            info_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(info_label)

        if info_settings.show_creation_date:
            date_text = _format_date(video.created_at)
            if date_text:
                date_label = QLabel(date_text)
                date_label.setStyleSheet("color: gray; font-size: 10px;")
                date_label.setAlignment(Qt.AlignCenter)
                layout.addWidget(date_label)

        # Filters section: "below"-location icons, then tag-name text --
        # both come after the title (and after the optional length/
        # size/date lines above), replacing where tag-name text used to
        # sit right under the title before length/size/date existed.
        if show_filters:
            if display_settings.show_filter_icons and matching and \
                    display_settings.filter_icon_location == "below":
                layout.addWidget(self._build_icon_row(matching, vertical=False))

            if video.tags and display_settings.show_filter_names:
                tag_label = QLabel(", ".join(video.tags))
                tag_label.setStyleSheet("color: gray; font-size: 10px;")
                tag_label.setAlignment(Qt.AlignCenter)
                layout.addWidget(tag_label)

        # Without this, extra vertical space the grid gives this card
        # (e.g. because another card in the same row is taller, due to
        # having tags and this one not) gets distributed by the layout
        # instead of landing predictably at the bottom -- which is what
        # made the title look like it sat "a percent of the way down"
        # rather than snug under the thumbnail. Pinning the stretch to
        # the bottom keeps thumbnail/title/tags packed together
        # regardless of how tall the card ends up being.
        layout.addStretch(1)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def _title_text(self, video: "library.Video") -> str:
        return f"{FAVORITE_STAR} {video.title}" if video.favorite else video.title

    def _build_icon_row(self, matching: list[tuple[str, str]], vertical: bool) -> QWidget:
        available = THUMB_SIZE.height() if vertical else THUMB_SIZE.width()
        icon_size = _icon_size_for_count(len(matching), available, self._appearance.filter_icon_size)

        container = QWidget()
        row_layout = QVBoxLayout(container) if vertical else QHBoxLayout(container)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(ICON_SPACING)
        # Stretches on both ends center the icons within the row/column
        # rather than left/top-aligning them.
        row_layout.addStretch(1)
        for tag_name, path in matching:
            icon_label = _FilterIconLabel(tag_name, QPixmap(path), icon_size, container)
            icon_label.left_clicked.connect(self.filter_left_clicked.emit)
            icon_label.right_clicked.connect(self.filter_right_clicked.emit)
            row_layout.addWidget(icon_label)
        row_layout.addStretch(1)
        return container

    def set_font_scale(self, factor: float) -> None:
        """Live-updatable independent of set_highlight_enabled -- called
        by the Library's window-size-based scaling (see
        LibraryPage.apply_scale) without needing to rebuild the card."""
        self._current_font_scale = factor
        font = self.title_label.font()
        target_pt = self._base_title_pt * factor
        if self._appearance.resize_text_to_fit:
            # Shrink (never grow past target_pt) until the title's
            # single-line width fits the card -- word-wrap is turned
            # off for this mode (see __init__), so an overlong title
            # needs to shrink instead of wrapping to a second line.
            from PySide6.QtGui import QFontMetricsF
            pt = target_pt
            max_width = THUMB_SIZE.width() - 8  # small margin, matches layout's own content margins
            while pt > 6.0:
                font.setPointSizeF(pt)
                if QFontMetricsF(font).horizontalAdvance(self.title_label.text()) <= max_width:
                    break
                pt -= 0.5
            font.setPointSizeF(pt)
        else:
            font.setPointSizeF(target_pt)
        self.title_label.setFont(font)

    def set_highlight_enabled(self, enabled: bool) -> None:
        """Called live by the Library's "Highlight Unedited" toggle --
        no need to rebuild/recreate cards, just repaint them."""
        self._highlight_enabled = enabled
        self.update()

    def set_selected(self, selected: bool) -> None:
        """Called by the grid's selection handling (_VideoGridTab) --
        a selected card's border always wins over the unedited-highlight
        one (see paintEvent), regardless of the video's edited state or
        the "Highlight Unedited" toggle, since selection is a separate,
        higher-priority concept from either."""
        if selected != self._selected:
            self._selected = selected
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
        if self._selected:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            # Gold/white gradient image (selected_border_gradient.png by
            # default, or a custom override) stretched to fill, same
            # technique as the unedited-highlight branch below -- no
            # brightness/multiply-darken step here, since a selection
            # border is a UI-chrome indicator rather than a
            # brightness-tunable highlight like the unedited one.
            painter.drawPixmap(self.rect(), self._selected_border_pixmap)
            border_width = self._appearance.unedited_selected_border_width
            inner_rect = self.rect().adjusted(border_width, border_width, -border_width, -border_width)
            painter.fillRect(inner_rect, self.palette().window())
            painter.end()
        elif self._should_show_highlight():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            # Full-widget gradient first, then an inset fill in the
            # normal background color on top -- only the outer rim (not
            # covered by the inset) ends up showing the gradient, which
            # is what reads as a "border" rather than a solid highlight
            # fill. The inset here is independent of the layout's own
            # 4px content margin (children never fully reach the
            # widget's edge either way), so it works regardless of
            # whatever's between the thumbnail/title internally.
            painter.drawPixmap(self.rect(), self._highlight_pixmap)
            darken_factor = 1 - (self._appearance.unedited_highlight_brightness / 100.0)
            if darken_factor > 0:
                gray = round(255 * (1 - darken_factor))
                painter.setCompositionMode(QPainter.CompositionMode_Multiply)
                painter.fillRect(self.rect(), QColor(gray, gray, gray))
            border_width = self._appearance.unedited_selected_border_width
            inner_rect = self.rect().adjusted(border_width, border_width, -border_width, -border_width)
            painter.fillRect(inner_rect, self.palette().window())
            painter.end()
        super().paintEvent(event)

    def _load_pixmap(self, video: "library.Video") -> QPixmap:
        thumb_path = thumbnails.get_thumbnail(video.id, Path(video.path))
        if thumb_path is None:
            return _placeholder_pixmap()
        pixmap = QPixmap(str(thumb_path))
        if pixmap.isNull():
            return _placeholder_pixmap()
        return pixmap.scaled(THUMB_SIZE, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.video_id, event.modifiers())
            # Explicitly accept (rather than falling through to
            # QWidget's default, which ignores it) -- an ignored event
            # bubbles up to the parent's own mousePressEvent, which
            # would otherwise immediately clear the selection this
            # just set via the grid container's own background-click
            # handling (see _SelectionClearingContainer).
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
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

        filters_menu = self._build_filters_menu(menu, target_ids, target_videos)
        menu.addMenu(filters_menu)

        copy_action = menu.addAction(f"Copy{count_suffix}")
        delete_action = menu.addAction(f"Delete{count_suffix}")

        chosen = menu.exec(self.mapToGlobal(pos))
        if edit_action is not None and chosen == edit_action:
            self.edit_requested.emit(self.video_id)
        elif rename_action is not None and chosen == rename_action:
            self._rename()
        elif chosen == favorite_action:
            self._bulk_set_favorite(target_ids, not all_favorited)
        elif chosen == upload_action:
            for vid in target_ids:
                self.upload_requested.emit(vid)
        elif chosen == copy_action:
            self._bulk_copy_to_clipboard(target_ids)
        elif chosen == delete_action:
            self._bulk_delete(target_ids)

    def _build_filters_menu(self, parent_menu: QMenu, target_ids: set[int],
                             target_videos: list["library.Video"]) -> QMenu:
        """Replaces the old single-tag "Add Filter" dialog with a
        side-opening submenu (hover to open, like the category submenus
        in the Library's own Filters dropdown) listing every known tag,
        grouped into the same categories, each as a checkbox: checked
        when EVERY video in target_ids already has that tag, toggling
        adds/removes it across all of them at once. A "+" at the bottom
        creates a brand new tag (globally -- mirrors the Library
        dropdown's own "+ Add Filter", which also just creates the tag
        without applying it to anything)."""
        menu = QMenu("Filters", parent_menu)

        def make_checkbox(tag: str, target_menu: QMenu) -> None:
            checkbox = QCheckBox(tag, target_menu)
            checkbox.setChecked(all(tag in v.tags for v in target_videos))

            def on_toggled(checked: bool, tag=tag) -> None:
                for vid in target_ids:
                    if checked:
                        library.add_tag_to_video(vid, tag)
                    else:
                        library.remove_tag_from_video(vid, tag)
                self.tags_changed.emit()

            checkbox.toggled.connect(on_toggled)
            action = QWidgetAction(target_menu)
            action.setDefaultWidget(checkbox)
            target_menu.addAction(action)

        all_tags = library.all_known_tags()
        grouped, uncategorized = library.tags_grouped_by_category()

        if not all_tags:
            no_tags_action = menu.addAction("(no tags yet)")
            no_tags_action.setEnabled(False)

        for category_name, tag_names in grouped.items():
            category_menu = QMenu(category_name, menu)
            for tag in tag_names:
                make_checkbox(tag, category_menu)
            menu.addMenu(category_menu)

        for tag in uncategorized:
            make_checkbox(tag, menu)

        menu.addSeparator()
        add_filter_btn = QPushButton("+ Add Filter")
        add_filter_btn.setFlat(True)
        add_filter_btn.clicked.connect(self._create_new_filter)
        add_filter_action = QWidgetAction(menu)
        add_filter_action.setDefaultWidget(add_filter_btn)
        menu.addAction(add_filter_action)

        return menu

    def _create_new_filter(self) -> None:
        name, ok = QInputDialog.getText(self, "Add Filter", "Filter name:")
        name = name.strip()
        if ok and name:
            library.create_tag(name)
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

        urls = []
        missing = []
        for vid in target_ids:
            video = library.get_video(vid)
            path = Path(video.path)
            if path.exists():
                urls.append(QUrl.fromLocalFile(str(path)))
            else:
                missing.append(str(path))
        if missing:
            QMessageBox.warning(
                self, "Copy Failed",
                "File(s) not found:\n" + "\n".join(missing),
            )
        if urls:
            mime = QMimeData()
            mime.setUrls(urls)
            QApplication.clipboard().setMimeData(mime)

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
        new_title, ok = QInputDialog.getText(
            self, "Rename Video", "Title:", QLineEdit.Normal, self._video.title
        )
        if not ok:
            return
        new_title = new_title.strip()
        if not new_title or new_title == self._video.title:
            return
        self._video = library.rename_video(self.video_id, title=new_title)
        self.title_label.setText(self._title_text(self._video))
        self.set_font_scale(self._current_font_scale)  # re-fit if Resize Text to Fit is on
        self.renamed.emit()
