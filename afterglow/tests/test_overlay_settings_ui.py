"""
Input overlay settings UI: the per-clip-type "Input overlay" toggle + options
dialog widgets and Settings > Input Overlay (global placements), with real
widgets under the offscreen platform and a real DB/config in an isolated HOME.

    QT_QPA_PLATFORM=offscreen python3 tests/test_overlay_settings_ui.py
"""
import os
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="overlay_ui_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow import clips, config, db
from afterglow.gui.settings_page import SettingsPage
from afterglow.gui.overlay_options import OverlayOptionsDialog, PlacementEditor

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


db.init_db()
config.load()
cfg = clips.create_clip_config("Ace", 30)

page = SettingsPage()
page.resize(1100, 900)
page.show()
app.processEvents()
row = page._rows[0]
check(row.overlay_check.isChecked() is False and not row.overlay_options_btn.isEnabled(),
      "clip type row: overlay off by default, options button disabled")
check(row.overlay_reason_label.text() == "", "no availability warning while the overlay is off")

row.overlay_check.setChecked(True)
app.processEvents()
check(row.overlay_options_btn.isEnabled() and row.overlay_reason_label.text() == "", "turning it on enables the options button; Puppetry available -> no warning")

os.environ["FAKE_PUPPETRY_OFF"] = "1"
row._refresh_overlay_reason()
check("Unavailable" in row.overlay_reason_label.text() and "Layered Replay Buffer" in row.overlay_reason_label.text(),
      "availability reason is shown beside the toggle when Puppetry can't record")
del os.environ["FAKE_PUPPETRY_OFF"]
row._refresh_overlay_reason()

# options dialog round trip (without exec(): drive its widgets directly)
dlg = OverlayOptionsDialog(["keyboard", "mouse"], True, 0.0, {}, page)
dlg._piece_checks["controller"].setChecked(True)
dlg._piece_checks["mouse"].setChecked(False)
dlg.show_default_check.setChecked(False)
dlg.offset_spin.setValue(-33)
dlg.placements._rows["keyboard"]["inherit"].setChecked(False)
dlg.placements._rows["keyboard"]["x"].setValue(25.0)
dlg.placements._rows["keyboard"]["rotation"].setValue(12.0)
vals = dlg.result_values()
check(vals["overlay_pieces"] == ["keyboard", "controller"] and vals["overlay_visible_default"] is False
      and vals["overlay_offset_ms"] == -33, "dialog returns pieces / show-by-default / offset")
check(set(vals["overlay_placements"]) == {"keyboard"} and abs(vals["overlay_placements"]["keyboard"]["x"] - 0.25) < 1e-9
      and vals["overlay_placements"]["keyboard"]["rotation"] == 12.0,
      "only pieces not set to 'Use global' are stored as overrides")
dlg2 = OverlayOptionsDialog(vals["overlay_pieces"], False, -33, vals["overlay_placements"], page)
check(dlg2.placements._rows["mouse"]["inherit"].isChecked() and not dlg2.placements._rows["keyboard"]["inherit"].isChecked(),
      "reopening restores which pieces override and which use global")
row._overlay.update(vals)

# global placements page
gp = page.input_overlay_page
gp.editor._rows["mouse"]["x"].setValue(50.0)
gp.editor._rows["mouse"]["rotation"].setValue(7.0)

page._save()
saved = clips.get_clip_config(cfg.id)
check(saved.overlay_enabled and saved.overlay_pieces == ["keyboard", "controller"] and saved.overlay_offset_ms == -33
      and saved.overlay_visible_default is False and saved.overlay_placements["keyboard"]["rotation"] == 12.0,
      "Save Settings persists the clip type's overlay options")
g = config.load().overlay_placements
check(abs(g["mouse"]["x"] - 0.5) < 1e-9 and g["mouse"]["rotation"] == 7.0, "Save Settings persists the global placements")

# a brand-new row is created with its overlay fields
page._add_row(None, "Second", 20)
page._rows[-1].overlay_check.setChecked(True)
page._save()
second = clips.get_clip_config_by_name("Second")
check(second.overlay_enabled and second.overlay_pieces == ["keyboard", "mouse"], "a new clip type saves with its overlay fields")

page2 = SettingsPage()
r2 = [r for r in page2._rows if r.to_fields()["name"] == "Ace"][0]
check(r2.overlay_check.isChecked() and r2._overlay["overlay_offset_ms"] == -33, "settings reload shows the saved overlay state")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
