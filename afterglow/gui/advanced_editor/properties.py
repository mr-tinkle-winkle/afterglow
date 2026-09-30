"""
The Advanced Editor's properties panel (right side): everything about
the selected segment(s) -- name, volume/mute, speed, fades, transform
and crop, zoom filter, the incoming transition, text styling, and
keyframes.

With several segments selected, the shown values come from the first
one and edits apply to all of them (name, speed and text stay
single-segment). Spin box edits are grouped into one undo step per
burst of changes (see _edit). Values refresh when the project changes,
except in the field being typed into.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QPlainTextEdit,
    QScrollArea, QVBoxLayout, QWidget,
)

from ... import config as config_module
from ...nle import ops
from ...nle.model import KIND_TEXT, MAX_SPEED, MIN_SPEED, Segment
from ...nle.render import eval_keyframes, zoom_factor
from ..custom_button import CustomButton
from ..custom_checkbox import CustomCheckBox
from ..custom_combo_style import combo_box_stylesheet
from ..custom_group_box import CustomGroupBox
from ..custom_line_edit import CustomLineEdit
from ..custom_spinbox import CustomDoubleSpinBox
from ..smooth_scroll_area import SmoothScrollArea
from ..theme import Theme
from .controller import EditorController
from .timeline import format_time

# "position" and "tail" are two-value keyframes: each key sets both X and Y.
COMPOSITE = {"position": ("x", "y"), "tail": ("tail_x", "tail_y")}
KEYFRAME_PROPS = [("position", "Position"), ("x", "Position X"), ("y", "Position Y"), ("scale", "Scale"), ("rotation", "Rotation"),
                  ("opacity", "Opacity"), ("volume", "Volume")]
TRANSITION_KINDS = [("None", None), ("Crossfade", "crossfade"), ("Blur / Focus", "blur"),
                    ("Slide", "slide"), ("Fade (wipe)", "fade")]
TARGETS = [("Both", "both"), ("Destination", "destination"), ("Original", "original")]
DIRECTIONS = [("Left", "left"), ("Right", "right"), ("Top", "top"), ("Bottom", "bottom")]
EDIT_GROUP_MS = 700


def _spin(lo, hi, step, decimals=2, suffix=""):
    s = CustomDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    s.setMinimumWidth(74)
    return s


class PropertiesPanel(QWidget):
    seek_requested = Signal(float)

    def __init__(self, controller: EditorController, parent=None):
        super().__init__(parent)
        self.ctl = controller
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self._label_qss = f"QLabel {{ color: {appearance.card_text_color}; }}"
        self._combo_qss = combo_box_stylesheet(appearance)
        self._ids: list[str] = []
        self._fields: dict = {}
        self._refreshing = False
        self._open_label: "str | None" = None
        self._end_timer = QTimer(self)
        self._end_timer.setSingleShot(True)
        self._end_timer.setInterval(EDIT_GROUP_MS)
        self._end_timer.timeout.connect(self._end_edit)
        controller.flush_hooks.append(self._end_edit)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        self.title = QLabel("Properties")
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSizeF(f.pointSizeF() * 1.15)
        self.title.setFont(f)
        self.title.setStyleSheet(self._label_qss)
        outer.addWidget(self.title)
        self.scroll = SmoothScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setAttribute(Qt.WA_TranslucentBackground, True)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, stretch=1)
        self._body = None
        controller.selection_changed.connect(self._rebuild)
        controller.changed.connect(self._refresh)
        controller.previewed.connect(self._refresh_keyframe_values)   # a canvas drag: just the numbers
        controller.playhead_changed.connect(lambda _t: self._refresh_keyframe_values())
        self._rebuild()

    # ================================================================ edit grouping
    def _edit(self, label: str, fn) -> None:
        if self._refreshing or not self.ctl.has_project:
            return
        if self._open_label != label:
            self._end_edit()
            self.ctl.begin(label)
            self._open_label = label
        self.ctl.live(fn)
        self._end_timer.start()

    def _end_edit(self) -> None:
        self._end_timer.stop()
        if self._open_label is not None:
            self._open_label = None
            self.ctl.end()

    def _once(self, label: str, fn) -> None:
        self._end_edit()
        self.ctl.perform(label, fn)

    # ================================================================ building
    def _segments(self) -> list[Segment]:
        return self.ctl.selected_segments()

    def _rebuild(self) -> None:
        self._end_edit()
        segs = self._segments()
        self._ids = [s.id for s in segs]
        self._fields = {}
        body = QWidget()
        body.setAttribute(Qt.WA_TranslucentBackground, True)
        lay = QVBoxLayout(body)
        lay.setContentsMargins(0, 0, 2, 0)
        lay.setSpacing(8)
        if not segs:
            self.title.setText("Properties")
            hint = QLabel("Select a segment on the timeline to edit it.\n\n"
                          "S split · C combine · L lock · M mute · V hide segment\n"
                          "H hide layer (click a track header to select it)\n"
                          "Ctrl+C / Ctrl+V / Ctrl+D · Del · Shift+Del ripple\n"
                          "Space play · , . frame step · N snapping\n"
                          "Wheel scrolls tracks · Alt+wheel scrolls time\n"
                          "Shift+wheel steps frames · Ctrl+wheel zooms · middle-drag pans")
            hint.setWordWrap(True)
            hint.setStyleSheet(self._label_qss)
            lay.addWidget(hint)
            lay.addStretch(1)
            self._set_body(body)
            return
        first = segs[0]
        single = len(segs) == 1
        self.title.setText(first.name or "Segment" if single else f"{len(segs)} segments")
        any_audio = any(s.has_audio for s in segs)
        any_video = any(s.has_video for s in segs)
        text_part = next((p for p in first.parts if p.kind == KIND_TEXT and p.text), None) if single else None

        # ---- copy / paste colors and properties
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        cc = CustomButton("Copy Colors")
        cc.setToolTip("Copy this element's text, outline, bubble and shadow colors")
        cc.setEnabled(single and ops.has_colors(first))
        cc.clicked.connect(lambda: self._copy_style("colors"))
        pc = CustomButton("Paste Colors")
        pc.setEnabled("colors" in self.ctl.style_clipboard)
        pc.clicked.connect(lambda: self.ctl.paste_style("colors"))
        cp = CustomButton("Copy Properties")
        cp.setToolTip("Copy how this element looks and behaves (font, style, effects, fades, shadow, "
                      "scale/rotation...) -- not its text, position or timing")
        cp.setEnabled(single)
        cp.clicked.connect(lambda: self._copy_style("properties"))
        pp = CustomButton("Paste Properties")
        pp.setEnabled("properties" in self.ctl.style_clipboard)
        pp.clicked.connect(lambda: self.ctl.paste_style("properties"))
        for i, b_ in enumerate((cc, pc, cp, pp)):
            b_.setMinimumHeight(26)
            grid.addWidget(b_, i // 2, i % 2)
        lay.addLayout(grid)
        self._fields.update(paste_colors_btn=pc, paste_props_btn=pp)

        # ---- clip
        g, form = self._group(lay, "Clip")
        if single:
            name = CustomLineEdit(first.name)
            name.editingFinished.connect(lambda w=name: self._set_name(w.text()))
            form.addRow(self._lbl("Name"), name)
            self._fields["name"] = name
        info = QLabel()
        info.setStyleSheet(self._label_qss)
        form.addRow(self._lbl("Time"), info)
        self._fields["info"] = info
        if single:
            speed = _spin(MIN_SPEED, MAX_SPEED, 0.1, 2, "x")
            speed.valueChanged.connect(lambda v: self._edit("Speed", lambda p: ops.set_speed(p, self._ids[0], v)))
            form.addRow(self._lbl("Speed"), speed)
            self._fields["speed"] = speed
        fade_in = _spin(0, 3600, 0.1, 2, " s")
        fade_in.valueChanged.connect(lambda v: self._edit("Fade in", lambda p: ops.set_fades(p, self._ids, fade_in=v)))
        fade_out = _spin(0, 3600, 0.1, 2, " s")
        fade_out.valueChanged.connect(lambda v: self._edit("Fade out", lambda p: ops.set_fades(p, self._ids, fade_out=v)))
        form.addRow(self._lbl("Fade in"), fade_in)
        form.addRow(self._lbl("Fade out"), fade_out)
        self._fields.update(fade_in=fade_in, fade_out=fade_out)

        # ---- audio
        if any_audio:
            g, form = self._group(lay, "Audio")
            vol = _spin(0, 200, 5, 0, " %")
            vol.valueChanged.connect(lambda v: self._edit("Volume", self.ctl.animated_fn(self._ids, "volume", v / 100)))
            form.addRow(self._lbl("Volume"), vol)
            mute = CustomCheckBox("Muted")
            mute.clicked.connect(lambda: self._once("Mute", lambda p: ops.toggle(p, self._ids, "muted")))
            form.addRow(self._lbl(""), mute)
            self._fields.update(volume=vol, muted=mute)
            if single and first.has_video and first.has_audio:
                det = CustomButton("Detach Audio")
                det.clicked.connect(self.ctl.detach_audio)
                form.addRow(self._lbl(""), det)

        # ---- transform / crop / zoom
        if any_video:
            g, form = self._group(lay, "Transform")
            x = _spin(-500, 500, 1, 1, " %")
            y = _spin(-500, 500, 1, 1, " %")
            sc = _spin(1, 2000, 5, 1, " %")
            rot = _spin(-360, 360, 1, 1, "°")
            op = _spin(0, 100, 5, 0, " %")
            x.valueChanged.connect(lambda v: self._edit("Position", self.ctl.animated_fn(self._ids, "x", v / 100)))
            y.valueChanged.connect(lambda v: self._edit("Position", self.ctl.animated_fn(self._ids, "y", v / 100)))
            sc.valueChanged.connect(lambda v: self._edit("Scale", self.ctl.animated_fn(self._ids, "scale", v / 100)))
            rot.valueChanged.connect(lambda v: self._edit("Rotation", self.ctl.animated_fn(self._ids, "rotation", v)))
            op.valueChanged.connect(lambda v: self._edit("Opacity", self.ctl.animated_fn(self._ids, "opacity", v / 100)))
            for lbl, w in (("X", x), ("Y", y), ("Scale", sc), ("Rotation", rot), ("Opacity", op)):
                form.addRow(self._lbl(lbl), w)
            reset = CustomButton("Reset Transform")
            reset.clicked.connect(self._reset_transform)
            form.addRow(self._lbl(""), reset)
            self._fields.update(x=x, y=y, scale=sc, rotation=rot, opacity=op)

            if not text_part:
                g, form = self._group(lay, "Crop")
                for key, lbl in (("crop_left", "Left"), ("crop_top", "Top"), ("crop_right", "Right"),
                                 ("crop_bottom", "Bottom")):
                    w = _spin(0, 95, 1, 1, " %")
                    w.valueChanged.connect(lambda v, k=key: self._edit(
                        "Crop", lambda p: ops.set_transform(p, self._ids, **{k: v / 100})))
                    form.addRow(self._lbl(lbl), w)
                    self._fields[key] = w

            g, form = self._group(lay, "Drop Shadow")
            sh_on = CustomCheckBox("Drop shadow")
            sh_on.clicked.connect(lambda: self._once("Drop shadow", lambda p: self._set_seg(p, shadow=sh_on.isChecked())))
            sh_col = CustomButton("Color")
            sh_col.clicked.connect(self._pick_shadow_color)
            sh_op = _spin(0, 100, 5, 0, " %")
            sh_op.valueChanged.connect(lambda v: self._edit("Shadow", lambda p: self._set_seg(p, shadow_opacity=v / 100)))
            sh_dist = _spin(0, 20, 0.1, 1, " %")
            sh_dist.setToolTip("Distance, as a percentage of the frame height")
            sh_dist.valueChanged.connect(lambda v: self._edit("Shadow", lambda p: self._set_seg(p, shadow_distance=v / 100)))
            sh_ang = _spin(-360, 360, 5, 0, "°")
            sh_ang.setToolTip("Direction the shadow falls (0 = right, 90 = down)")
            sh_ang.valueChanged.connect(lambda v: self._edit("Shadow", lambda p: self._set_seg(p, shadow_angle=v)))
            sh_blur = _spin(0, 100, 5, 0, " %")
            sh_blur.valueChanged.connect(lambda v: self._edit("Shadow", lambda p: self._set_seg(p, shadow_blur=v / 100)))
            form.addRow(self._lbl(""), sh_on)
            form.addRow(self._lbl("Color"), sh_col)
            form.addRow(self._lbl("Opacity"), sh_op)
            form.addRow(self._lbl("Distance"), sh_dist)
            form.addRow(self._lbl("Angle"), sh_ang)
            form.addRow(self._lbl("Softness"), sh_blur)
            self._fields.update(shadow=sh_on, shadow_color_btn=sh_col, shadow_opacity=sh_op, shadow_distance=sh_dist,
                                shadow_angle=sh_ang, shadow_blur=sh_blur)

            g, form = self._group(lay, "Zoom Filter")
            za = _spin(100, 500, 5, 0, " %")
            zi = _spin(0, 3600, 0.1, 2, " s")
            zo = _spin(0, 3600, 0.1, 2, " s")

            def zoom_edit(_v=None):
                self._edit("Zoom filter", lambda p: ops.set_zoom(p, self._ids, za.value() / 100, zi.value(), zo.value()))
            for w in (za, zi, zo):
                w.valueChanged.connect(zoom_edit)
            form.addRow(self._lbl("Amount"), za)
            form.addRow(self._lbl("Zoom in over"), zi)
            form.addRow(self._lbl("Zoom out over"), zo)
            self._fields.update(zoom_amount=za, zoom_in=zi, zoom_out=zo)

        # ---- transition
        g, form = self._group(lay, "Transition In")
        kind = self._combo(TRANSITION_KINDS)
        dur = _spin(0.05, 30, 0.1, 2, " s")
        target = self._combo(TARGETS)
        direction = self._combo(DIRECTIONS)

        def trans_edit(*_a):
            k = kind.currentData()
            self._once("Transition", lambda p: self._apply_transition(p, k, dur.value(), target.currentData(),
                                                                      direction.currentData()))
        kind.currentIndexChanged.connect(trans_edit)
        target.currentIndexChanged.connect(trans_edit)
        direction.currentIndexChanged.connect(trans_edit)
        dur.valueChanged.connect(lambda v: self._edit("Transition", lambda p: self._apply_transition(
            p, kind.currentData(), v, target.currentData(), direction.currentData())))
        form.addRow(self._lbl("Type"), kind)
        form.addRow(self._lbl("Duration"), dur)
        form.addRow(self._lbl("Moves"), target)
        form.addRow(self._lbl("From"), direction)
        note = QLabel("Blends from the segment ending exactly where this one starts on the same track.")
        note.setWordWrap(True)
        note.setStyleSheet(self._label_qss + "QLabel { font-size: 11px; }")
        form.addRow(note)
        self._fields.update(t_kind=kind, t_dur=dur, t_target=target, t_dir=direction)

        # ---- text
        if text_part is not None:
            g, form = self._group(lay, "Text")
            edit = QPlainTextEdit(text_part.text.text)
            edit.setFixedHeight(70)
            edit.setStyleSheet(
                f"QPlainTextEdit {{ background-color: {self._appearance.afterglow_color_card_background};"
                f" color: {self._appearance.card_text_color}; border: 1px solid {self._appearance.afterglow_color_accent};"
                f" border-radius: 6px; }}")
            edit.textChanged.connect(lambda: self._edit("Edit text", lambda p: self._set_text(p, text=edit.toPlainText())))
            font = self._font_combo(text_part.text.font_family)
            font.activated.connect(lambda _i, c=font: self._pick_font(c))
            size = _spin(0.5, 50, 0.5, 1, " %")
            size.valueChanged.connect(lambda v: self._edit("Text size", lambda p: self._set_text(p, font_size=v / 100)))
            color = CustomButton("Color")
            color.clicked.connect(lambda: self._pick_color("color"))
            ocolor = CustomButton("Outline")
            ocolor.clicked.connect(lambda: self._pick_color("outline_color"))
            owidth = _spin(0, 40, 0.5, 1, " px")
            owidth.valueChanged.connect(lambda v: self._edit("Outline", lambda p: self._set_text(p, outline_width=v)))
            bold = CustomCheckBox("Bold")
            bold.clicked.connect(lambda: self._once("Bold", lambda p: self._set_text(p, bold=bold.isChecked())))
            italic = CustomCheckBox("Italic")
            italic.clicked.connect(lambda: self._once("Italic", lambda p: self._set_text(p, italic=italic.isChecked())))
            form.addRow(edit)
            form.addRow(self._lbl("Font"), font)
            form.addRow(self._lbl("Size"), size)
            row = QHBoxLayout()
            row.addWidget(color)
            row.addWidget(ocolor)
            form.addRow(self._lbl("Colors"), row)
            form.addRow(self._lbl("Outline width"), owidth)
            row2 = QHBoxLayout()
            row2.addWidget(bold)
            row2.addWidget(italic)
            form.addRow(self._lbl("Style"), row2)

            g2, form2 = self._group(lay, "Text Transitions")
            tin = _spin(0, 600, 0.1, 2, " s")
            tin.setToolTip("Type the text in over this long at the start (0 = off)")
            tin.valueChanged.connect(lambda v: self._edit("Type in", lambda p: self._set_text(p, type_in=v)))
            tout = _spin(0, 600, 0.1, 2, " s")
            tout.setToolTip("Delete the text over this long at the end (0 = off)")
            tout.valueChanged.connect(lambda v: self._edit("Type out", lambda p: self._set_text(p, type_out=v)))
            cur = CustomCheckBox("Typing cursor ( | )")
            cur.clicked.connect(lambda: self._once("Typing cursor", lambda p: self._set_text(p, type_cursor=cur.isChecked())))
            din = _spin(0, 600, 0.1, 2, " s")
            din.setToolTip("Delay: fade the words in one by one (letters left to right) over this long (0 = off)")
            din.valueChanged.connect(lambda v: self._edit("Delay in", lambda p: self._set_text(p, delay_in=v)))
            dout = _spin(0, 600, 0.1, 2, " s")
            dout.setToolTip("Delay: fade the words out in reading order over this long at the end (0 = off)")
            dout.valueChanged.connect(lambda v: self._edit("Delay out", lambda p: self._set_text(p, delay_out=v)))
            form2.addRow(self._lbl("Type in"), tin)
            form2.addRow(self._lbl("Type out"), tout)
            form2.addRow(self._lbl(""), cur)
            form2.addRow(self._lbl("Delay in"), din)
            form2.addRow(self._lbl("Delay out"), dout)
            keyed = CustomCheckBox("Per-word timing")
            keyed.setToolTip("Time each word yourself: move the playhead to when a word is said and click it below")
            keyed.clicked.connect(lambda: self._set_word_keyed(keyed.isChecked()))
            form2.addRow(self._lbl(""), keyed)
            words_box = QWidget()
            from .flow_layout import FlowLayout
            from PySide6.QtWidgets import QSizePolicy
            FlowLayout(words_box)
            pol = words_box.sizePolicy()
            pol.setHeightForWidth(True)
            words_box.setSizePolicy(pol)
            words_note = QLabel("Click a word to make it appear at the playhead; right-click to clear. "
                                "Words without a key are spread evenly between their neighbours.")
            words_note.setWordWrap(True)
            words_note.setStyleSheet(self._label_qss + "QLabel { font-size: 11px; }")
            form2.addRow(words_box)
            form2.addRow(words_note)
            clear_keys = CustomButton("Clear Word Keys")
            clear_keys.clicked.connect(lambda: self._once("Clear word keys", lambda p: self._set_text(p, delay_word_times=[])))
            form2.addRow(self._lbl(""), clear_keys)
            self._fields.update(delay_keyed=keyed, words_box=words_box, words_note=words_note, clear_keys=clear_keys)

            g3, form3 = self._group(lay, "Bubble")
            bub = self._combo([("None", ""), ("Speech", "speech"), ("Thought", "thought")])
            bub.currentIndexChanged.connect(lambda *_: self._set_bubble(bub.currentData()))
            bfill = CustomButton("Fill")
            bfill.clicked.connect(lambda: self._pick_color("bubble_fill"))
            bline = CustomButton("Outline")
            bline.clicked.connect(lambda: self._pick_color("bubble_outline"))
            bwidth = _spin(0, 40, 0.5, 1, " px")
            bwidth.valueChanged.connect(lambda v: self._edit("Bubble outline", lambda p: self._set_text(p, bubble_outline_width=v)))
            form3.addRow(self._lbl("Type"), bub)
            from ...nle.render import BUBBLE_VARIANTS
            kind_now = text_part.text.bubble
            bvar = self._combo(BUBBLE_VARIANTS.get(kind_now, [("Neutral", "")]))
            bvar.setToolTip("The bubble's style")
            bvar.currentIndexChanged.connect(lambda *_: self._set_variant(bvar.currentData()))
            banim = CustomCheckBox("Animated")
            banim.setToolTip("Keep the style's effect moving (spikes flicker, dashes march, lines wiggle...)")
            banim.clicked.connect(lambda: self._once("Bubble animation", lambda p: self._set_text(
                p, bubble_animated=banim.isChecked())))
            form3.addRow(self._lbl("Style"), bvar)
            form3.addRow(self._lbl(""), banim)
            bspeed = _spin(0.1, 5, 0.1, 2, "x")
            bspeed.setToolTip("How fast the style's animation runs (1 = normal)")
            bspeed.valueChanged.connect(lambda v: self._edit("Bubble animation speed", lambda p: self._set_text(
                p, bubble_anim_speed=v)))
            form3.addRow(self._lbl("Animation speed"), bspeed)
            brow = QHBoxLayout()
            brow.addWidget(bfill)
            brow.addWidget(bline)
            form3.addRow(self._lbl("Colors"), brow)
            form3.addRow(self._lbl("Outline width"), bwidth)
            bft = _spin(0, 100, 5, 0, " %")
            bft.valueChanged.connect(lambda v: self._edit("Bubble transparency", lambda p: self._set_text(
                p, bubble_fill_transparency=v / 100)))
            bot = _spin(0, 100, 5, 0, " %")
            bot.valueChanged.connect(lambda v: self._edit("Bubble outline transparency", lambda p: self._set_text(
                p, bubble_outline_transparency=v / 100)))
            gin = _spin(0, 30, 0.05, 2, " s")
            gin.setToolTip("Grow: the bubble grows out of its tail tip into place (0 = off)")
            gin.valueChanged.connect(lambda v: self._edit("Grow in", lambda p: self._set_text(p, grow_in=v)))
            gout = _spin(0, 30, 0.05, 2, " s")
            gout.setToolTip("Grow out: the bubble shrinks back into its tail tip at the end (0 = off)")
            gout.valueChanged.connect(lambda v: self._edit("Grow out", lambda p: self._set_text(p, grow_out=v)))
            form3.addRow(self._lbl("Fill transparency"), bft)
            form3.addRow(self._lbl("Outline transparency"), bot)
            form3.addRow(self._lbl("Grow in"), gin)
            form3.addRow(self._lbl("Grow out"), gout)
            note = QLabel("Drag the orange diamond in the preview to aim the tail; it moves independently of "
                          "the bubble and can be keyframed (Keyframes > Bubble tail tip).")
            note.setWordWrap(True)
            note.setStyleSheet(self._label_qss + "QLabel { font-size: 11px; }")
            form3.addRow(note)
            for w_ in (bfill, bline, bwidth, bft, bot, gin, gout, bvar):
                w_.setEnabled(bool(text_part.text.bubble))
            self._fields.update(type_in=tin, type_out=tout, type_cursor=cur, bubble=bub, bubble_fill_btn=bfill,
                                bubble_outline_btn=bline, bubble_outline_width=bwidth, delay_in=din, delay_out=dout,
                                bubble_fill_transparency=bft, bubble_outline_transparency=bot, grow_in=gin,
                                grow_out=gout, bubble_variant=bvar, bubble_animated=banim,
                                bubble_anim_speed=bspeed)
            self._fields.update(text=edit, font=font, text_size=size, color_btn=color, outline_btn=ocolor,
                                outline_width=owidth, bold=bold, italic=italic)

        # ---- keyframes
        if single:
            g, form = self._group(lay, "Keyframes")
            is_bubble = text_part is not None and bool(text_part.text.bubble)
            items = [(label, key) for key, label in KEYFRAME_PROPS if key != "volume" or first.has_audio]
            if is_bubble:
                items.insert(1, ("Bubble tail tip", "tail"))
            prop = self._combo(items)
            prop.currentIndexChanged.connect(lambda *_: self._refresh_keyframes())
            add = CustomButton("Add at Playhead")
            add.clicked.connect(self._add_keyframe)
            row = QHBoxLayout()
            prevb = CustomButton("◀")
            prevb.setToolTip("Previous keyframe")
            prevb.clicked.connect(lambda: self._jump_keyframe(-1))
            nextb = CustomButton("▶")
            nextb.setToolTip("Next keyframe")
            nextb.clicked.connect(lambda: self._jump_keyframe(1))
            row.addWidget(prevb)
            row.addWidget(add, stretch=1)
            row.addWidget(nextb)
            form.addRow(self._lbl("Property"), prop)
            form.addRow(row)
            listw = QWidget()
            QVBoxLayout(listw).setContentsMargins(0, 0, 0, 0)
            form.addRow(listw)
            self._fields.update(kf_prop=prop, kf_list=listw)

        lay.addStretch(1)
        self._set_body(body)
        self._refresh()

    def _set_body(self, body: QWidget) -> None:
        body.setAutoFillBackground(False)
        body.setObjectName("propsBody")
        body.setStyleSheet("QWidget#propsBody { background: transparent; }")
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self.scroll.setWidget(body)
        self._body = body

    def _group(self, lay, title):
        g = CustomGroupBox(title)
        form = g.make_layout(QFormLayout)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        lay.addWidget(g)
        return g, form

    def _lbl(self, text):
        label = QLabel(text)
        label.setStyleSheet(self._label_qss)
        return label

    def _combo(self, items):
        c = QComboBox()
        c.setStyleSheet(self._combo_qss)
        # don't let the longest item widen the whole panel past its edge
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(8)
        for label, data in items:
            c.addItem(label, data)
        return c

    # ================================================================ refreshing values
    def _set(self, key, value) -> None:
        w = self._fields.get(key)
        if w is None or w.hasFocus():
            return
        w.blockSignals(True)
        if isinstance(w, CustomDoubleSpinBox):
            w.setValue(value)
        elif isinstance(w, QComboBox):
            i = w.findData(value)
            w.setCurrentIndex(max(0, i))
        elif isinstance(w, CustomCheckBox):
            w.setChecked(bool(value))
        elif isinstance(w, CustomLineEdit):
            if w.text() != value:
                w.setText(value)
        elif isinstance(w, QPlainTextEdit):
            if w.toPlainText() != value:
                w.setPlainText(value)
        w.blockSignals(False)

    def _refresh(self) -> None:
        segs = self._segments()
        if [s.id for s in segs] != self._ids:
            self._rebuild()
            return
        if not segs:
            return
        self._refreshing = True
        s = segs[0]
        p = self.ctl.project
        if "info" in self._fields:
            self._fields["info"].setText(f"{format_time(s.start, p.fps, True)} – {format_time(s.end, p.fps, True)}"
                                         f"  ({s.duration:.2f} s)")
        self._set("name", s.name)
        if s.parts:
            self._set("speed", s.parts[0].speed)
        self._set("fade_in", s.fade_in)
        self._set("fade_out", s.fade_out)
        self._set("muted", s.muted)
        tr = s.transform
        self._set("crop_left", tr.crop_left * 100)
        self._set("crop_top", tr.crop_top * 100)
        self._set("crop_right", tr.crop_right * 100)
        self._set("crop_bottom", tr.crop_bottom * 100)
        self._set("zoom_amount", s.zoom_amount * 100)
        self._set("shadow", s.shadow)
        self._set("shadow_opacity", s.shadow_opacity * 100)
        self._set("shadow_distance", s.shadow_distance * 100)
        self._set("shadow_angle", s.shadow_angle)
        self._set("shadow_blur", s.shadow_blur * 100)
        if "shadow_color_btn" in self._fields:
            self._fields["shadow_color_btn"].set_fill_color(s.shadow_color)
            for key in ("shadow_color_btn", "shadow_opacity", "shadow_distance", "shadow_angle", "shadow_blur"):
                self._fields[key].setEnabled(s.shadow)
        self._set("zoom_in", s.zoom_in)
        self._set("zoom_out", s.zoom_out)
        t = s.transition_in
        self._set("t_kind", t.kind if t else None)
        if not t:
            self._set("t_dur", 0.5)
        if t:
            self._set("t_dur", t.duration)
            self._set("t_target", t.target)
            self._set("t_dir", t.direction)
        for key in ("t_dur", "t_target", "t_dir"):
            w = self._fields.get(key)
            if w is not None:
                w.setEnabled(t is not None and (key == "t_dur" or t.kind in ("slide", "fade")))
        tp = next((pt for pt in s.parts if pt.kind == KIND_TEXT and pt.text), None)
        if tp is not None and "text" in self._fields:
            st = tp.text
            self._set("text", st.text)
            self._set("text_size", st.font_size * 100)
            self._set("outline_width", st.outline_width)
            self._set("bold", st.bold)
            self._set("italic", st.italic)
            self._fields["color_btn"].set_fill_color(st.color)
            self._set("type_in", st.type_in)
            self._set("type_out", st.type_out)
            self._set("type_cursor", st.type_cursor)
            self._set("bubble", st.bubble)
            self._set("bubble_outline_width", st.bubble_outline_width)
            self._set("delay_in", st.delay_in)
            self._set("delay_out", st.delay_out)
            self._set("delay_keyed", st.delay_keyed)
            self._refresh_word_chips(s, tp)
            self._set("bubble_fill_transparency", st.bubble_fill_transparency * 100)
            self._set("bubble_outline_transparency", st.bubble_outline_transparency * 100)
            self._set("grow_in", st.grow_in)
            self._set("grow_out", st.grow_out)
            from ...nle.render import valid_variant
            var = valid_variant(st.bubble, st.bubble_variant)
            self._set("bubble_variant", var)
            self._set("bubble_animated", st.bubble_animated)
            self._set("bubble_anim_speed", st.bubble_anim_speed)
            if "bubble_animated" in self._fields:
                # everything but the neutral speech bubble has something to animate
                can = bool(st.bubble) and (bool(var) or st.bubble == "thought")
                self._fields["bubble_animated"].setEnabled(can)
                self._fields["bubble_anim_speed"].setEnabled(can and st.bubble_animated)
            fc = self._fields.get("font")
            if fc is not None and fc.currentData() != st.font_family:
                i = fc.findData(st.font_family)
                if i >= 0:
                    fc.blockSignals(True)
                    fc.setCurrentIndex(i)
                    fc.blockSignals(False)
            self._fields["bubble_fill_btn"].set_fill_color(st.bubble_fill)
            self._fields["bubble_outline_btn"].set_fill_color(st.bubble_outline)
            self._fields["outline_btn"].set_fill_color(st.outline_color)
        self._refresh_keyframe_values()
        self._refreshing = False
        self._refresh_keyframes()

    def _refresh_keyframe_values(self) -> None:
        """Animated values (they depend on the playhead when keyframed)."""
        segs = self._segments()
        if not segs or [s.id for s in segs] != self._ids:
            return
        s = segs[0]
        was = self._refreshing
        self._refreshing = True
        local = self.ctl.local_time(s)
        kf = s.keyframes
        tr = s.transform
        self._set("x", eval_keyframes(kf.get("x"), local, tr.x) * 100)
        self._set("y", eval_keyframes(kf.get("y"), local, tr.y) * 100)
        self._set("scale", eval_keyframes(kf.get("scale"), local, tr.scale) * 100)
        self._set("rotation", eval_keyframes(kf.get("rotation"), local, tr.rotation))
        self._set("opacity", eval_keyframes(kf.get("opacity"), local, 1.0) * 100)
        self._set("volume", eval_keyframes(kf.get("volume"), local, s.volume) * 100)
        self._refreshing = was

    def _refresh_keyframes(self) -> None:
        listw = self._fields.get("kf_list")
        prop_c = self._fields.get("kf_prop")
        segs = self._segments()
        if listw is None or not segs:
            return
        lay = listw.layout()
        s = segs[0]
        prop = prop_c.currentData()
        comps = COMPOSITE.get(prop, (prop,))
        kfs = s.keyframes.get(comps[0], [])
        # Rebuild the rows only when what they show changed (this runs on
        # every project change, including each step of a timeline drag).
        sig = (s.id, prop, round(s.start, 6), tuple((c, round(k.t, 6), k.value, k.easing)
                                                    for c in comps for k in s.keyframes.get(c, [])))
        if sig == getattr(self, "_kf_sig", None) and lay.count():
            return
        self._kf_sig = sig
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            elif it.layout() is not None:
                sub = it.layout()
                while sub.count():
                    sw = sub.takeAt(0).widget()
                    if sw is not None:
                        sw.deleteLater()
        if not kfs:
            lab = self._lbl("No keyframes -- the value is constant.")
            lab.setWordWrap(True)
            lay.addWidget(lab)
            return
        for k in kfs:
            row = QHBoxLayout()
            t_btn = CustomButton(format_time(s.start + k.t, self.ctl.project.fps, True))
            t_btn.setToolTip("Move the playhead here")
            t_btn.clicked.connect(lambda _=False, tt=s.start + k.t: self.ctl.set_playhead(tt))
            if len(comps) == 2:
                other = eval_keyframes(s.keyframes.get(comps[1]), k.t, self._static_value(s, comps[1]))
                val = QLabel(f"{k.value * 100:.0f} %, {other * 100:.0f} %")
            else:
                val = QLabel(self._fmt_value(prop, k.value))
            val.setStyleSheet(self._label_qss)
            ease = self._combo([("Linear", "linear"), ("Ease", "ease"), ("Hold", "hold")])
            ease.setCurrentIndex(max(0, ease.findData(k.easing)))
            ease.currentIndexChanged.connect(lambda _i, kt=k.t, c=ease: self._once(
                "Keyframe easing", lambda p: self._set_easing(p, comps, kt, c.currentData())))
            rm = CustomButton("✕")
            rm.setToolTip("Delete keyframe")
            rm.clicked.connect(lambda _=False, kt=k.t: self._once(
                "Delete keyframe", lambda p: [ops.remove_keyframe(p, self._ids[0], c, kt) for c in comps]))
            row.addWidget(t_btn)
            row.addWidget(val, stretch=1)
            row.addWidget(ease)
            row.addWidget(rm)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(holder)

    @staticmethod
    def _fmt_value(prop, v):
        if prop in ("x", "y", "scale", "opacity", "volume"):
            return f"{v * 100:.0f} %"
        return f"{v:.1f}°"

    # ================================================================ actions
    def _set_name(self, name: str) -> None:
        if not self._ids:
            return
        sid = self._ids[0]

        def fn(p):
            _, s = p.find_segment(sid)
            if s is not None:
                s.name = name.strip()
        self._once("Rename", fn)

    def _reset_transform(self) -> None:
        ids = list(self._ids)

        def fn(p):
            ops.set_transform(p, ids, x=0, y=0, scale=1, rotation=0)
            for sid in ids:
                _, s = p.find_segment(sid)
                if s is not None and not s.locked:
                    for key in ("x", "y", "scale", "rotation", "opacity"):
                        s.keyframes.pop(key, None)
        self._once("Reset transform", fn)

    def _apply_transition(self, p, kind, duration, target, direction) -> None:
        from ...nle.model import Transition
        for sid in self._ids:
            _, s = p.find_segment(sid)
            if s is None or s.locked:
                continue
            s.transition_in = None if kind is None else Transition(
                kind=kind, duration=max(0.05, min(duration, s.duration)), target=target or "both",
                direction=direction or "left")

    def _set_text(self, p, **values) -> None:
        if not self._ids:
            return
        _, s = p.find_segment(self._ids[0])
        if s is None or s.locked:
            return
        for part in s.parts:
            if part.kind == KIND_TEXT and part.text is not None:
                for k, v in values.items():
                    setattr(part.text, k, v)
                if "text" in values:
                    s.name = values["text"].split("\n")[0][:40] or "Text"

    def _font_combo(self, current: str) -> QComboBox:
        """A plain dropdown of the bundled fonts (see gui/fonts.py), grouped by
        style, each shown in its own typeface. Back Issues comes first when
        it's installed; a font the text already uses that isn't in the list
        is kept at the top so it isn't silently replaced."""
        from ..fonts import DISPLAY_NAMES, FONT_CHOICES, installed_back_issues, load_bundled_fonts
        load_bundled_fonts()
        c = QComboBox()
        c.setStyleSheet(self._combo_qss)
        c.setEditable(False)
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(8)
        c.setMaxVisibleItems(26)

        def add(label, family):
            c.addItem(label, family)
            f = QFont(family)
            f.setPointSizeF(max(9.0, self.font().pointSizeF() * 1.05))
            c.setItemData(c.count() - 1, f, Qt.FontRole)
        known = {fam for _cat, fam in FONT_CHOICES}
        bi = installed_back_issues()
        if bi:
            known.add(bi)
        if current and current not in known:
            add(f"{current} (current)", current)
            c.insertSeparator(c.count())
        if bi:
            add(f"{bi} (comic lettering)", bi)
        last_cat = None
        for cat, fam in FONT_CHOICES:
            if cat != last_cat and c.count():
                c.insertSeparator(c.count())
            last_cat = cat
            add(f"{DISPLAY_NAMES.get(fam, fam)}  \u2014  {cat}", fam)
        i = c.findData(current)
        c.setCurrentIndex(max(0, i))
        c.setToolTip("Font")
        return c

    def _pick_font(self, combo: QComboBox) -> None:
        name = combo.currentData()
        if not name or self._refreshing:
            return
        segs = self._segments()
        tp = next((pt.text for pt in segs[0].parts if pt.kind == KIND_TEXT and pt.text), None) if segs else None
        if tp is not None and tp.font_family == name:
            return
        self._once("Font", lambda p: self._set_text(p, font_family=name))

    def _set_seg(self, p, **values) -> None:
        for sid in self._ids:
            _, s = p.find_segment(sid)
            if s is None or s.locked:
                continue
            for k, v in values.items():
                setattr(s, k, v)

    def _pick_shadow_color(self) -> None:
        segs = self._segments()
        if not segs:
            return
        c = QColorDialog.getColor(QColor(segs[0].shadow_color), self, "Shadow color")
        if c.isValid():
            self._once("Shadow color", lambda p: self._set_seg(p, shadow_color=c.name()))

    # ---- per-word Delay keyframes -----------------------------------------------
    def _set_word_keyed(self, on: bool) -> None:
        self._once("Per-word timing", lambda p: self._set_text(p, delay_keyed=on))

    def _refresh_word_chips(self, seg: Segment, part) -> None:
        box = self._fields.get("words_box")
        if box is None:
            return
        from ...nle.render import text_words, word_start_times
        st = part.text
        on = st.delay_keyed
        for k in ("words_box", "words_note", "clear_keys"):
            self._fields[k].setVisible(on)
        words = [st.text[f:f + n] for f, n in text_words(st.text)]
        keys = list(st.delay_word_times or [])[:len(words)]
        keys += [None] * (len(words) - len(keys))
        times = [t for t, _w in word_start_times(st, part.duration)] if on else []
        sig = (on, tuple(words), tuple(keys), tuple(round(t, 3) for t in times), round(seg.start + part.offset, 4))
        if sig == getattr(self, "_chips_sig", None) and box.layout().count() == (len(words) if on else 0):
            return
        self._chips_sig = sig
        lay = box.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget() is not None:
                it.widget().hide()
                it.widget().setParent(None)
                it.widget().deleteLater()
        if not on:
            return
        fps = self.ctl.project.fps if self.ctl.project else 30
        for i, word in enumerate(words):
            b = CustomButton(word)
            b.setMinimumHeight(26)
            keyed_ = keys[i] is not None
            if keyed_:
                b.set_fill_color(self._theme.turquoise())
            at = seg.start + part.offset + (keys[i] if keyed_ else times[i])
            b.setToolTip(("Keyed at " if keyed_ else "Auto: ") + format_time(at, fps, True)
                         + (" -- right-click to clear" if keyed_ else " -- click to key it at the playhead"))
            b.clicked.connect(lambda _=False, i=i: self._key_word(i))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _pos, i=i: self._key_word(i, clear=True))
            lay.addWidget(b)
        box.setMinimumHeight(lay.heightForWidth(max(box.width(), 240)))

    def _key_word(self, index: int, clear: bool = False) -> None:
        if not self._ids:
            return
        sid = self._ids[0]
        playhead = self.ctl.playhead

        def fn(p):
            _, s = p.find_segment(sid)
            if s is None or s.locked:
                return
            from ...nle.render import text_words
            for part in s.parts:
                st = part.text
                if part.kind != KIND_TEXT or st is None:
                    continue
                n = len(text_words(st.text))
                keys = list(st.delay_word_times or [])[:n]
                keys += [None] * (n - len(keys))
                if 0 <= index < n:
                    keys[index] = None if clear else max(0.0, min(part.duration, playhead - s.start - part.offset))
                st.delay_word_times = keys
                st.delay_keyed = True
        self._once("Clear word key" if clear else "Key word", fn)

    def _copy_style(self, what: str) -> None:
        if self.ctl.copy_style(what):
            key = "paste_colors_btn" if what == "colors" else "paste_props_btn"
            if key in self._fields:
                self._fields[key].setEnabled(True)

    def _set_variant(self, variant: str) -> None:
        if self._refreshing or not self._ids:
            return
        self._once("Bubble style", lambda p: self._set_text(p, bubble_variant=variant or ""))

    def _set_bubble(self, kind: str) -> None:
        if self._refreshing or not self._ids:
            return
        sid = self._ids[0]

        def fn(p):
            _, s = p.find_segment(sid)
            if s is None or s.locked:
                return
            for part in s.parts:
                st = part.text
                if part.kind != KIND_TEXT or st is None:
                    continue
                if kind and not st.bubble:
                    # Turning a bubble on: aim the tail just below-left of it,
                    # switch to dark comic lettering on the white bubble, and
                    # give it the default grow in/out.
                    from .controller import comic_font
                    st.tail_x, st.tail_y = -0.08, 0.2          # offset from the bubble
                    st.font_family = comic_font()
                    if st.grow_in == 0 and st.grow_out == 0:
                        st.grow_in, st.grow_out = (0.6, 0.45) if kind == "thought" else (0.35, 0.3)
                    if st.color.lower() in ("#ffffff", "#ffffffff"):
                        st.color = "#111111"
                        st.outline_width = 0.0
                if kind != st.bubble:
                    st.bubble_variant = ""        # styles differ per bubble kind
                st.bubble = kind or ""
        self._once("Bubble", fn)
        QTimer.singleShot(0, self._rebuild)      # the keyframe list gains/loses "Bubble tail tip"

    def _pick_color(self, attr: str) -> None:
        segs = self._segments()
        if not segs:
            return
        tp = next((pt for pt in segs[0].parts if pt.kind == KIND_TEXT and pt.text), None)
        if tp is None:
            return
        c = QColorDialog.getColor(QColor(getattr(tp.text, attr)), self, "Text color",
                                  QColorDialog.ShowAlphaChannel)
        if c.isValid():
            name = c.name(QColor.HexArgb) if c.alpha() < 255 else c.name()
            self._once("Text color", lambda p: self._set_text(p, **{attr: name}))

    @staticmethod
    def _static_value(s: Segment, prop: str) -> float:
        tr = s.transform
        tp = next((pt.text for pt in s.parts if pt.kind == KIND_TEXT and pt.text), None)
        defaults = {"x": tr.x, "y": tr.y, "scale": tr.scale, "rotation": tr.rotation, "opacity": 1.0,
                    "volume": s.volume, "tail_x": tp.tail_x if tp else 0.0, "tail_y": tp.tail_y if tp else 0.0}
        return defaults[prop]

    def _current_value(self, s: Segment, prop: str) -> float:
        return eval_keyframes(s.keyframes.get(prop), self.ctl.local_time(s), self._static_value(s, prop))

    def _set_easing(self, p, comps, kt, easing) -> None:
        _, s = p.find_segment(self._ids[0])
        if s is None:
            return
        for c in comps:
            for k in s.keyframes.get(c, []):
                if abs(k.t - kt) < 1e-3:
                    ops.set_keyframe(p, s.id, c, k.t, k.value, easing)

    def _add_keyframe(self) -> None:
        segs = self._segments()
        prop_c = self._fields.get("kf_prop")
        if not segs or prop_c is None:
            return
        s = segs[0]
        if not s.covers(self.ctl.playhead) and abs(self.ctl.playhead - s.end) > 1e-6:
            self.ctl.error.emit("Move the playhead over the segment to add a keyframe.")
            return
        prop = prop_c.currentData()
        comps = COMPOSITE.get(prop, (prop,))
        values = {c: self._current_value(s, c) for c in comps}
        local = self.ctl.local_time(s)
        self._once("Add keyframe", lambda p: [ops.set_keyframe(p, s.id, c, local, v) for c, v in values.items()])

    def _jump_keyframe(self, direction: int) -> None:
        segs = self._segments()
        if not segs:
            return
        s = segs[0]
        times = sorted({s.start + k.t for lst in s.keyframes.values() for k in lst})
        ph = self.ctl.playhead
        if direction < 0:
            cands = [t for t in times if t < ph - 1e-4]
            if cands:
                self.ctl.set_playhead(cands[-1])
        else:
            cands = [t for t in times if t > ph + 1e-4]
            if cands:
                self.ctl.set_playhead(cands[0])
