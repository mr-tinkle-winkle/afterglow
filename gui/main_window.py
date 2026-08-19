"""
Main window: left sidebar nav across the eventual 3 pages (Settings, Clips,
Editor). Only Settings is implemented per the current build order --
Clips and Editor are present as disabled nav entries so the layout is
already right for when they land, rather than restructuring the nav later.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QListWidget, QListWidgetItem,
    QStackedWidget, QLabel,
)
from PySide6.QtCore import Qt

from gui.settings_page import SettingsPage


class _ComingSoonPage(QWidget):
    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        label = QLabel(f"{name} — coming soon")
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("clipping-app")
        self.resize(900, 650)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)

        self.nav_list = QListWidget()
        self.nav_list.setFixedWidth(140)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        layout.addWidget(self.nav_list)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        self._add_page("Settings", SettingsPage(), enabled=True)
        self._add_page("Clips", _ComingSoonPage("Clips"), enabled=False)
        self._add_page("Editor", _ComingSoonPage("Editor"), enabled=False)

        self.nav_list.setCurrentRow(0)

    def _add_page(self, name: str, widget: QWidget, enabled: bool) -> None:
        item = QListWidgetItem(name)
        if not enabled:
            item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
        self.nav_list.addItem(item)
        self.stack.addWidget(widget)

    def _on_nav_changed(self, index: int) -> None:
        if index >= 0:
            self.stack.setCurrentIndex(index)
