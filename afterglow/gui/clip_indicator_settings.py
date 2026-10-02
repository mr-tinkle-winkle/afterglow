"""
Settings UI for the clip indicator ("the clapper"):

  ClipIndicatorGroup   the "Clip Indicator" group under Settings > Clipping: enable, style, position
                       (3x3 picker), padding, size, enter / exit animation (hover a row to preview it),
                       processing mode, the two circle colours, a live preview and a Test button that
                       plays the whole thing through the real helper on the real screen.
  ClipIndicatorDialog  the per-clip-type "Indicator" dialog (opened from a clip option's row): colours
                       for each part, a custom icon, a clap sound, with the same live preview.
  ColorSwatch          a clickable colour square that opens the themed colour picker.

Nothing here talks to the helper except the Test button (through indicator_client, which never
raises or blocks).
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import config as config_module
from .. import indicator_client
from ..indicator import (ANIMATION_LABELS, ENTER_ANIMATIONS, EXIT_ANIMATIONS, STYLES)
from ..indicator import draw
from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .custom_combo_box import CustomComboBox
from .custom_group_box import CustomGroupBox
from .custom_line_edit import CustomLineEdit
from .custom_spinbox import CustomSpinBox
from .indicator_preview import AnchorPicker, IndicatorPreview
from .rounded_rect import rounded_rect_path
from .theme import Theme, contrast_text
from .themed_dialogs import get_color, get_open_file_name

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.svg);;All Files (*)"
SOUND_FILTER = "Audio Files (*.wav *.mp3 *.ogg *.flac);;All Files (*)"
STYLE_LABELS = {"clapper": "Clapper", "hands": "Hands"}
PROCESSING_LABELS = {"circle": "Clapper leaves, a loading circle shows", "stay": "Clapper stays until it's done"}
SCREEN_LABELS = {"focused": "The screen with the focused window", "primary": "The primary screen"}


class ColorSwatch(QWidget):
    """A colour square.  Click to choose a colour with the themed picker."""
    changed = Signal(str)

    def __init__(self, color: str = "#ffffff", title: str = "Choose Color", parent=None):
        super().__init__(parent)
        self._color = QColor(color) if QColor(color).isValid() else QColor("#ffffff")
        self._title = title
        self._theme = Theme(config_module.load_readonly().appearance)
        self.setFixedSize(52, 26)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(title)

    def color(self) -> str:
        return self._color.name()

    def set_color(self, color: str) -> None:
        c = QColor(color)
        if c.isValid() and c != self._color:
            self._color = c
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            picked = get_color(self._color, self.window(), self._title)
            if picked.isValid() and picked != self._color:
                self._color = picked
                self.update()
                self.changed.emit(picked.name())

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = rounded_rect_path(r, 6)
        p.fillPath(path, self._color)
        p.setPen(QPen(self._theme.accent(), 1.5))
        p.drawPath(path)
        p.end()


def _combo(items: "list[tuple[str, str]]", current: str) -> CustomComboBox:
    box = CustomComboBox()
    for value, label in items:
        box.addItem(label, value)
    i = box.findData(current)
    box.setCurrentIndex(max(0, i))
    return box


def _animation_items(names) -> "list[tuple[str, str]]":
    return [(n, ANIMATION_LABELS.get(n, n.title())) for n in names]


class ClipIndicatorGroup(CustomGroupBox):
    """Edits ``config.ClipIndicatorSettings``; SettingsPage calls ``save_into`` on Save."""
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__("Clip Indicator", parent)
        ci = settings.clip_indicator
        self._appearance = config_module.load_readonly().appearance
        root = self.make_layout(QVBoxLayout)

        self.enabled_check = CustomCheckBox("Show a clapper on screen when a clip is captured")
        self.enabled_check.setChecked(bool(ci.enabled))
        self.enabled_check.setToolTip(
            "A movie clapper slides in when a clip hotkey is pressed, claps when OBS has saved the clip, "
            "then shows a loading circle until the clip is in the library.")
        root.addWidget(self.enabled_check)

        self.preview = IndicatorPreview(with_overlay=True)
        self.preview.setFixedSize(480, 270)
        root.addWidget(self.preview, alignment=Qt.AlignLeft)
        self.preview_note = QLabel("Preview on a 1280×720 screen, drawn to scale. "
                                   "Hover an animation in the lists below to see it here.")
        self.preview_note.setWordWrap(True)
        root.addWidget(self.preview_note)

        form = QFormLayout()
        root.addLayout(form)

        self.style_combo = _combo([(s, STYLE_LABELS[s]) for s in STYLES], ci.style)
        form.addRow("Style:", self.style_combo)

        self.anchor_picker = AnchorPicker(ci.anchor)
        form.addRow("Position:", self.anchor_picker)

        self.pad_x_spin = CustomSpinBox()
        self.pad_x_spin.setRange(0, 2000)
        self.pad_x_spin.setSuffix(" px")
        self.pad_x_spin.setValue(int(ci.padding_x))
        form.addRow("Padding X:", self.pad_x_spin)
        self.pad_y_spin = CustomSpinBox()
        self.pad_y_spin.setRange(0, 2000)
        self.pad_y_spin.setSuffix(" px")
        self.pad_y_spin.setValue(int(ci.padding_y))
        form.addRow("Padding Y:", self.pad_y_spin)

        self.size_spin = CustomSpinBox()
        self.size_spin.setRange(24, 512)
        self.size_spin.setSuffix(" px")
        self.size_spin.setValue(int(ci.size))
        self.size_spin.setToolTip("The clapper's width")
        form.addRow("Size:", self.size_spin)

        self.enter_combo = _combo(_animation_items(ENTER_ANIMATIONS), ci.enter_animation)
        form.addRow("Enter animation:", self.enter_combo)
        self._auto_faded: "dict[str, str]" = {}      # combo -> the animation it had before the centre switched it to fade
        self.exit_combo = _combo(_animation_items(EXIT_ANIMATIONS), ci.exit_animation)
        form.addRow("Exit animation:", self.exit_combo)

        self.processing_combo = _combo(list(PROCESSING_LABELS.items()), ci.processing)
        form.addRow("While processing:", self.processing_combo)

        self.circle_swatch = ColorSwatch(ci.circle_color, "Loading circle: processing the clip")
        form.addRow("Circle (processing):", self._with_reset(self.circle_swatch, draw.DEFAULT_CIRCLE_COLOR))
        self.overlay_swatch = ColorSwatch(ci.overlay_circle_color, "Loading circle: rendering the input overlay")
        form.addRow("Circle (input overlay):", self._with_reset(self.overlay_swatch, draw.DEFAULT_OVERLAY_CIRCLE_COLOR))

        self.opacity_spin = CustomSpinBox()
        self.opacity_spin.setRange(5, 100)
        self.opacity_spin.setSuffix(" %")
        self.opacity_spin.setValue(int(round(ci.circle_opacity * 100)))
        form.addRow("Circle opacity:", self.opacity_spin)

        self.clapper_opacity_spin = CustomSpinBox()
        self.clapper_opacity_spin.setRange(5, 100)
        self.clapper_opacity_spin.setSuffix(" %")
        self.clapper_opacity_spin.setValue(int(round(ci.clapper_opacity * 100)))
        self.clapper_opacity_spin.setToolTip("How see-through the clapper (or the hands) is")
        form.addRow("Clapper opacity:", self.clapper_opacity_spin)

        self.pulse_check = CustomCheckBox("Pulse the ring each time a stage completes")
        self.pulse_check.setChecked(bool(ci.ring_pulse))
        form.addRow("", self.pulse_check)

        sound_row = QWidget()
        sl = QHBoxLayout(sound_row)
        sl.setContentsMargins(0, 0, 0, 0)
        self.clap_sound_edit = CustomLineEdit(ci.clap_sound or "")
        self.clap_sound_edit.setPlaceholderText("(the clip's usual sound)")
        self.clap_sound_browse = CustomButton("Browse...")
        self.clap_sound_browse.clicked.connect(self._browse_clap_sound)
        self.clap_sound_clear = CustomButton("✕")
        self.clap_sound_clear.setToolTip("Clear")
        self.clap_sound_clear.clicked.connect(lambda: self.clap_sound_edit.setText(""))
        self.clap_sound_play = CustomButton("Play")
        self.clap_sound_play.clicked.connect(self._play_clap_sound)
        for w in (self.clap_sound_edit,):
            sl.addWidget(w, stretch=1)
        for w in (self.clap_sound_browse, self.clap_sound_clear, self.clap_sound_play):
            sl.addWidget(w)
        self.clap_sound_edit.setToolTip("Played when OBS has saved the clip; replaces the clip's usual sound only "
                                        "while the indicator is enabled. A clip type's own clap sound wins over this one.")
        form.addRow("Clap sound:", sound_row)

        self.screen_combo = _combo(list(SCREEN_LABELS.items()), ci.screen)
        form.addRow("Screen:", self.screen_combo)

        test_row = QHBoxLayout()
        self.test_btn = CustomButton("Test on screen")
        self.test_btn.setToolTip("Plays the whole sequence on your real screen through the indicator helper, "
                                 "with the settings as they are now (no need to save first).")
        self.test_btn.clicked.connect(self.run_test)
        self.test_status = QLabel("")
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_status, stretch=1)
        form.addRow("", test_row)

        # live preview follows every control; hovering an animation row plays that one
        for sig in (self.style_combo.currentIndexChanged, self.anchor_picker.changed, self.pad_x_spin.valueChanged,
                    self.pad_y_spin.valueChanged, self.size_spin.valueChanged, self.enter_combo.currentIndexChanged,
                    self.exit_combo.currentIndexChanged, self.processing_combo.currentIndexChanged,
                    self.circle_swatch.changed, self.overlay_swatch.changed, self.opacity_spin.valueChanged,
                    self.clapper_opacity_spin.valueChanged, self.pulse_check.toggled):
            sig.connect(self._refresh)
        self.enter_combo.itemHovered.connect(lambda i: self.preview.set_overrides(enter=self.enter_combo.itemData(i)))
        self.exit_combo.itemHovered.connect(lambda i: self.preview.set_overrides(exit=self.exit_combo.itemData(i)))
        self.enter_combo.popupHidden.connect(self.preview.clear_overrides)
        self.exit_combo.popupHidden.connect(self.preview.clear_overrides)
        self.anchor_picker.changed.connect(self._sync_padding_enabled)
        self.anchor_picker.changed.connect(self._sync_centre_animations)
        self.clap_sound_edit.textChanged.connect(lambda *_: self.changed.emit())
        self.enabled_check.toggled.connect(self._sync_enabled)
        self.enabled_check.toggled.connect(lambda *_: self.changed.emit())
        self._sync_padding_enabled()
        self._sync_enabled()
        self._sync_centre_animations()
        self._refresh()

    # ------------------------------------------------------------ helpers

    def _with_reset(self, swatch: ColorSwatch, default: str) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(swatch)
        reset = CustomButton("Default")
        reset.setToolTip("Back to the default colour")
        reset.clicked.connect(lambda: (swatch.set_color(default), swatch.changed.emit(default)))
        row.addWidget(reset)
        row.addStretch(1)
        return w

    def _sync_centre_animations(self, *_a) -> None:
        """The centre has no screen edge to slide from, so choosing it from the slide defaults switches
        enter / exit to fade; moving away from the centre restores what was there (unless it was
        changed by hand in between)."""
        centre = self.anchor_picker.anchor() == "center"
        for key, combo in (("enter", self.enter_combo), ("exit", self.exit_combo)):
            cur = combo.currentData()
            if centre:
                if cur == "slide" and key not in self._auto_faded:
                    self._auto_faded[key] = cur
                    combo.setCurrentIndex(max(0, combo.findData("fade")))
            elif key in self._auto_faded:
                before = self._auto_faded.pop(key)
                if cur == "fade":
                    combo.setCurrentIndex(max(0, combo.findData(before)))

    def _sync_padding_enabled(self, *_a) -> None:
        a = self.anchor_picker.anchor()
        self.pad_x_spin.setEnabled(a not in ("top", "bottom", "center"))     # centred horizontally
        self.pad_y_spin.setEnabled(a not in ("left", "right", "center"))     # centred vertically
        self.pad_x_spin.setToolTip("" if self.pad_x_spin.isEnabled() else "Unused: the clapper is centred horizontally here.")
        self.pad_y_spin.setToolTip("" if self.pad_y_spin.isEnabled() else "Unused: the clapper is centred vertically here.")

    def _sync_enabled(self, *_a) -> None:
        on = self.enabled_check.isChecked()
        for w in (self.style_combo, self.anchor_picker, self.size_spin, self.enter_combo, self.exit_combo,
                  self.processing_combo, self.circle_swatch, self.overlay_swatch, self.opacity_spin,
                  self.clapper_opacity_spin, self.pulse_check, self.clap_sound_edit, self.clap_sound_browse,
                  self.clap_sound_clear, self.clap_sound_play, self.screen_combo, self.test_btn):
            w.setEnabled(on)
        if on:
            self._sync_padding_enabled()
        else:
            self.pad_x_spin.setEnabled(False)
            self.pad_y_spin.setEnabled(False)

    def _refresh(self, *_a) -> None:
        self.preview.set_style(self.style_dict())
        self.changed.emit()

    def _browse_clap_sound(self) -> None:
        path, _ = get_open_file_name(self, "Choose Clap Sound", self.clap_sound_edit.text(), SOUND_FILTER)
        if path:
            self.clap_sound_edit.setText(path)

    def _play_clap_sound(self) -> None:
        from ..clips import play_sound
        play_sound(self.clap_sound_edit.text().strip())

    # ------------------------------------------------------------ data

    def style_dict(self) -> dict:
        """The settings as they are in the widgets right now, in the shape of a `start` event's style."""
        return {
            "style": self.style_combo.currentData(),
            "colors": draw.afterglow_defaults(self._appearance), "icon": "",
            "anchor": self.anchor_picker.anchor(),
            "padding_x": self.pad_x_spin.value(), "padding_y": self.pad_y_spin.value(),
            "size": self.size_spin.value(),
            "enter": self.enter_combo.currentData(), "exit": self.exit_combo.currentData(),
            "mode": self.processing_combo.currentData(),
            "circle_color": self.circle_swatch.color(), "overlay_circle_color": self.overlay_swatch.color(),
            "circle_opacity": self.opacity_spin.value() / 100.0,
            "opacity": self.clapper_opacity_spin.value() / 100.0,
            "pulse": self.pulse_check.isChecked(),
            "screen": self.screen_combo.currentData(), "screen_hint": None,
        }

    def save_into(self, settings) -> None:
        ci = settings.clip_indicator
        d = self.style_dict()
        ci.enabled = self.enabled_check.isChecked()
        ci.style = d["style"]
        ci.anchor = d["anchor"]
        ci.padding_x, ci.padding_y, ci.size = d["padding_x"], d["padding_y"], d["size"]
        ci.enter_animation, ci.exit_animation = d["enter"], d["exit"]
        ci.processing = d["mode"]
        ci.circle_color, ci.overlay_circle_color = d["circle_color"], d["overlay_circle_color"]
        ci.circle_opacity = d["circle_opacity"]
        ci.clapper_opacity = d["opacity"]
        ci.ring_pulse = d["pulse"]
        ci.clap_sound = self.clap_sound_edit.text().strip()
        ci.screen = d["screen"]

    # ------------------------------------------------------------ the Test button

    TEST_STEPS = ((0.9, "clap"), (0.5, "processing"), (1.5, "overlay"), (1.5, "overlay_done"))

    def run_test(self) -> None:
        """start -> clap -> processing -> overlay -> overlay_done through the real helper (on its own
        thread: looking up the focused screen runs a compositor tool)."""
        style = self.style_dict()
        sound = self.clap_sound_edit.text().strip()
        cid = indicator_client.new_id()
        send = indicator_client.send_raw
        steps = self.TEST_STEPS

        def run():
            if style["screen"] == "focused":
                try:
                    from ..indicator.focus import focused_window_center
                    style["screen_hint"] = focused_window_center()
                except Exception:  # noqa: BLE001 -- the primary screen then
                    style["screen_hint"] = None
            send({"id": cid, "event": "start", "style": style})
            for delay, event in steps:
                time.sleep(delay)
                send({"id": cid, "event": event})
                if event == "clap" and sound:
                    try:
                        from ..clips import play_sound
                        play_sound(sound)
                    except Exception:  # noqa: BLE001 -- the test is visual first
                        pass
        threading.Thread(target=run, name="afterglow-indicator-test", daemon=True).start()
        self.test_status.setText("Sent to the indicator.")


class ClipIndicatorDialog(QDialog):
    """Per-clip-type indicator options: part colours, a custom icon, a clap sound."""

    def __init__(self, colors: dict, icon_path: str, clap_sound: str, style_provider=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clip Indicator")
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # the dialog paints its own background, so its labels need a text colour that reads on it
        # (a palette, not a stylesheet: children inherit it and nothing else changes)
        pal = self.palette()
        text = contrast_text(self._theme.library_background())
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            pal.setColor(role, text)
        self.setPalette(pal)
        self._style_provider = style_provider
        self._defaults = draw.afterglow_defaults(appearance)
        self._colors: dict[str, str] = {k: v for k, v in (colors or {}).items() if QColor(str(v)).isValid()}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        self.preview = IndicatorPreview(with_overlay=False)
        self.preview.setFixedWidth(400)
        self.preview.setFixedHeight(int(400 * 720 / 1280))
        layout.addWidget(self.preview, alignment=Qt.AlignHCenter)

        self.swatches: dict[str, ColorSwatch] = {}
        for title, keys in (("Clapper colours", draw.CLAPPER_COLOR_KEYS),
                            ("Hands colours (the Hands style)", draw.HANDS_COLOR_KEYS)):
            layout.addWidget(QLabel(f"<b>{title}</b>"))
            form = QFormLayout()
            layout.addLayout(form)
            for key in keys:
                sw = ColorSwatch(self._colors.get(key) or self._defaults[key], draw.COLOR_LABELS[key])
                sw.changed.connect(lambda c, k=key: self._set_color(k, c))
                reset = CustomButton("Default")
                reset.clicked.connect(lambda _=False, k=key: self._reset_color(k))
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.addWidget(sw)
                rl.addWidget(reset)
                rl.addStretch(1)
                form.addRow(f"{draw.COLOR_LABELS[key]}:", row)
                self.swatches[key] = sw

        layout.addWidget(QLabel("<b>Icon and sound</b>"))
        form2 = QFormLayout()
        layout.addLayout(form2)
        icon_row, self.icon_edit = self._file_row(icon_path, "(no icon)", IMAGE_FILTER, "Choose Icon Image")
        form2.addRow("Icon:", icon_row)
        icon_hint = QLabel("Drawn on the clapper's board, or on the back of the glove.")
        icon_hint.setWordWrap(True)
        form2.addRow("", icon_hint)
        sound_row, self.sound_edit = self._file_row(clap_sound, "(the clip's usual sound)", SOUND_FILTER, "Choose Clap Sound")
        form2.addRow("Clap sound:", sound_row)
        sound_hint = QLabel("Played when OBS has saved the clip, instead of the clip's usual sound. "
                            "Only used while the clip indicator is enabled.")
        sound_hint.setWordWrap(True)
        form2.addRow("", sound_hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = CustomButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = CustomButton("OK")
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

        self.icon_edit.textChanged.connect(self._refresh_preview)
        self._refresh_preview()

    # ------------------------------------------------------------ pieces

    def _file_row(self, value: str, placeholder: str, filt: str, caption: str):
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        edit = CustomLineEdit(value or "")
        edit.setPlaceholderText(placeholder)
        browse = CustomButton("Browse...")
        browse.clicked.connect(lambda: self._browse(edit, caption, filt))
        clear = CustomButton("✕")
        clear.setToolTip("Clear")
        clear.clicked.connect(lambda: edit.setText(""))
        row.addWidget(edit, stretch=1)
        row.addWidget(browse)
        row.addWidget(clear)
        return row_widget, edit

    def _browse(self, edit, caption: str, filt: str) -> None:
        path, _ = get_open_file_name(self, caption, edit.text(), filt)
        if path:
            edit.setText(path)

    def _set_color(self, key: str, color: str) -> None:
        self._colors[key] = color
        self._refresh_preview()

    def _reset_color(self, key: str) -> None:
        self._colors.pop(key, None)
        self.swatches[key].set_color(self._defaults[key])
        self._refresh_preview()

    def _refresh_preview(self, *_a) -> None:
        base = {}
        if self._style_provider is not None:
            try:
                base = dict(self._style_provider() or {})
            except Exception:  # noqa: BLE001
                base = {}
        base["colors"] = {**self._defaults, **self._colors}
        base["icon"] = self.icon_edit.text().strip()
        base["screen_hint"] = None
        self.preview.set_style(base)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        a = self._appearance
        radius = a.rounded_corner_radius if a.rounded_corners_enabled else 16
        if radius:
            painter.fillPath(rounded_rect_path(rect, radius), self._theme.library_background())
        else:
            painter.fillRect(rect, self._theme.library_background())
        painter.end()

    # ------------------------------------------------------------ result

    def result_values(self) -> dict:
        return {
            "indicator_colors": dict(self._colors),
            "indicator_icon_path": self.icon_edit.text().strip(),
            "indicator_clap_sound": self.sound_edit.text().strip(),
        }
