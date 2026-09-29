"""
An in-window Filters panel for the previewer -- the Editor's "change this
clip's filters" feature, brought into the previewer.

Deliberately a plain CHILD widget of the previewer (shown/hidden by the
Filters button), not a Qt.Popup and not a QMenu: both of those were
tried elsewhere in this app for the same checkbox list and never stayed
open reliably while toggling several boxes (see filters_popup.py's
history). A child widget has no auto-close behavior at all, so toggling
any number of checkboxes just works.

Content matches the Library's Filters list: categories as group boxes,
uncategorized tags below, tag icons as leading icons, and a "+ Add
Filter" button that opens the shared AddFilterDialog. Tag changes are
written straight to the library and reported via `tags_changed`.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QLabel, QDialog

from .. import library
from .. import config as config_module
from .theme import Theme
from .rounded_rect import rounded_rect_path
from .custom_checkbox import CustomCheckBox
from .custom_group_box import CustomGroupBox
from .custom_button import CustomButton
from .smooth_scroll_area import SmoothScrollArea

PANEL_WIDTH = 300
MAX_BODY_HEIGHT = 420


class PreviewFiltersPanel(QWidget):
    tags_changed = Signal()
    close_requested = Signal()

    def __init__(self, video_id: int, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self._video_id = video_id
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(8, 8, 8, 8)
        self.setFixedWidth(PANEL_WIDTH)
        self._scroll = None
        self._build()

    def set_video(self, video_id: int) -> None:
        self._video_id = video_id
        self._build()

    def _clear(self) -> None:
        while self._outer.count():
            item = self._outer.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _build(self) -> None:
        self._clear()
        video = library.get_video(self._video_id)
        tag_icon_paths = library.tag_icons()

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)

        def make_checkbox(tag: str, target_layout: QVBoxLayout) -> None:
            leading_icon = None
            icon_path = tag_icon_paths.get(tag)
            if icon_path and Path(icon_path).exists():
                pixmap = QPixmap(icon_path)
                if not pixmap.isNull():
                    leading_icon = pixmap
            checkbox = CustomCheckBox(tag, leading_icon=leading_icon)
            checkbox.setChecked(tag in video.tags)
            checkbox.toggled.connect(lambda checked, t=tag: self._toggle(t, checked))
            target_layout.addWidget(checkbox)

        all_tags = library.all_known_tags()
        grouped, uncategorized = library.tags_grouped_by_category()
        if not all_tags:
            label = QLabel("(no filters yet)")
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
        layout.addStretch(1)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setAttribute(Qt.WA_TranslucentBackground, True)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(content)
        scroll.setFixedHeight(min(content.sizeHint().height() + 12, MAX_BODY_HEIGHT))
        self._outer.addWidget(scroll)
        self._scroll = scroll

        row = QHBoxLayout()
        add_btn = CustomButton("+ Add Filter")
        add_btn.clicked.connect(self._create_new_filter)
        row.addWidget(add_btn)
        done_btn = CustomButton("Done")
        done_btn.clicked.connect(self.close_requested.emit)
        row.addWidget(done_btn)
        self._outer.addLayout(row)
        self.adjustSize()

    def _toggle(self, tag: str, checked: bool) -> None:
        if checked:
            library.add_tag_to_video(self._video_id, tag)
        else:
            library.remove_tag_from_video(self._video_id, tag)
        self.tags_changed.emit()

    def _create_new_filter(self) -> None:
        from .add_filter_dialog import AddFilterDialog
        dialog = AddFilterDialog(library.all_categories(), parent=self.window())
        if dialog.exec() != QDialog.Accepted:
            return
        name, category_id = dialog.result_values()
        if not name:
            return
        # Same as the Editor's Add Filter: create it and apply it to the
        # clip being viewed (the Editor asks; the previewer applies,
        # since adding a filter from a clip's own panel almost always
        # means "this clip needs it").
        library.add_tag_to_video(self._video_id, name)
        if category_id is not None:
            tag_id = next((tid for tid, tname in library.all_tags_with_ids() if tname == name), None)
            if tag_id is not None:
                library.set_tag_category(tag_id, category_id)
        self._build()
        self.tags_changed.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 12
        pen = painter.pen()
        pen.setColor(self._theme.accent())
        pen.setWidthF(2)
        painter.setPen(pen)
        path = rounded_rect_path(rect, radius) if radius else None
        if path is not None:
            painter.fillPath(path, self._theme.library_background())
            painter.drawPath(path)
        else:
            painter.fillRect(rect, self._theme.library_background())
            painter.drawRect(rect)
        painter.end()
