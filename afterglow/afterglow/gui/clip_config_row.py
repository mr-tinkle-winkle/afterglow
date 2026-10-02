"""
One row in the Clip Options list on the Settings page. Collapsible
("expandable list" per spec): collapsed shows a one-line summary
(name, length, hotkey), expanded shows the editable fields.

This widget only edits in-memory state + emits signals; SettingsPage is
responsible for persisting changes to the DB via clips.py, and for
deciding when to actually save (see SettingsPage for the save strategy).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QLineEdit,
    QToolButton, QFrame,
)

from ..hotkeys import ComboError, parse_combo
from .hotkey_record_dialog import HotkeyRecordDialog
from .custom_line_edit import CustomLineEdit
from .collapse_toggle_button import CollapseToggleButton, _ANIM_DURATION_MS as _TOGGLE_ANIM_MS
from .custom_spinbox import CustomSpinBox
from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .overlay_options import OverlayOptionsDialog
from .. import overlay_support
from .themed_dialogs import get_open_file_name
from .themed_frame import ThemedFrame


class ClipConfigRow(ThemedFrame):
    changed = Signal()          # any field edited (name/length/sound/hotkey)
    delete_requested = Signal()

    def __init__(self, clip_config_id: int | None, name: str, length_seconds: int,
                 sound_path: str | None, hotkey: str | None, parent=None, overlay: dict | None = None,
                 indicator: dict | None = None, indicator_style_provider=None):
        super().__init__(parent)
        ov = overlay or {}
        ind = indicator or {}
        # clip indicator ("the clapper"): colours per part, a custom icon, a clap sound
        self._indicator = {
            "indicator_colors": dict(ind.get("indicator_colors") or {}),
            "indicator_icon_path": ind.get("indicator_icon_path") or "",
            "indicator_clap_sound": ind.get("indicator_clap_sound") or "",
        }
        # a callable returning the global indicator style as it is in Settings right now (for the preview)
        self.indicator_style_provider = indicator_style_provider
        self._overlay = {
            "overlay_pieces": list(ov.get("overlay_pieces", ["keyboard", "mouse"])),
            "overlay_visible_default": bool(ov.get("overlay_visible_default", True)),
            "overlay_offset_ms": float(ov.get("overlay_offset_ms", 0.0)),
            "overlay_placements": dict(ov.get("overlay_placements", {})),
        }
        self.clip_config_id = clip_config_id  # None for a not-yet-saved new row

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        # ---- collapsible header ----
        header = QHBoxLayout()
        self.toggle_btn = CollapseToggleButton(expanded=True)
        self.toggle_btn.clicked.connect(self._on_toggle)
        header.addWidget(self.toggle_btn)

        self.summary_label = QLabel()
        header.addWidget(self.summary_label, stretch=1)

        self.delete_btn = CustomButton("Delete")
        self.delete_btn.clicked.connect(self.delete_requested.emit)
        header.addWidget(self.delete_btn)

        outer.addLayout(header)

        # ---- expandable body ----
        self.body = QWidget()
        form = QFormLayout(self.body)

        self.name_edit = CustomLineEdit(name)
        self.name_edit.textChanged.connect(self._on_any_change)
        form.addRow("Name:", self.name_edit)

        self.length_spin = CustomSpinBox()
        self.length_spin.setRange(1, 3600)
        self.length_spin.setSuffix(" sec")
        self.length_spin.setValue(length_seconds)
        self.length_spin.valueChanged.connect(self._on_any_change)
        form.addRow("Length:", self.length_spin)

        sound_row = QHBoxLayout()
        self.sound_edit = CustomLineEdit(sound_path or "")
        self.sound_edit.setPlaceholderText("(use default sound)")
        self.sound_edit.textChanged.connect(self._on_any_change)
        sound_browse = CustomButton("Browse...")
        sound_browse.clicked.connect(self._browse_sound)
        sound_row.addWidget(self.sound_edit)
        sound_row.addWidget(sound_browse)
        form.addRow("Sound:", sound_row)

        hotkey_row = QHBoxLayout()
        self.hotkey_edit = CustomLineEdit(hotkey or "")
        self.hotkey_edit.setReadOnly(True)
        self.hotkey_edit.setPlaceholderText("(no hotkey set)")
        record_btn = CustomButton("Record...")
        record_btn.clicked.connect(self._record_hotkey)
        self.clear_hotkey_btn = CustomButton()
        self.clear_hotkey_btn.setText("\u2715")  # "✕"
        self.clear_hotkey_btn.setToolTip("Clear hotkey")
        self.clear_hotkey_btn.clicked.connect(self._clear_hotkey)
        hotkey_row.addWidget(self.hotkey_edit)
        hotkey_row.addWidget(record_btn)
        hotkey_row.addWidget(self.clear_hotkey_btn)
        form.addRow("Hotkey:", hotkey_row)

        # Input overlay (Puppetry): capture a keyboard/mouse/controller
        # overlay with each clip of this type. The availability reason
        # (Puppetry missing, Layered Replay Buffer off, ...) sits beside
        # the toggle; "..." opens the pieces/timing/placement options.
        overlay_row = QHBoxLayout()
        self.overlay_check = CustomCheckBox("Capture")
        self.overlay_check.setChecked(bool((overlay or {}).get("overlay_enabled", False)))
        self.overlay_check.toggled.connect(self._on_overlay_toggled)
        self.overlay_options_btn = CustomButton("\u2026")
        self.overlay_options_btn.setToolTip("Input overlay options: pieces, show by default, timing offset, placement")
        self.overlay_options_btn.clicked.connect(self._edit_overlay_options)
        self.overlay_reason_label = QLabel()
        self.overlay_reason_label.setWordWrap(True)
        overlay_row.addWidget(self.overlay_check)
        overlay_row.addWidget(self.overlay_options_btn)
        overlay_row.addWidget(self.overlay_reason_label, stretch=1)
        form.addRow("Input overlay:", overlay_row)
        self._refresh_overlay_reason()

        # Clip indicator: this clip type's colours / icon / clap sound ("..." opens the dialog).
        indicator_row = QHBoxLayout()
        self.indicator_btn = CustomButton("\u2026")
        self.indicator_btn.setToolTip("Clip indicator options: colours, icon and clap sound for this clip option")
        self.indicator_btn.clicked.connect(self._edit_indicator)
        self.indicator_label = QLabel()
        indicator_row.addWidget(self.indicator_btn)
        indicator_row.addWidget(self.indicator_label, stretch=1)
        form.addRow("Indicator:", indicator_row)
        self._refresh_indicator_label()

        outer.addWidget(self.body)
        self._update_summary()

    # ------------------------------------------------------------ behavior

    def _on_toggle(self) -> None:
        expanded = self.toggle_btn.isChecked()
        self._animate_body(expanded)

    def _animate_body(self, expanded: bool) -> None:
        """Glides the body open/closed in sync with the toggle button's
        own arrow rotation (same _TOGGLE_ANIM_MS duration and OutCubic
        easing) instead of the old instant setVisible(expanded)
        teleport. Animates maximumHeight rather than a raw resize --
        that's what lets the surrounding QVBoxLayout smoothly reflow
        everything below this row as the body grows/shrinks, frame by
        frame, rather than the layout just snapping in one jump."""
        natural_height = self.body.sizeHint().height()
        if expanded:
            self.body.setVisible(True)
            self.body.setMaximumHeight(0)
            start, end = 0, natural_height
        else:
            start, end = self.body.height(), 0

        anim = QPropertyAnimation(self.body, b"maximumHeight", self)
        anim.setDuration(_TOGGLE_ANIM_MS)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(start)
        anim.setEndValue(end)
        if expanded:
            # Lifts the cap back off entirely once open, so later
            # content changes (e.g. picking a longer sound file path)
            # aren't stuck capped at this one snapshot of the natural
            # height taken when it was opened.
            anim.finished.connect(lambda: self.body.setMaximumHeight(16777215))
        else:
            anim.finished.connect(lambda: self.body.setVisible(False))
        anim.start()
        self._body_anim = anim  # keep a reference so it isn't garbage-collected mid-animation

    def _on_any_change(self, *_args) -> None:
        self._update_summary()
        self.changed.emit()

    def _update_summary(self) -> None:
        hotkey = self.hotkey_edit.text() or "no hotkey"
        self.summary_label.setText(
            f"<b>{self.name_edit.text() or '(unnamed)'}</b>  "
            f"&nbsp;&middot;&nbsp; {self.length_spin.value()}s "
            f"&nbsp;&middot;&nbsp; {hotkey}"
        )

    def _browse_sound(self) -> None:
        path, _ = get_open_file_name(
            self, "Choose Sound File", "", "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*)"
        )
        if path:
            self.sound_edit.setText(path)

    def _record_hotkey(self) -> None:
        dialog = HotkeyRecordDialog(self, current_combo=self.hotkey_edit.text() or None)
        if dialog.exec() and dialog.result_combo:
            try:
                parse_combo(dialog.result_combo)  # validate before accepting
            except ComboError:
                return
            self.hotkey_edit.setText(dialog.result_combo)
            self._on_any_change()

    def _on_overlay_toggled(self, *_a) -> None:
        self._refresh_overlay_reason()
        self._on_any_change()

    def _refresh_overlay_reason(self) -> None:
        """Shows aio.available()'s reason beside the toggle when the overlay
        is on but can't work right now (checked only when it's on, so a
        user who doesn't use Puppetry never pays for the lookup)."""
        self.overlay_options_btn.setEnabled(self.overlay_check.isChecked())
        if not self.overlay_check.isChecked():
            self.overlay_reason_label.setText("")
            return
        ok, why = overlay_support.available()
        self.overlay_reason_label.setText("" if ok else f"\u26a0 Unavailable: {why}")

    def _edit_overlay_options(self) -> None:
        o = self._overlay
        dialog = OverlayOptionsDialog(o["overlay_pieces"], o["overlay_visible_default"], o["overlay_offset_ms"],
                                      o["overlay_placements"], self)
        if dialog.exec():
            self._overlay.update(dialog.result_values())
            self._on_any_change()

    def _refresh_indicator_label(self) -> None:
        i = self._indicator
        parts = []
        if i["indicator_colors"]:
            parts.append("custom colours")
        if i["indicator_icon_path"]:
            parts.append("icon")
        if i["indicator_clap_sound"]:
            parts.append("clap sound")
        self.indicator_label.setText(", ".join(parts) if parts else "default look")

    def _edit_indicator(self) -> None:
        from .clip_indicator_settings import ClipIndicatorDialog
        i = self._indicator
        dialog = ClipIndicatorDialog(i["indicator_colors"], i["indicator_icon_path"], i["indicator_clap_sound"],
                                     style_provider=self.indicator_style_provider, parent=self)
        if dialog.exec():
            self._indicator.update(dialog.result_values())
            self._refresh_indicator_label()
            self._on_any_change()

    def _clear_hotkey(self) -> None:
        if self.hotkey_edit.text():
            self.hotkey_edit.setText("")
            self._on_any_change()

    # ------------------------------------------------------------ data access

    def to_fields(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "length_seconds": self.length_spin.value(),
            "sound_path": self.sound_edit.text().strip() or None,
            "hotkey": self.hotkey_edit.text().strip() or None,
            "overlay_enabled": self.overlay_check.isChecked(),
            **self._overlay,
            **self._indicator,
        }
