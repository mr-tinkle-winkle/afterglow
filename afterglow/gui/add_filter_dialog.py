"""
Shared "Add Filter" dialog -- replaces the old plain QInputDialog.getText()
used in three places (the Library's own "+ Add Filter", the right-click
context menu's Filters submenu, and Settings > Filters' new "+ Add New"
button) with one custom-styled dialog that also lets a category be
assigned right at creation time, instead of needing a second trip to
Settings > Filters afterward to categorize it.
"""
from __future__ import annotations

from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox

from .. import config as config_module
from .theme import Theme
from .custom_button import CustomButton
from .custom_line_edit import CustomLineEdit


def _combo_stylesheet(appearance) -> str:
    """QSS reskin for QComboBox -- same theme colors as the QMenu
    reskin elsewhere (card background, accent border/highlight,
    rounded corners), so the closed box and its popup list both match
    the app's own look instead of native/KDE chrome. A genuine custom
    dropdown (its own popup widget, not a styled QComboBox) is a
    bigger, separate undertaking -- this covers the "fully custom"
    ask for THIS dialog's own look without that larger rebuild."""
    return f"""
        QComboBox {{
            background-color: {appearance.afterglow_color_card_background};
            color: {appearance.card_text_color};
            border: 1px solid {appearance.afterglow_color_accent};
            border-radius: 6px;
            padding: 4px 8px;
        }}
        QComboBox::drop-down {{
            border: none;
        }}
        QComboBox QAbstractItemView {{
            background-color: {appearance.afterglow_color_card_background};
            color: {appearance.card_text_color};
            border: 1px solid {appearance.afterglow_color_accent};
            selection-background-color: {appearance.afterglow_color_accent};
            outline: none;
        }}
    """


class AddFilterDialog(QDialog):
    def __init__(self, categories: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Filter")
        appearance = config_module.load().appearance
        theme = Theme(appearance)
        self.setStyleSheet(f"QDialog {{ background-color: {theme.library_background().name()}; }}")

        layout = QVBoxLayout(self)

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
        self.category_combo.setStyleSheet(_combo_stylesheet(appearance))
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
