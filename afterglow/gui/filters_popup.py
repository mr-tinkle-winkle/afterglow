"""
The video card's "Filters" action button: the SAME filter selector the Library's sort popover
uses (SortPopover's chrome, the same category group boxes and checkboxes with tag icons, the same
"+ Add Filter" button), but applied to the card's own video(s) -- ticking a tag adds it to the
video, unticking removes it -- instead of filtering the grid.

It is a Qt.Popup like the sorter's, so it stays open while several tags are toggled and closes only
on a click outside it.  (It replaces a QMenu that closed on every click.)
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from .. import library
from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .custom_group_box import CustomGroupBox
from .sort_popover import SortPopover


class FiltersPopup(SortPopover):
    """``on_tags_changed()`` runs after every toggle; ``on_create_new_filter()`` for "+ Add Filter"."""
    # Emitted from hideEvent: a Qt.Popup closed by an outside click is hidden, not destroyed.
    closed = Signal()

    def __init__(self, target_ids: "set[int]", target_videos: "list[library.Video]",
                 on_tags_changed, on_create_new_filter, parent=None):
        super().__init__(parent, tab_labels=("Filters",))
        self._target_ids = set(target_ids)
        self._target_videos = list(target_videos)
        self._on_tags_changed = on_tags_changed
        self.checkboxes: "dict[str, CustomCheckBox]" = {}
        self.set_page_widget(0, self._build_page(on_create_new_filter))

    def _build_page(self, on_create_new_filter) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        tag_icon_paths = library.tag_icons()

        def make_checkbox(tag: str, target_layout: QVBoxLayout) -> None:
            leading_icon = None
            icon_path = tag_icon_paths.get(tag)
            if icon_path and Path(icon_path).exists():
                pixmap = QPixmap(icon_path)
                if not pixmap.isNull():
                    leading_icon = pixmap
            checkbox = CustomCheckBox(tag, leading_icon=leading_icon)
            checkbox.setChecked(all(tag in v.tags for v in self._target_videos))
            checkbox.toggled.connect(lambda checked, tag=tag: self._toggle(tag, checked))
            target_layout.addWidget(checkbox)
            self.checkboxes[tag] = checkbox

        all_tags = library.all_known_tags()
        grouped, uncategorized = library.tags_grouped_by_category()
        if not all_tags:
            label = QLabel("(no tags yet)")
            label.setStyleSheet("color: gray;")
            layout.addWidget(label)
        for category_name, tag_names in grouped.items():
            group = CustomGroupBox(category_name)
            group_layout = group.make_layout(QVBoxLayout)
            for tag in tag_names:
                make_checkbox(tag, group_layout)
            layout.addWidget(group)
        for tag in uncategorized:
            make_checkbox(tag, layout)

        add_btn = CustomButton("+ Add Filter")
        add_btn.clicked.connect(on_create_new_filter)
        layout.addWidget(add_btn)
        layout.addStretch(1)
        return self._wrap(page)

    @staticmethod
    def _wrap(page: QWidget) -> QWidget:
        from PySide6.QtCore import Qt
        page.setAttribute(Qt.WA_TranslucentBackground, True)
        return page

    def _toggle(self, tag: str, checked: bool) -> None:
        for vid in self._target_ids:
            if checked:
                library.add_tag_to_video(vid, tag)
            else:
                library.remove_tag_from_video(vid, tag)
        self._on_tags_changed()

    def hideEvent(self, event) -> None:
        self.closed.emit()
        super().hideEvent(event)
        self.deleteLater()
