"""Settings auto-save, and OBS's keyframe interval written into OBS's profile.

    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_settings_autosave.py
(also runs with QT_QPA_PLATFORM=offscreen)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="autosave_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["XDG_DATA_HOME"] = str(Path(HOME) / ".local" / "share")
os.environ["XDG_RUNTIME_DIR"] = HOME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

FAILS = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def wait_saved(page, ms=2000):
    QTest.qWait(80)          # a button's save is scheduled just after its own click handler
    t = 0
    while page._autosave_timer.isActive() and t < ms:
        QTest.qWait(50)
        t += 50
    QTest.qWait(50)


app = QApplication.instance() or QApplication(sys.argv)

# a Flatpak OBS profile in Advanced output mode, recording with the stream encoder
obs = Path(HOME) / ".var/app/com.obsproject.Studio/config/obs-studio"
prof = obs / "basic/profiles/Gaming"
prof.mkdir(parents=True)
(obs / "user.ini").write_text("[Basic]\nProfile=Gaming\nProfileDir=Gaming\n")
(prof / "basic.ini").write_text("[General]\nName=Gaming\n\n[Output]\nMode=Advanced\n\n[AdvOut]\nEncoder=ffmpeg_vaapi_tex\nRecEncoder=none\n")
(prof / "streamEncoder.json").write_text(json.dumps({"bitrate": 51000, "rate_control": "CBR", "keyint_sec": 2}))

from afterglow import config, db, obs_profile
db.init_db()

# ---------------------------------------------------------------- obs_profile
info = obs_profile.read_keyframe_info()
check(info.ok and info.profile == "Gaming" and info.encoder_file.name == "streamEncoder.json" and info.keyint_sec == 2,
      "reads the replay buffer's encoder file (stream encoder) and its 2 s keyframe interval")
info = obs_profile.set_keyint_sec(1)
data = json.loads((prof / "streamEncoder.json").read_text())
check(info.keyint_sec == 1 and data["keyint_sec"] == 1 and data["bitrate"] == 51000 and data["rate_control"] == "CBR",
      "set to 1 s: only keyint_sec changes, the rest of OBS's encoder settings are kept")
check(json.loads((prof / "streamEncoder.json.afterglow-backup").read_text())["keyint_sec"] == 2,
      "...with a backup of the original")
(prof / "basic.ini").write_text("[General]\nName=Gaming\n\n[Output]\nMode=Advanced\n\n[AdvOut]\nRecEncoder=obs_x264\n")
check(obs_profile.read_keyframe_info().encoder_file.name == "recordEncoder.json",
      "a separate recording encoder: recordEncoder.json is the one changed")
(prof / "basic.ini").write_text("[General]\nName=Gaming\n\n[Output]\nMode=Simple\n")
i2 = obs_profile.read_keyframe_info()
check(not i2.ok and "Simple" in i2.reason, "Simple output mode: explained, nothing written")
(prof / "basic.ini").write_text("[General]\nName=Gaming\n\n[Output]\nMode=Advanced\n\n[AdvOut]\nRecEncoder=none\n")

# ---------------------------------------------------------------- auto-save
from afterglow.gui.settings_page import SettingsPage

page = SettingsPage()
page.show()
QTest.qWait(100)
from afterglow.gui.custom_button import CustomButton
check(not any(b.text() == "Save Settings" for b in page.findChildren(CustomButton)), "no Save button any more")
check(page.obs_keyint_combo.currentData() == 1 and "every 1 s" in page.obs_keyint_status.text(),
      "Settings shows OBS's current keyframe interval")

page.obs_host_edit.clear()
QTest.keyClicks(page.obs_host_edit, "192.168.1.50")
check(config.load().obs.host != "192.168.1.50", "typing doesn't write on every keystroke")
wait_saved(page)
check(config.load().obs.host == "192.168.1.50", "...a moment after typing stops, it's saved")

page.obs_port_spin.setValue(4466)
wait_saved(page)
check(config.load().obs.port == 4466, "spin box change saved")

before = config.load().auto_copy_as_mp4
page.auto_copy_mp4_checkbox.setChecked(not before)
wait_saved(page)
check(config.load().auto_copy_as_mp4 == (not before), "checkbox change saved")

page.throttle_combo.setCurrentIndex(page.throttle_combo.findData("auto"))
wait_saved(page)
check(config.load().processing_throttle == "auto", "combo change saved")

good = config.load().appearance.afterglow_color_accent
page.afterglow_accent_edit.setText("#12")
wait_saved(page)
check(config.load().appearance.afterglow_color_accent == good and "Not saved yet" in page.status_label.text(),
      "a half-typed colour isn't saved and shows why in the status line (no dialog)")
page.afterglow_accent_edit.setText("#123456")
wait_saved(page)
check(config.load().appearance.afterglow_color_accent == "#123456" and "saved" in page.status_label.text(),
      "...saved as soon as it's valid")

page.obs_host_edit.setText("10.0.0.9")
page.hide()          # leaving the page flushes a pending change immediately
check(config.load().obs.host == "10.0.0.9", "leaving Settings saves a pending change right away")
page.show()

# clip options: add a row with the button, rename it
from afterglow import clips
n0 = len(clips.list_clip_configs())
add_btn = next(b for b in page.findChildren(CustomButton) if "Add" in b.text() and "Clip" in b.text())
QTest.mouseClick(add_btn, Qt.LeftButton)
wait_saved(page)
check(len(clips.list_clip_configs()) == n0 + 1, "the Add button creates a clip option")
row = page._rows[-1]
row.name_edit.setText("Ace 2")
wait_saved(page)
check(any(c.name == "Ace 2" for c in clips.list_clip_configs()), "renaming a clip option saves it")
page._remove_row(row)
wait_saved(page)
check(len(clips.list_clip_configs()) == n0, "removing it deletes it")

# importing settings swaps in a fresh page so auto-save can't write the old values back
from afterglow.gui import main_window  # noqa: F401  (MainWindow wiring is exercised elsewhere)
rebuilt = []
page.rebuild_requested.connect(lambda: rebuilt.append(1))
st = config.load()
st.obs.host = "imported-host"
config.save(st)
page._autosave_ready = False
page.rebuild_requested.emit()
page2 = SettingsPage()
check(rebuilt and page2.obs_host_edit.text() == "imported-host", "after an import the page is rebuilt from the new values")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
