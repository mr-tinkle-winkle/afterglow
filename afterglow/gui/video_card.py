"""
One clickable card in the Library grid: thumbnail + title below it, with a
right-click context menu (Edit / Upload / Delete / Add Filter).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QMenu, QMessageBox, QLineEdit,
    QPushButton, QDialog, QHBoxLayout, QInputDialog, QComboBox,
    QDialogButtonBox,
)

from .. import library, thumbnails, config as config_module
from .resources import resource_qpixmap

THUMB_SIZE = QSize(400, 224)  # 16:9, doubled from the original 200x112
FAVORITE_STAR = "\u2605"  # "★"


def _placeholder_pixmap() -> QPixmap:
    pixmap = QPixmap(THUMB_SIZE)
    pixmap.fill(QColor("#2a2a2a"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#888"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "No preview")
    painter.end()
    return pixmap


class AddTagDialog(QDialog):
    """A dropdown of every existing tag, plus a '+' button that prompts
    for a brand new tag name and adds/selects it in the dropdown --
    replaces the earlier free-text-with-autocomplete version, which read
    as more error-prone (a typo silently creates a new near-duplicate
    tag rather than picking the existing one)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Tag")
        self.chosen_tag: str | None = None

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.tag_combo = QComboBox()
        self.tag_combo.addItems(library.all_known_tags())
        row.addWidget(self.tag_combo, stretch=1)

        add_btn = QPushButton("+")
        add_btn.setFixedWidth(30)
        add_btn.setToolTip("Create a new tag")
        add_btn.clicked.connect(self._create_new_tag)
        row.addWidget(add_btn)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_new_tag(self) -> None:
        name, ok = QInputDialog.getText(self, "New Tag", "Tag name:")
        name = name.strip()
        if not ok or not name:
            return
        if self.tag_combo.findText(name, Qt.MatchFixedString) < 0:
            self.tag_combo.addItem(name)
        self.tag_combo.setCurrentText(name)

    def _accept_selected(self) -> None:
        text = self.tag_combo.currentText().strip()
        if text:
            self.chosen_tag = text
            self.accept()


class VideoCard(QWidget):
    edit_requested = Signal(int)      # video_id
    deleted = Signal(int)             # video_id
    upload_requested = Signal(int)    # video_id
    tags_changed = Signal()           # tag added/removed -- parent should refresh filter list
    renamed = Signal()                # title changed -- parent should refresh (search may no longer match)

    def __init__(self, video: "library.Video", parent=None, highlight_enabled: bool = True,
                 font_scale: float = 1.0):
        super().__init__(parent)
        self.video_id = video.id
        self._video = video
        self._highlight_enabled = highlight_enabled
        self._highlight_pixmap = resource_qpixmap("unedited_highlight_gradient.png")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        display_settings = config_module.load().filter_display
        icons = library.tag_icons()
        matching_icons = [icons[t] for t in video.tags if t in icons]

        icon_row_above = None
        if display_settings.show_filter_icons and matching_icons and \
                display_settings.filter_icon_location == "above":
            icon_row_above = self._build_icon_row(matching_icons, vertical=False)
            layout.addWidget(icon_row_above)

        thumb_row = QHBoxLayout()
        if display_settings.show_filter_icons and matching_icons and \
                display_settings.filter_icon_location == "vtile_left":
            thumb_row.addWidget(self._build_icon_row(matching_icons, vertical=True))

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(THUMB_SIZE)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setPixmap(self._load_pixmap(video))
        thumb_row.addWidget(self.thumb_label)

        if display_settings.show_filter_icons and matching_icons and \
                display_settings.filter_icon_location == "vtile_right":
            thumb_row.addWidget(self._build_icon_row(matching_icons, vertical=True))
        layout.addLayout(thumb_row)

        if display_settings.show_filter_icons and matching_icons and \
                display_settings.filter_icon_location == "below":
            layout.addWidget(self._build_icon_row(matching_icons, vertical=False))

        self.title_label = QLabel(self._title_text(video))
        self.title_label.setWordWrap(True)
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
        self.set_font_scale(font_scale)
        layout.addWidget(self.title_label)

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

    def _build_icon_row(self, icon_paths: list[str], vertical: bool) -> QWidget:
        container = QWidget()
        row_layout = QVBoxLayout(container) if vertical else QHBoxLayout(container)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(2)
        for path in icon_paths:
            icon_label = QLabel()
            pixmap = QPixmap(path)
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(QSize(18, 18), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            row_layout.addWidget(icon_label)
        if vertical:
            row_layout.addStretch(1)
        return container

    def set_font_scale(self, factor: float) -> None:
        """Live-updatable independent of set_highlight_enabled -- called
        by the Library's window-size-based scaling (see
        LibraryPage.apply_scale) without needing to rebuild the card."""
        font = self.title_label.font()
        font.setPointSizeF(self._base_title_pt * factor)
        self.title_label.setFont(font)

    def set_highlight_enabled(self, enabled: bool) -> None:
        """Called live by the Library's "Highlight Unedited" toggle --
        no need to rebuild/recreate cards, just repaint them."""
        self._highlight_enabled = enabled
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
        if self._should_show_highlight():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            # Full-widget gradient first, then an inset fill in the
            # normal background color on top -- only the outer rim (not
            # covered by the inset) ends up showing the gradient, which
            # is what reads as a "border" rather than a solid highlight
            # fill. The 3px inset here is independent of the layout's
            # own 4px content margin (children never fully reach the
            # widget's edge either way), so it works regardless of
            # whatever's between the thumbnail/title internally.
            painter.drawPixmap(self.rect(), self._highlight_pixmap)
            border_width = 3
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

    def mouseDoubleClickEvent(self, event) -> None:
        self.edit_requested.emit(self.video_id)

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        edit_action = menu.addAction("Edit")
        rename_action = menu.addAction("Rename")
        favorite_action = menu.addAction(
            "Unfavorite" if self._video.favorite else "Favorite"
        )
        upload_action = menu.addAction("Upload")
        add_filter_action = menu.addAction("Add Filter")
        delete_action = menu.addAction("Delete")

        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == edit_action:
            self.edit_requested.emit(self.video_id)
        elif chosen == rename_action:
            self._rename()
        elif chosen == favorite_action:
            self._toggle_favorite()
        elif chosen == upload_action:
            self.upload_requested.emit(self.video_id)
        elif chosen == add_filter_action:
            self._add_filter()
        elif chosen == delete_action:
            self._confirm_delete()

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
        self.renamed.emit()

    def _toggle_favorite(self) -> None:
        self._video = library.set_favorite(self.video_id, not self._video.favorite)
        self.title_label.setText(self._title_text(self._video))
        self.tags_changed.emit()

    def _add_filter(self) -> None:
        dialog = AddTagDialog(self)
        if dialog.exec() and dialog.chosen_tag:
            library.add_tag_to_video(self.video_id, dialog.chosen_tag)
            self.tags_changed.emit()

    def _confirm_delete(self) -> None:
        reply = QMessageBox.question(
            self, "Delete Video",
            f"Delete '{self._video.title}'? This removes the file from disk and can't be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            library.delete_video(self.video_id)
            self.deleted.emit(self.video_id)
