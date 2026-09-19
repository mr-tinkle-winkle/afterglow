"""
Shared "Add Filter" dialog -- replaces the old plain QInputDialog.getText()
used in three places (the Library's own "+ Add Filter", the right-click
context menu's Filters submenu, and Settings > Filters' new "+ Add New"
button) with one custom-styled dialog that also lets a category be
assigned right at creation time, instead of needing a second trip to
Settings > Filters afterward to categorize it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox

from .. import config as config_module
from .theme import Theme
from .rounded_rect import rounded_rect_path
from .custom_button import CustomButton
from .custom_line_edit import CustomLineEdit
from .custom_combo_style import combo_box_stylesheet


class AddFilterDialog(QDialog):
    def __init__(self, categories: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Filter")
        appearance = config_module.load().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        # Frameless + translucent + a custom-painted rounded fill,
        # same technique as the video previewer's own rounded corners
        # -- a plain setStyleSheet() background-color on a normal
        # QDialog still renders with square corners (the window's own
        # native frame), which is what was reported.
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        name_label = QLabel("Filter name:")
        name_label.setStyleSheet(f"color: {appearance.card_text_color};")
        layout.addWidget(name_label)
        self.name_edit = CustomLineEdit()
        self.name_edit.setPlaceholderText("New filter name")
        layout.addWidget(self.name_edit)

        category_label = QLabel("Category (optional):")
        category_label.setStyleSheet(f"color: {appearance.card_text_color};")
        layout.addWidget(category_label)
        self.category_combo = QComboBox()
        self.category_combo.setStyleSheet(combo_box_stylesheet(appearance))
        self.category_combo.addItem("(none)", None)
        for cat_id, cat_name in categories:
            self.category_combo.addItem(cat_name, cat_id)
        layout.addWidget(self.category_combo)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        cancel_btn = CustomButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        add_btn = CustomButton("Add")
        add_btn.clicked.connect(self.accept)
        button_row.addWidget(add_btn)
        layout.addLayout(button_row)

        self.name_edit.setFocus()

    def result_values(self) -> tuple[str, int | None]:
        """(name, category_id_or_None) -- only meaningful if exec()
        returned QDialog.Accepted."""
        return self.name_edit.text().strip(), self.category_combo.currentData()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 16
        if radius:
            painter.fillPath(rounded_rect_path(rect, radius), self._theme.library_background())
        else:
            painter.fillRect(rect, self._theme.library_background())
        painter.end()
