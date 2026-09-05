"""
One clickable card in the Library grid: thumbnail + title below it, with a
right-click context menu (Edit / Upload / Delete / Add Filter).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QMenu, QMessageBox, QLineEdit,
    QCompleter, QPushButton, QDialog, QHBoxLayout, QInputDialog,
)

from .. import library, thumbnails

THUMB_SIZE = QSize(400, 224)  # 16:9, doubled from the original 200x112


def _placeholder_pixmap() -> QPixmap:
    pixmap = QPixmap(THUMB_SIZE)
    pixmap.fill(QColor("#2a2a2a"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#888"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "No preview")
    painter.end()
    return pixmap


class AddTagDialog(QDialog):
    """Text box (filters a dropdown of existing tags as you type) + a '+'
    button to add the typed text as a brand new tag, per spec."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Tag")
        self.chosen_tag: str | None = None

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.tag_edit = QLineEdit()
        self.tag_edit.setPlaceholderText("Type a tag name...")
        completer = QCompleter(library.all_known_tags(), self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.tag_edit.setCompleter(completer)
        row.addWidget(self.tag_edit)

        add_btn = QPushButton("+")
        add_btn.setFixedWidth(30)
        add_btn.clicked.connect(self._accept_new)
        row.addWidget(add_btn)
        layout.addLayout(row)

        self.tag_edit.returnPressed.connect(self._accept_new)

    def _accept_new(self) -> None:
        text = self.tag_edit.text().strip()
        if text:
            self.chosen_tag = text
            self.accept()


class VideoCard(QWidget):
    edit_requested = Signal(int)      # video_id
    deleted = Signal(int)             # video_id
    upload_requested = Signal(int)    # video_id
    tags_changed = Signal()           # tag added/removed -- parent should refresh filter list
    renamed = Signal()                # title changed -- parent should refresh (search may no longer match)

    def __init__(self, video: "library.Video", parent=None):
        super().__init__(parent)
        self.video_id = video.id
        self._video = video

        # QWidget doesn't paint a stylesheet border by default -- needs
        # this attribute set explicitly, or setStyleSheet below is a
        # silent no-op.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._apply_edit_border(video)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(THUMB_SIZE)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setPixmap(self._load_pixmap(video))
        layout.addWidget(self.thumb_label)

        self.title_label = QLabel(video.title)
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setFixedWidth(THUMB_SIZE.width())
        # 2.5x the app's actual default label size (queried at runtime,
        # not assumed) -- titles were reportedly unreadable at the
        # default size.
        title_font = self.title_label.font()
        base_pt = title_font.pointSizeF()
        if base_pt <= 0:  # some platforms report pixel-based fonts instead
            base_pt = 9.0
        title_font.setPointSizeF(base_pt * 2.5)
        self.title_label.setFont(title_font)
        layout.addWidget(self.title_label)

        if video.tags:
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

    def _apply_edit_border(self, video: "library.Video") -> None:
        # video.has_edit already IS a per-video "has this been trimmed
        # yet" flag, tracked in the DB and kept correct automatically by
        # the existing edit/undo/prune/rescan logic -- a separate
        # tracked list of "unedited" clip paths (built up as new clips
        # arrive, reconciled against the clips folder on startup) would
        # just be a second, independently-maintainable copy of the exact
        # same fact, with its own chance to drift out of sync. Using the
        # field that already exists gets identical visible behavior for
        # free -- new clips start with has_edit=False (bordered),
        # trimming sets it True (border gone), and deleted files are
        # already removed from the DB entirely by prune_missing_videos.
        if video.has_edit:
            self.setStyleSheet("")
        else:
            self.setStyleSheet("VideoCard { border: 3px solid orange; border-radius: 4px; }")

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
        upload_action = menu.addAction("Upload")
        add_filter_action = menu.addAction("Add Filter")
        delete_action = menu.addAction("Delete")

        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == edit_action:
            self.edit_requested.emit(self.video_id)
        elif chosen == rename_action:
            self._rename()
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
        self.title_label.setText(self._video.title)
        self.renamed.emit()

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
