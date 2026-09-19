"""
Replaces the QMenu-based Filters submenu (both the context menu's
"Filters" entry and the quick-action Filters button) with a genuine
Qt.Popup-flagged custom widget -- the SAME proven technique
SearchBubble/SortPopover already use reliably elsewhere in this app.

This exists because the QMenu version's "stay open while toggling
several checkboxes" behavior was fixed at the QMenu level FOUR separate
times (checking activeAction()/actionAt() in mousePressEvent/
mouseReleaseEvent; a hideEvent-based suppression/re-show; pausing the
debounced grid refresh while a menu is open) and still didn't reliably
close only-when-it-should. Rather than attempt a fifth QMenu-level fix
against a native mechanism that's evidently NOT any of the things
already tried, this sidesteps QMenu (and its "auto-close on ANY click"
default behavior) entirely: Qt.Popup's own NATIVE behavior is exactly
"a click outside this widget closes it; clicks inside are delivered
normally to whatever's there and never close it on their own" -- which
is precisely what's needed, and is already relied on successfully by
two other widgets in this exact codebase.
"""
from __future__ import annotations

from pathlib import Path
from PySide6.QtCore import Qt, QRectF, QPoint, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QLabel

from .. import library
from .. import config as config_module
from .theme import Theme
from .rounded_rect import rounded_rect_path
from .custom_checkbox import CustomCheckBox
from .custom_group_box import CustomGroupBox
from .custom_button import CustomButton
from .smooth_scroll_area import SmoothScrollArea

_WIDTH = 280
_MAX_HEIGHT = 420  # tiles into a scrollable area past this, rather than growing off-screen forever


class FiltersPopup(QWidget):
    # Emitted from hideEvent, NOT relying on `destroyed` -- a Qt.Popup
    # widget typically just HIDES (not destroys) when an outside click
    # closes it natively, so `destroyed` would likely never fire at
    # all here, which would have permanently leaked VideoCard's own
    # context_menu_opened/closed open-count the very first time this
    # was ever used (blocking every future debounced grid refresh,
    # forever, for that tab).
    closed = Signal()

    def __init__(self, target_ids: set[int], target_videos: list["library.Video"],
                 on_tags_changed, on_create_new_filter, parent=None):
        # Flags passed DIRECTLY to the constructor, not via a separate
        # setWindowFlags() call afterward -- both proven-working
        # references (SearchBubble, SortPopover) do it this way, and
        # setWindowFlags() on an already-constructed widget requires
        # Qt to re-create the underlying platform window to fully take
        # effect, which doesn't reliably happen in every case. This,
        # combined with using Qt.Popup ALONE (missing
        # Qt.FramelessWindowHint, which both references always pair
        # it with), are the two concrete differences found between
        # this and the two working references after "it still closes"
        # was reported a fifth time.
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        appearance = config_module.load().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self._target_ids = target_ids
        self._on_tags_changed = on_tags_changed

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 10, 10, 10)

        tag_icon_paths = library.tag_icons()

        def make_checkbox(tag: str, target_layout: QVBoxLayout) -> None:
            leading_icon = None
            icon_path = tag_icon_paths.get(tag)
            if icon_path and Path(icon_path).exists():
                pixmap = QPixmap(icon_path)
                if not pixmap.isNull():
                    leading_icon = pixmap
            checkbox = CustomCheckBox(tag, leading_icon=leading_icon)
            checkbox.setChecked(all(tag in v.tags for v in target_videos))

            def on_toggled(checked: bool, tag=tag) -> None:
                for vid in target_ids:
                    if checked:
                        library.add_tag_to_video(vid, tag)
                    else:
                        library.remove_tag_from_video(vid, tag)
                self._on_tags_changed()

            checkbox.toggled.connect(on_toggled)
            target_layout.addWidget(checkbox)

        all_tags = library.all_known_tags()
        grouped, uncategorized = library.tags_grouped_by_category()

        if not all_tags:
            no_tags_label = QLabel("(no tags yet)")
            no_tags_label.setStyleSheet("color: gray;")
            content_layout.addWidget(no_tags_label)

        for category_name, tag_names in grouped.items():
            group = CustomGroupBox(category_name)
            group_layout = group.make_layout(QVBoxLayout)
            for tag in tag_names:
                make_checkbox(tag, group_layout)
            content_layout.addWidget(group)

        for tag in uncategorized:
            make_checkbox(tag, content_layout)

        content_layout.addStretch(1)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setAttribute(Qt.WA_TranslucentBackground, True)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setWidget(content)
        natural_height = content.sizeHint().height() + 20
        scroll.setFixedHeight(min(natural_height, _MAX_HEIGHT))
        outer.addWidget(scroll)

        add_filter_btn = CustomButton("+ Add Filter")
        add_filter_btn.clicked.connect(on_create_new_filter)
        outer.addWidget(add_filter_btn)

        self.setFixedWidth(_WIDTH)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 12
        pen = painter.pen()
        pen.setColor(self._theme.accent())
        pen.setWidthF(2)
        painter.setPen(pen)
        if radius:
            path = rounded_rect_path(rect.adjusted(1, 1, -1, -1), radius)
            painter.fillPath(path, self._theme.library_background())
            painter.drawPath(path)
        else:
            painter.fillRect(rect, self._theme.library_background())
            painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.end()

    def show_near(self, anchor_global_point: QPoint) -> None:
        self.adjustSize()
        self.move(anchor_global_point)
        self.show()

    def hideEvent(self, event) -> None:
        self.closed.emit()
        super().hideEvent(event)
        self.deleteLater()
