"""
Clip indicator Settings UI: the "Clip Indicator" group (controls <-> config), the 3x3 position
picker, the live preview loop, hover-previewing an animation in a dropdown, the Test button, and the
per-clip-type dialog (colours / icon / clap sound) with its round trip through the database.
Real widgets (offscreen Qt), a real DB / config in an isolated HOME.

    QT_QPA_PLATFORM=offscreen python3 tests/test_clip_indicator_ui.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="indicator_ui_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow import clips, config, db, indicator_client as ic
from afterglow.indicator import ANCHORS, ENTER_ANIMATIONS, EXIT_ANIMATIONS, draw
from afterglow.gui.settings_page import SettingsPage
from afterglow.gui.clip_indicator_settings import ClipIndicatorDialog, ClipIndicatorGroup, ColorSwatch
from afterglow.gui.indicator_preview import AnchorPicker, IndicatorPreview

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


db.init_db()
config.load()
cfg = clips.create_clip_config("Ace", 30)

# ------------------------------------------------------------------ the group on the Settings page
page = SettingsPage()
page.resize(1100, 1000)
page.show()
app.processEvents()
g = page.indicator_group
check(isinstance(g, ClipIndicatorGroup) and g.isVisibleTo(page.parent() or page) is not None, "Settings > Clipping has a Clip Indicator group")
d = config.ClipIndicatorSettings()
check(d.size == 156 and d.clapper_opacity == 1.0 and d.ring_pulse is True and d.clap_sound == "", "defaults: 156 px, opaque, pulse on, no own clap sound")
check(g.clapper_opacity_spin.value() == 100 and g.pulse_check.isChecked() and g.clap_sound_edit.text() == "", "...and the group shows them")
check(g.enabled_check.isChecked() == d.enabled and g.style_combo.currentData() == d.style
      and g.anchor_picker.anchor() == d.anchor and g.pad_x_spin.value() == d.padding_x and g.size_spin.value() == d.size
      and g.enter_combo.currentData() == d.enter_animation and g.exit_combo.currentData() == d.exit_animation
      and g.processing_combo.currentData() == d.processing and g.circle_swatch.color() == d.circle_color
      and g.overlay_swatch.color() == d.overlay_circle_color and g.opacity_spin.value() == round(d.circle_opacity * 100)
      and g.screen_combo.currentData() == d.screen, "the controls start at the config's values")
check([g.enter_combo.itemData(i) for i in range(g.enter_combo.count())] == list(ENTER_ANIMATIONS), "every enter animation is listed")
check([g.exit_combo.itemData(i) for i in range(g.exit_combo.count())] == list(EXIT_ANIMATIONS), "every exit animation is listed")

# ---- Clip Options keeps a usable height under the tall Clip Indicator group
from afterglow.gui.settings_page import CLIP_OPTIONS_MIN_HEIGHT
page.resize(1100, 700)
app.processEvents()
rows_scroll = page.rows_container.parentWidget().parentWidget()
check(rows_scroll.height() >= CLIP_OPTIONS_MIN_HEIGHT >= 300, f"Clip Options list is not squashed ({rows_scroll.height()} px)")
page.resize(1100, 1000)
app.processEvents()

# ---- position picker: 9 cells, the centre is one
ap = g.anchor_picker
ap.resize(ap.size())
picked = []
ap.changed.connect(picked.append)
cells = {}
for r in range(3):
    for c in range(3):
        cells[(r, c)] = ap.cell_rect(r, c).center()
for (r, c), name in AnchorPicker._CELLS.items():
    QTest.mouseClick(ap, Qt.LeftButton, Qt.NoModifier, cells[(r, c)].toPoint())
    check(ap.anchor() == name, f"clicking cell ({r},{c}) selects {name}")
check(set(AnchorPicker._CELLS.values()) == set(ANCHORS) and len(ANCHORS) == 9, "the picker covers exactly the nine anchors")
check(AnchorPicker._CELLS[(1, 1)] == "center", "the middle cell is the centre anchor")
ap.set_anchor("bottom_right")
g.enter_combo.setCurrentIndex(g.enter_combo.findData("slide"))
g.exit_combo.setCurrentIndex(g.exit_combo.findData("slide"))
ap.set_anchor("center")
check(not g.pad_x_spin.isEnabled() and not g.pad_y_spin.isEnabled(), "centre: neither padding applies")
check(g.enter_combo.currentData() == "fade" and g.exit_combo.currentData() == "fade", "centre: slide defaults switch to fade")
ap.set_anchor("top")
check(g.enter_combo.currentData() == "slide" and g.exit_combo.currentData() == "slide", "leaving the centre restores slide")
ap.set_anchor("center")
g.enter_combo.setCurrentIndex(g.enter_combo.findData("pop"))
ap.set_anchor("bottom")
check(g.enter_combo.currentData() == "pop" and g.exit_combo.currentData() == "slide", "a hand-picked animation survives leaving the centre")
g.enter_combo.setCurrentIndex(g.enter_combo.findData("slide"))
ap.set_anchor("bottom_right")
ap.set_anchor("top")
check(not g.pad_x_spin.isEnabled() and g.pad_y_spin.isEnabled(), "top: padding X is unused (centred), Y applies")
ap.set_anchor("left")
check(g.pad_x_spin.isEnabled() and not g.pad_y_spin.isEnabled(), "left: padding Y is unused, X applies")
ap.set_anchor("top_right")
check(g.pad_x_spin.isEnabled() and g.pad_y_spin.isEnabled(), "a corner: both paddings apply")

# ---- save round trip through the page
g.enabled_check.setChecked(True)
g.style_combo.setCurrentIndex(g.style_combo.findData("hands"))
g.pad_x_spin.setValue(48)
g.pad_y_spin.setValue(20)
g.size_spin.setValue(140)
g.enter_combo.setCurrentIndex(g.enter_combo.findData("swing"))
g.exit_combo.setCurrentIndex(g.exit_combo.findData("zip"))
g.processing_combo.setCurrentIndex(g.processing_combo.findData("stay"))
g.circle_swatch.set_color("#112233")
g.circle_swatch.changed.emit("#112233")
g.overlay_swatch.set_color("#445566")
g.overlay_swatch.changed.emit("#445566")
g.opacity_spin.setValue(70)
g.clapper_opacity_spin.setValue(60)
g.pulse_check.setChecked(False)
g.clap_sound_edit.setText("/tmp/clap.wav")
g.screen_combo.setCurrentIndex(g.screen_combo.findData("primary"))
page._save()
ci = config.load().clip_indicator
check((ci.style, ci.anchor, ci.padding_x, ci.padding_y, ci.size) == ("hands", "top_right", 48, 20, 140),
      f"Save writes style / position / padding / size to config ({ci.style}, {ci.anchor}, {ci.padding_x}, {ci.padding_y}, {ci.size})")
check((ci.enter_animation, ci.exit_animation, ci.processing, ci.screen) == ("swing", "zip", "stay", "primary"),
      "...animations, processing mode and screen")
check((ci.circle_color, ci.overlay_circle_color) == ("#112233", "#445566") and abs(ci.circle_opacity - 0.7) < 1e-9,
      "...circle colours and opacity")
check((ci.clapper_opacity, ci.ring_pulse, ci.clap_sound) == (0.6, False, "/tmp/clap.wav"), "...clapper opacity, ring pulse and the clap sound")
g2 = SettingsPage().indicator_group
check(g2.clapper_opacity_spin.value() == 60 and not g2.pulse_check.isChecked() and g2.clap_sound_edit.text() == "/tmp/clap.wav",
      "...and the new page shows them again")
check(g2.anchor_picker.anchor() == "top_right" and g2.size_spin.value() == 140 and g2.exit_combo.currentData() == "zip",
      "a new Settings page loads what was saved")
g.enabled_check.setChecked(False)
check(not g.style_combo.isEnabled() and not g.test_btn.isEnabled() and not g.pad_x_spin.isEnabled(), "disabling greys out the other controls")
g.enabled_check.setChecked(True)
check(g.style_combo.isEnabled() and g.test_btn.isEnabled(), "...and enabling brings them back")

# ------------------------------------------------------------------ the live preview
t = [0.0]
pv = IndicatorPreview(clock=lambda: t[0])
pv.resize(480, 270)
pv.set_style({"anchor": "bottom_right", "size": 96, "enter": "slide", "exit": "slide"})


def run_loop(pv, t, limit=12.0, dt=1 / 60):
    states = []
    start = t[0]
    while t[0] - start < limit:
        t[0] += dt
        pv.step()
        ind = pv._model._inds.get(pv._cur)
        st = ind.state if ind else None
        if not states or states[-1] != st:
            states.append(st)
        if pv.finished():
            break
    return states


states = run_loop(pv, t)
check(states[:4] == ["enter", "hold", "clap", "leave"], f"the preview plays enter -> clap -> exit ({states})")
check("circle" in states and "overlay" in states and states[-2:] == ["circle_out", None], f"...then the gray circle, the purple circle, gone ({states})")
check(pv.finished(), "the loop ends and then idles")
t[0] += 5
pv.restart()
check(not pv.finished(), "restart() begins a new loop")

pv_plain = IndicatorPreview(with_overlay=False, clock=lambda: t[0])
states = run_loop(pv_plain, t)
check("overlay" not in states and "circle" in states, f"a preview without an input overlay never turns purple ({states})")


def render(pv):
    img = QImage(pv.size(), QImage.Format_ARGB32)
    img.fill(Qt.black)
    pv.render(img)
    return img


def px_diff(a, b):
    n = 0
    for y in range(0, a.height(), 3):
        for x in range(0, a.width(), 3):
            if a.pixel(x, y) != b.pixel(x, y):
                n += 1
    return n


# the clapper really is drawn, at the anchor
pv2 = IndicatorPreview(clock=lambda: t[0])
pv2.resize(640, 360)
pv2.set_style({"anchor": "bottom_right", "size": 160, "padding_x": 40, "padding_y": 40})
t[0] += 0.0
base_t = t[0]
for _ in range(60):
    t[0] += 1 / 60
    pv2.step()
img = render(pv2)
cr = pv2._canvas_rect()
s = cr.width() / 1280
# bottom-right quadrant has the clapper; top-left has none (just the backdrop)
def region_diff(img, x0, y0, x1, y1, ref):
    n = 0
    for y in range(int(y0), int(y1), 2):
        for x in range(int(x0), int(x1), 2):
            if QColor.fromRgba(img.pixel(x, y)) != QColor.fromRgba(ref.pixel(x, y)):
                n += 1
    return n
pv_empty = IndicatorPreview(clock=lambda: t[0])
pv_empty.resize(640, 360)
pv_empty.set_style({"anchor": "bottom_right", "size": 160})
ref = render(pv_empty)                       # just started: the clapper is still off-screen
w, h = img.width(), img.height()
check(region_diff(img, cr.right() - 260 * s, cr.bottom() - 220 * s, cr.right() - 10, cr.bottom() - 10, ref) > 50,
      "the clapper shows up in the corner it is anchored to")
check(region_diff(img, cr.left() + 4, cr.top() + 4, cr.left() + 200, cr.top() + 100, ref) == 0, "...and nowhere near the opposite corner")

# anchors really move it
def clapper_center(anchor):
    p = IndicatorPreview(clock=lambda: t[0])
    p.resize(640, 360)
    p.set_style({"anchor": anchor, "size": 160, "padding_x": 40, "padding_y": 40, "enter": "fade"})
    empty = render(p)
    for _ in range(90):
        t[0] += 1 / 60
        p.step()
    cur = render(p)
    xs, ys = [], []
    for y in range(0, cur.height(), 2):
        for x in range(0, cur.width(), 2):
            if cur.pixel(x, y) != empty.pixel(x, y):
                xs.append(x); ys.append(y)
    return (sum(xs) / len(xs), sum(ys) / len(ys)) if xs else None
cen = {a: clapper_center(a) for a in ANCHORS}
check(all(v is not None for v in cen.values()), "every anchor draws something")
check(cen["top_left"][0] < cen["top"][0] < cen["top_right"][0] and cen["bottom_left"][0] < cen["bottom"][0] < cen["bottom_right"][0],
      "left < centre < right along the top and the bottom")
check(cen["top"][1] < cen["left"][1] < cen["bottom"][1] and abs(cen["left"][1] - cen["right"][1]) < 6,
      "top < middle < bottom down the sides; left and right are at the same height")

# hover-preview: overrides play an animation without changing the committed settings
pv3 = IndicatorPreview(clock=lambda: t[0])
pv3.set_style({"enter": "slide", "exit": "slide"})
pv3.set_overrides(enter="drop")
check(pv3.style().enter == "drop" and pv3._style_dict["enter"] == "slide", "hover: the preview plays the hovered enter animation")
pv3.set_overrides(exit="fall")
check(pv3.style().exit == "fall", "...and the hovered exit animation")
pv3.clear_overrides()
check(pv3.style().enter == "slide" and pv3.style().exit == "slide", "...back to the committed ones when the dropdown closes")

# the dropdown's hover really drives it (popup list -> itemHovered)
g.enter_combo.showPopup()
app.processEvents()
popup = g.enter_combo.popup_widget()
check(popup is not None, "the enter dropdown opens")
if popup is not None:
    item = popup.list.item(ENTER_ANIMATIONS.index("toss"))
    popup.list.itemEntered.emit(item)
    check(g.preview.style().enter == "toss", "hovering 'Toss' in the dropdown previews it")
    g.enter_combo.hidePopup()
    app.processEvents()
    check(g.preview.style().enter == g.enter_combo.currentData(), "closing the dropdown restores the chosen animation")

# ------------------------------------------------------------------ Test button
sent = []
ic.set_test_sink(sent.append)
ClipIndicatorGroup.TEST_STEPS = ((0.05, "clap"), (0.05, "processing"), (0.05, "overlay"), (0.05, "overlay_done"))
g.screen_combo.setCurrentIndex(g.screen_combo.findData("primary"))
g.test_btn.click()
deadline = time.time() + 5
while time.time() < deadline and len(sent) < 5:
    time.sleep(0.05)
ic.flush()
check([m["event"] for m in sent] == ["start", "clap", "processing", "overlay", "overlay_done"],
      f"Test sends start, clap, processing, overlay, overlay_done ({[m['event'] for m in sent]})")
check(len({m["id"] for m in sent}) == 1 and sent[0]["style"]["anchor"] == "top_right" and sent[0]["style"]["size"] == 140,
      "...for one capture, with the settings as they are in the widgets (unsaved changes included)")
check(sent[0]["style"]["opacity"] == 0.6 and sent[0]["style"]["pulse"] is False, "...including the clapper opacity and the pulse switch")
check(sent[0]["style"]["colors"] == draw.afterglow_defaults(), "...and the afterglow colours as the base look")
ic.set_test_sink(None)

# ------------------------------------------------------------------ per-clip-type dialog
row = page._rows[0]
check(row.indicator_label.text() == "default look", "a clip option with nothing set reads 'default look'")
icon = Path(HOME) / "icon.png"
im = QImage(64, 64, QImage.Format_ARGB32)
im.fill(QColor("#e53935"))
im.save(str(icon))
dlg = ClipIndicatorDialog({"board": "#102030"}, str(icon), "/sounds/clap.wav", style_provider=g.style_dict)
check(dlg.swatches["board"].color() == "#102030" and dlg.swatches["stripe_a"].color() == draw.DEFAULT_COLORS["stripe_a"],
      "the dialog shows the clip type's colour overrides and the defaults for the rest")
check(dlg.icon_edit.text() == str(icon) and dlg.sound_edit.text() == "/sounds/clap.wav", "...its icon and clap sound")
dlg._set_color("stripe_a", "#ff0000")
dlg.swatches["stripe_a"].set_color("#ff0000")
dlg._reset_color("board")
vals = dlg.result_values()
check(vals["indicator_colors"] == {"stripe_a": "#ff0000"}, f"setting a colour adds it, resetting one removes it ({vals['indicator_colors']})")
check(dlg.swatches["board"].color() == draw.DEFAULT_COLORS["board"], "...and a reset swatch shows the default again")
_pc = dlg.preview.style().colors
check(_pc["stripe_a"] == "#ff0000" and _pc["board"] == draw.DEFAULT_COLORS["board"] and dlg.preview.style().icon == str(icon),
      "the dialog's preview uses the overrides over the afterglow defaults, and the icon")
check(dlg.preview.style().anchor == "top_right" and dlg.preview.style().style == "hands", "...and the global style from Settings (as currently edited)")
dlg.icon_edit.setText("")
dlg.sound_edit.setText("")
vals = dlg.result_values()
check(vals["indicator_icon_path"] == "" and vals["indicator_clap_sound"] == "", "clearing the icon / sound empties the fields")

# row <-> to_fields <-> DB
row._indicator.update({"indicator_colors": {"hinge": "#00ff00"}, "indicator_icon_path": str(icon), "indicator_clap_sound": "/s.wav"})
row._refresh_indicator_label()
check(row.indicator_label.text() == "custom colours, icon, clap sound", f"the row summarises what is customised ({row.indicator_label.text()})")
f = row.to_fields()
check(f["indicator_colors"] == {"hinge": "#00ff00"} and f["indicator_icon_path"] == str(icon) and f["indicator_clap_sound"] == "/s.wav",
      "to_fields carries the indicator fields")
page._save()
saved = clips.get_clip_config(cfg.id)
check(saved.indicator_colors == {"hinge": "#00ff00"} and saved.indicator_icon_path == str(icon) and saved.indicator_clap_sound == "/s.wav",
      "Save writes them to the clip type")
page2 = SettingsPage()
check(page2._rows[0].indicator_label.text() == "custom colours, icon, clap sound", "...and a new Settings page shows them again")
page._add_row(None, "Second", 20, None, None)
page._save()
new = clips.get_clip_config_by_name("Second")
check(new.indicator_colors == {} and new.indicator_icon_path == "" and new.indicator_clap_sound == "", "a new clip option is created with default indicator fields")
check(page._rows[-1].indicator_style_provider == g.style_dict, "rows preview with the Settings group's current values")


# ---- Front hand (Hands style only)
g.style_combo.setCurrentIndex(g.style_combo.findData("clapper"))
check(not g.front_combo.isEnabled(), "Front hand is off for the clapper")
g.style_combo.setCurrentIndex(g.style_combo.findData("hands"))
check(g.front_combo.isEnabled() and g.front_combo.currentData() == "right", "Front hand applies to the Hands style, right by default")
g.front_combo.setCurrentIndex(g.front_combo.findData("left"))
check(g.style_dict()["hands_front"] == "left" and g.preview.style().hands_front == "left", "...reaches the style and the preview")
page._save()
check(config.load().clip_indicator.hands_front == "left", "...and is saved")

# ---- Hands look (Hands style only): retro by default, cel to choose
check(g.look_combo.isEnabled() and g.look_combo.currentData() == "retro", "Hands look: on for the Hands style, retro by default")
g.look_combo.setCurrentIndex(g.look_combo.findData("cel"))
check(g.style_dict()["hands_look"] == "cel" and g.preview.style().hands_look == "cel", "...reaches the style and the preview")
check(g.style_dict()["colors"]["glove"] == draw.afterglow_defaults(None, "cel")["glove"], "...and the glove colours follow the look")
page._save()
check(config.load().clip_indicator.hands_look == "cel", "...and is saved")
g.style_combo.setCurrentIndex(g.style_combo.findData("clapper"))
check(not g.look_combo.isEnabled(), "Hands look is off for the clapper")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
