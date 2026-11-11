"""
The setup guide: every step of getting afterglow working, in order, each
with its LIVE state (setup_checks.py) -- a green tick when it is set up,
what to do when it isn't -- and a button that goes to the settings it is
about. Opens by itself once on a new install (MainWindow.maybe_show_setup_guide,
called from gui/main.py) and any time from Settings > Advanced > Setup Guide.

The checks talk to OBS, so they run on a worker thread; the steps show
"Checking..." until the result arrives.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QWidget, QFrame, QScrollArea

from .. import setup_checks
from .custom_button import CustomButton
from .smooth_scroll_area import SmoothScrollArea
from .themed_dialogs import ThemedDialog
from .theme import contrast_text

_STATE_COLORS = {setup_checks.OK: "#3fbf6f", setup_checks.TODO: "#e8a33d", setup_checks.INFO: "#5aa7e8"}
_STATE_MARKS = {setup_checks.OK: "✓", setup_checks.TODO: "!", setup_checks.INFO: "i"}
_STATE_WORDS = {setup_checks.OK: "Ready", setup_checks.TODO: "To do", setup_checks.INFO: "How it works"}


class _Badge(QWidget):
    """The round state mark in front of a step (and its number)."""

    def __init__(self, state: str, parent=None):
        super().__init__(parent)
        self.state = state
        self.setFixedSize(28, 28)
        self.setToolTip(_STATE_WORDS.get(state, ""))

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        color = QColor(_STATE_COLORS.get(self.state, "#888888"))
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(2, 2, 24, 24))
        p.setPen(QPen(contrast_text(color)))
        f = self.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 2)
        p.setFont(f)
        p.drawText(QRectF(2, 2, 24, 24), Qt.AlignCenter, _STATE_MARKS.get(self.state, "?"))
        p.end()


class StepCard(QWidget):
    """One step: badge, "N. Title", what was found, what to do, a button."""
    go_requested = Signal(object)        # the Check

    def __init__(self, number: int, check: "setup_checks.Check", dialog: ThemedDialog, parent=None):
        super().__init__(parent)
        self.check = check
        self._bg = dialog._theme.card_background()
        self._radius = min(dialog._appearance.rounded_corner_radius, 14) if dialog._appearance.rounded_corners_enabled else 0
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)
        self.badge = _Badge(check.state)
        row.addWidget(self.badge, alignment=Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.title_label = dialog.label(f"{number}. {check.title}")
        f = self.title_label.font()
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() + 1)
        self.title_label.setFont(f)
        col.addWidget(self.title_label)
        self.summary_label = dialog.label(check.summary)
        col.addWidget(self.summary_label)
        self.detail_label = None
        if check.detail:
            self.detail_label = dialog.label(check.detail, dim=True)
            self.detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)   # commands can be copied
            col.addWidget(self.detail_label)
        row.addLayout(col, stretch=1)
        self.go_button = None
        target = (f"Settings > {check.settings_tab}" if check.settings_tab
                  else {"library": "Library", "editor": "Editor"}.get(check.page, ""))
        if target:
            self.go_button = CustomButton("Open " + target)
            self.go_button.setMinimumHeight(28)
            self.go_button.clicked.connect(lambda _c=False: self.go_requested.emit(self.check))
            row.addWidget(self.go_button, alignment=Qt.AlignTop)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self._radius, self._radius)
        p.fillPath(path, self._bg)
        p.end()


class SetupGuideDialog(ThemedDialog):
    _results = Signal(object, int)       # checks, generation   (worker thread -> GUI thread)
    navigate_requested = Signal(object)  # a Check whose "Open ..." button was pressed

    def __init__(self, parent=None, run_checks=None):
        super().__init__("Setup Guide", parent)
        self._run_checks = run_checks or setup_checks.run_checks
        self._generation = 0
        self.checks: list = []
        self.cards: list[StepCard] = []
        self.setMinimumSize(720, 520)
        self.resize(820, 720)

        self.intro_label = self.label(
            "What afterglow needs to save clips, in the order to set it up, checked against how things are right "
            "now. Fix a step, then press Check Again.")
        self.lay.addWidget(self.intro_label)
        self.progress_label = self.label("Checking...")
        f = self.progress_label.font()
        f.setBold(True)
        self.progress_label.setFont(f)
        self.lay.addWidget(self.progress_label)

        self.scroll = SmoothScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._body = QWidget()
        self._body.setAutoFillBackground(False)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(0, 0, 6, 0)
        self._body_layout.setSpacing(8)
        self._body_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self._body)
        self._body.setAutoFillBackground(False)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setAutoFillBackground(False)
        self.lay.addWidget(self.scroll, stretch=1)

        buttons = QHBoxLayout()
        self.recheck_button = CustomButton("Check Again")
        self.recheck_button.clicked.connect(lambda _c=False: self.start_checks())
        buttons.addWidget(self.recheck_button)
        buttons.addStretch(1)
        self.close_button = CustomButton("Close")
        self.close_button.clicked.connect(self.accept)
        for b in (self.recheck_button, self.close_button):
            b.setMinimumWidth(110)
            b.setMinimumHeight(30)
        buttons.addWidget(self.close_button)
        self.lay.addLayout(buttons)

        self._results.connect(self._show_results, Qt.QueuedConnection)
        self.start_checks()

    # ------------------------------------------------------------ checking

    def start_checks(self) -> None:
        self._generation += 1
        generation = self._generation
        self.recheck_button.setEnabled(False)
        self.progress_label.setText("Checking...")

        def work():
            try:
                checks = self._run_checks()
            except Exception as e:  # noqa: BLE001
                checks = [setup_checks.Check("error", "The checks couldn't run", setup_checks.TODO, str(e))]
            try:
                self._results.emit(checks, generation)
            except RuntimeError:
                pass        # the dialog was closed meanwhile
        threading.Thread(target=work, name="afterglow-setup-checks", daemon=True).start()

    def _show_results(self, checks, generation: int) -> None:
        if generation != self._generation:
            return          # an older run finishing after a newer one started
        self.checks = list(checks)
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        for i, check in enumerate(self.checks, start=1):
            card = StepCard(i, check, self)
            card.go_requested.connect(self.navigate_requested.emit)
            self._body_layout.addWidget(card)
            self.cards.append(card)
        ready, checked = setup_checks.summary(self.checks)
        if checked and ready == checked:
            self.progress_label.setText(f"All {checked} steps are set up. Press a clip hotkey to save a clip.")
        else:
            first = next((c for c in self.checks if c.state == setup_checks.TODO), None)
            self.progress_label.setText(f"{ready} of {checked} steps are set up."
                                        + (f" Next: {first.title}." if first else ""))
        self.recheck_button.setEnabled(True)

    def wait_for_checks(self, timeout: float = 15.0) -> bool:
        """Process events until the running checks have been shown (tests)."""
        import time
        from PySide6.QtCore import QCoreApplication
        end = time.monotonic() + timeout
        while not self.recheck_button.isEnabled() and time.monotonic() < end:
            QCoreApplication.processEvents()
            time.sleep(0.005)
        return self.recheck_button.isEnabled()
