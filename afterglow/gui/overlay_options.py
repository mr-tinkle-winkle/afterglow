"""
Input overlay (Puppetry) settings widgets:

  * PlacementEditor        -- one row per overlay piece: show, X / Y / Size (% of the
                              video) and rotation. With ``inherit=True`` (a clip type's
                              overrides) each row also has "Use global", which leaves
                              that piece out of the stored overrides.
  * OverlayOptionsDialog   -- a clip type's "..." button: which pieces to capture, show
                              by default, timing offset, per-piece placement overrides.
  * InputOverlaySettingsPage -- Settings > Input Overlay: the global default placements
                              (+ Puppetry's availability).

Placements are stored as fractions of the video ({x, y, w}) plus degrees of rotation
and a visible flag -- see input_overlay.py / overlay_support.py.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QWidget, QFrame,
)

from .. import config as config_module
from .. import overlay_support
from ..input_overlay import PIECES, DEFAULT_PLACEMENT
from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .custom_group_box import CustomGroupBox
from .custom_spinbox import CustomDoubleSpinBox
from .rounded_rect import rounded_rect_path
from .theme import Theme

PIECE_LABELS = {
    "full": "Keyboard + mouse (one picture)",
    "keyboard": "Keyboard",
    "mouse": "Mouse",
    "controller": "Controller",
    "simple": "Held inputs (text)",
    "movement": "Mouse-movement arrow",
}


def _pct_spin(lo: float, hi: float, value: float, suffix: str = " %") -> CustomDoubleSpinBox:
    s = CustomDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(1)
    s.setSingleStep(1.0)
    s.setSuffix(suffix)
    s.setValue(value)
    return s


class PlacementEditor(QWidget):
    """Rows of {visible, x, y, w, rotation} per piece in ``pieces``."""

    def __init__(self, pieces=PIECES, inherit: bool = False, parent=None):
        super().__init__(parent)
        self._inherit = inherit
        self._rows: dict[str, dict] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        heads = (["Piece"] + (["Use global"] if inherit else []) + ["Shown", "X", "Y", "Size", "Rotation"])
        for c, h in enumerate(heads):
            lab = QLabel(f"<b>{h}</b>")
            grid.addWidget(lab, 0, c)
        for r, piece in enumerate(pieces, start=1):
            d = DEFAULT_PLACEMENT[piece]
            row = {"label": QLabel(PIECE_LABELS.get(piece, piece))}
            c = 0
            grid.addWidget(row["label"], r, c)
            c += 1
            if inherit:
                row["inherit"] = CustomCheckBox("")
                row["inherit"].setChecked(True)
                row["inherit"].toggled.connect(lambda _=False, p=piece: self._sync_enabled(p))
                grid.addWidget(row["inherit"], r, c)
                c += 1
            row["visible"] = CustomCheckBox("")
            row["visible"].setChecked(True)
            row["x"] = _pct_spin(-100, 200, d["x"] * 100)
            row["y"] = _pct_spin(-100, 200, d["y"] * 100)
            row["w"] = _pct_spin(1, 300, d["w"] * 100)
            row["rotation"] = _pct_spin(-360, 360, 0.0, suffix="°")
            for key in ("visible", "x", "y", "w", "rotation"):
                grid.addWidget(row[key], r, c)
                c += 1
            self._rows[piece] = row
            self._sync_enabled(piece)

    def _sync_enabled(self, piece: str) -> None:
        row = self._rows[piece]
        on = not (self._inherit and row["inherit"].isChecked())
        for key in ("visible", "x", "y", "w", "rotation"):
            row[key].setEnabled(on)

    def set_values(self, values: dict) -> None:
        """values: {piece: {x, y, w, rotation, visible}} (fractions). In
        inherit mode a piece missing from ``values`` is "Use global"."""
        for piece, row in self._rows.items():
            have = bool(values.get(piece))
            if self._inherit:
                row["inherit"].setChecked(not have)
            p = values.get(piece) or {}
            d = DEFAULT_PLACEMENT[piece]
            row["visible"].setChecked(bool(p.get("visible", True)))
            row["x"].setValue(float(p.get("x", d["x"])) * 100)
            row["y"].setValue(float(p.get("y", d["y"])) * 100)
            row["w"].setValue(float(p.get("w", d["w"])) * 100)
            row["rotation"].setValue(float(p.get("rotation", 0.0)))
            self._sync_enabled(piece)

    def values(self) -> dict:
        out = {}
        for piece, row in self._rows.items():
            if self._inherit and row["inherit"].isChecked():
                continue
            out[piece] = {"x": row["x"].value() / 100, "y": row["y"].value() / 100, "w": row["w"].value() / 100,
                          "rotation": row["rotation"].value(), "visible": row["visible"].isChecked()}
        return out


class OverlayOptionsDialog(QDialog):
    """A clip type's input-overlay options (its "..." button)."""

    def __init__(self, pieces: list, visible_default: bool, offset_ms: float, placements: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Input Overlay")
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        layout.addWidget(QLabel("<b>Pieces captured with each clip</b>"))
        self._piece_checks: dict[str, CustomCheckBox] = {}
        for piece in PIECES:
            cb = CustomCheckBox(PIECE_LABELS[piece])
            cb.setChecked(piece in pieces)
            self._piece_checks[piece] = cb
            layout.addWidget(cb)

        self.show_default_check = CustomCheckBox("Show the overlay by default (previewer and Editor)")
        self.show_default_check.setChecked(visible_default)
        layout.addWidget(self.show_default_check)

        off_row = QHBoxLayout()
        off_row.addWidget(QLabel("Timing offset:"))
        self.offset_spin = CustomDoubleSpinBox()
        self.offset_spin.setRange(-2000, 2000)
        self.offset_spin.setDecimals(0)
        self.offset_spin.setSuffix(" ms")
        self.offset_spin.setValue(offset_ms)
        self.offset_spin.setToolTip("+ shows inputs later, − earlier. A few frames at most; calibrate against a clip.")
        off_row.addWidget(self.offset_spin)
        off_row.addStretch(1)
        layout.addLayout(off_row)

        layout.addWidget(QLabel("<b>Placement for this clip type</b> -- pieces set to \"Use global\" follow "
                                "Settings › Input Overlay"))
        self.placements = PlacementEditor(inherit=True)
        self.placements.set_values(placements)
        layout.addWidget(self.placements)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = CustomButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = CustomButton("OK")
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

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

    def result_values(self) -> dict:
        return {
            "overlay_pieces": [p for p in PIECES if self._piece_checks[p].isChecked()] or ["keyboard", "mouse"],
            "overlay_visible_default": self.show_default_check.isChecked(),
            "overlay_offset_ms": float(self.offset_spin.value()),
            "overlay_placements": self.placements.values(),
        }


class InputOverlaySettingsPage(QWidget):
    """Settings > Input Overlay: global default placement of each piece."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        outer = QVBoxLayout(self)

        status_group = CustomGroupBox("Puppetry")
        sl = status_group.make_layout(QVBoxLayout)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        sl.addWidget(self.status_label)
        refresh = CustomButton("Check again")
        refresh.clicked.connect(self.refresh_status)
        sl.addWidget(refresh, alignment=Qt.AlignLeft)
        outer.addWidget(status_group)

        group = CustomGroupBox("Default placement")
        gl = group.make_layout(QVBoxLayout)
        note = QLabel("Where each piece sits on a new clip, as a percentage of the video. A clip type can override "
                      "single pieces (its \"…\" button under Clipping). Pieces are placed in the Editor per clip.")
        note.setWordWrap(True)
        gl.addWidget(note)
        self.editor = PlacementEditor()
        self.editor.set_values(overlay_support.resolve_placements(settings.overlay_placements))
        gl.addWidget(self.editor)
        reset = CustomButton("Reset to built-in defaults")
        reset.clicked.connect(lambda: self.editor.set_values(overlay_support.resolve_placements({})))
        gl.addWidget(reset, alignment=Qt.AlignLeft)
        outer.addWidget(group)
        outer.addStretch(1)
        self.refresh_status()

    def refresh_status(self) -> None:
        ok, why = overlay_support.available()
        self.status_label.setText(("✔ " if ok else "✖ ") + why)

    def save_into(self, settings) -> None:
        settings.overlay_placements = self.editor.values()
