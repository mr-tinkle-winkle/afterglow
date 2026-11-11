"""
The setup guide (Settings > Advanced > Setup Guide): its live checks
(setup_checks.py) and the window that shows them.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_setup_guide.py
"""
import os, sys, tempfile, time, json
from pathlib import Path
HOME = tempfile.mkdtemp(prefix="guide_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config, clips, setup_checks as sc, obs_profile
db.init_db()

fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


settings = config.load()
settings.obs.port = 1          # nothing listens there: "OBS isn't running"

# ---------------------------------------------------------------- checks
obs_check, facts = sc.check_obs(settings)
check(obs_check.state == sc.TODO and not facts["connected"] and "WebSocket" in obs_check.detail
      and obs_check.settings_tab == "Clipping", "no OBS: a to-do that says how to turn on the WebSocket server")
rb = sc.check_replay_buffer(settings, facts, [])
check(rb.state == sc.TODO and "once OBS is connected" in rb.summary, "the replay buffer waits for the connection")


class Cfg:
    def __init__(self, name, length, hotkey):
        self.name, self.length_seconds, self.hotkey = name, length, hotkey


connected = {"connected": True, "buffer_seconds": 60, "buffer_available": True, "buffer_active": True}
check(sc.check_replay_buffer(settings, connected, [Cfg("a", 30, "F8")]).state == sc.OK, "replay buffer long enough: ready")
too_short = sc.check_replay_buffer(settings, connected, [Cfg("a", 30, "F8"), Cfg("b", 120, "F9")])
check(too_short.state == sc.TODO and "1 min" in too_short.summary and "2 min" in too_short.summary,
      "a clip option longer than the replay buffer is called out with both lengths")
off = sc.check_replay_buffer(settings, dict(connected, buffer_available=False), [])
check(off.state == sc.TODO and "Enable Replay Buffer" in off.detail, "replay buffer disabled in OBS: says where to enable it")
idle = sc.check_replay_buffer(settings, dict(connected, buffer_active=False), [Cfg("a", 30, "F8")])
check(idle.state == sc.OK and "starts it" in idle.summary, "enabled but not running is fine (afterglow starts it)")

check(sc.check_clip_options([]).state == sc.TODO, "no clip options: to do")
nokey = sc.check_clip_options([Cfg("Thirty", 30, None)])
check(nokey.state == sc.TODO and "Record" in nokey.detail, "clip options without a hotkey: to do")
ok = sc.check_clip_options([Cfg("Thirty", 30, "ctrl+F8"), Cfg("Long", 120, None)])
check(ok.state == sc.OK and "Thirty: 30 s on ctrl+F8" in ok.summary and "Long: 2 min (no hotkey)" in ok.summary,
      "clip options are listed with length and hotkey")

folder = sc.check_clips_folder(settings)
check(folder.state == sc.OK and str(settings.clips_path()) in folder.summary, "the clips folder is created and writable")

# keyframes: no OBS profile, then Simple mode, then Advanced 2 s (auto on / off), then 1 s
kf = sc.check_keyframes(settings)
check(kf.state == sc.INFO and "wasn't found" in kf.summary, "no OBS settings folder: not a to-do, just said")
root = Path(HOME) / ".config/obs-studio"
prof = root / "basic/profiles/Gaming"
prof.mkdir(parents=True)
(root / "user.ini").write_text("[Basic]\nProfileDir=Gaming\n")
(prof / "basic.ini").write_text("[General]\nName=Gaming\n[Output]\nMode=Simple\n")
check(sc.check_keyframes(settings).state == sc.TODO and "Simple" in sc.check_keyframes(settings).summary,
      "Simple output mode: to do (switch to Advanced)")
(prof / "basic.ini").write_text("[General]\nName=Gaming\n[Output]\nMode=Advanced\n[AdvOut]\nRecEncoder=none\nEncoder=obs_x264\n")
(prof / "streamEncoder.json").write_text(json.dumps({"keyint_sec": 2, "bitrate": 40000}))
settings.obs.auto_keyframe_interval = False
kf = sc.check_keyframes(settings)
check(kf.state == sc.TODO and "2 s" in kf.summary and "Apply to OBS" in kf.detail, "2 s with automatic off: says how to set it")
settings.obs.auto_keyframe_interval = True
kf = sc.check_keyframes(settings)
check(kf.state == sc.TODO and "automatically" in kf.summary, "2 s with automatic on: says it will be set")

# ---- the automatic keyframe interval itself
notes = []
settings.obs.auto_keyframe_interval = False
check(obs_profile.auto_apply(settings, on_changed=notes.append) is None
      and json.loads((prof / "streamEncoder.json").read_text())["keyint_sec"] == 2, "automatic off: OBS's file is left alone")
settings.obs.auto_keyframe_interval = True
msg = obs_profile.auto_apply(settings, on_changed=notes.append)
data = json.loads((prof / "streamEncoder.json").read_text())
check(msg is not None and notes == [msg] and data == {"keyint_sec": 1, "bitrate": 40000},
      "automatic on: keyint_sec becomes 1, the rest of OBS's encoder settings are kept, one notice")
check("was 2 s" in msg and "Gaming" in msg and "OBS" in msg, f"the notice says what changed ({msg[:60]}...)")
check(json.loads((prof / "streamEncoder.json.afterglow-backup").read_text())["keyint_sec"] == 2, "the original is backed up once")
check(obs_profile.auto_apply(settings, on_changed=notes.append) is None and len(notes) == 1,
      "already 1 s: nothing written, no second notice")
(prof / "streamEncoder.json").write_text(json.dumps({"bitrate": 40000}))       # OBS's "auto" (no keyint_sec)
check(obs_profile.auto_apply(settings, on_changed=notes.append) is not None and "was Auto" in notes[-1]
      and json.loads((prof / "streamEncoder.json").read_text())["keyint_sec"] == 1, "OBS's Auto (0) counts as not 1 s")
(prof / "basic.ini").write_text("[General]\nName=Gaming\n[Output]\nMode=Simple\n")
check(obs_profile.auto_apply(settings, on_changed=notes.append) is None, "Simple output mode: nothing to do, no error")
(prof / "basic.ini").write_text("[General]\nName=Gaming\n[Output]\nMode=Advanced\n[AdvOut]\nRecEncoder=none\nEncoder=obs_x264\n")
check(sc.check_keyframes(settings).state == sc.OK, "the guide then shows the keyframe step as ready")
check(config.load().obs.auto_keyframe_interval is True, "the automatic setting defaults to on")

all_checks = sc.run_checks(settings)
keys = [c.key for c in all_checks]
check(keys[:6] == ["obs", "replay_buffer", "keyframes", "daemon", "clips", "folder"]
      and keys[6:] == ["filters", "library", "editor", "overlay", "youtube"], f"the steps come in setup order {keys}")
ready, checked = sc.summary(all_checks)
check(checked == 6 and 1 <= ready < 6, f"summary counts only the checked steps ({ready} of {checked})")

# ---------------------------------------------------------------- the window
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
app = QApplication([])
from afterglow.gui.main_window import MainWindow, _SETTINGS_INDEX, _LIBRARY_INDEX
from afterglow.gui.setup_guide import SetupGuideDialog


def pump(s=0.3):
    end = time.perf_counter() + s
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


state = {"calls": 0, "fixed": False}


def fake_checks():
    state["calls"] += 1
    time.sleep(0.15)        # a slow OBS: the window must not wait for it
    return [
        sc.Check("obs", "Connect to OBS", sc.OK if state["fixed"] else sc.TODO,
                 "Connected." if state["fixed"] else "OBS isn't reachable.", "do this\nthen that", settings_tab="Clipping"),
        sc.Check("clips", "Clip options and hotkeys", sc.OK, "Thirty: 30 s on F8.", "", settings_tab="Clipping"),
        sc.Check("filters", "Filters", sc.INFO, "Labels for clips.", "", settings_tab="Filters"),
        sc.Check("library", "Library and previewer", sc.INFO, "Every clip.", "", page="library"),
    ]


w = MainWindow()
w.resize(1400, 900)
w.show()
pump(0.4)
t0 = time.perf_counter()
d = SetupGuideDialog(w, run_checks=fake_checks)
built = time.perf_counter() - t0
d.navigate_requested.connect(w._on_setup_guide_navigate)
d.show()
check(built < 0.14 and d.progress_label.text() == "Checking..." and not d.recheck_button.isEnabled(),
      f"the window opens at once and says it is checking ({built * 1000:.0f} ms)")
check(d.wait_for_checks(), "the checks arrive")
check(len(d.cards) == 4 and [c.badge.state for c in d.cards] == [sc.TODO, sc.OK, sc.INFO, sc.INFO], "one card per step with its state")
check(d.cards[0].title_label.text() == "1. Connect to OBS" and d.cards[0].summary_label.text() == "OBS isn't reachable."
      and "then that" in d.cards[0].detail_label.text(), "a card shows number, title, finding and what to do")
check(d.progress_label.text() == "1 of 2 steps are set up. Next: Connect to OBS.", f"progress line: {d.progress_label.text()}")
check(d.cards[0].go_button.text() == "Open Settings > Clipping" and d.cards[3].go_button.text() == "Open Library",
      "each card has a button to where it is set up")

d.cards[2].go_button.click()
pump(0.6)
sp = w.settings_page
check(w.stack.currentIndex() == _SETTINGS_INDEX and sp._tab_labels[sp._settings_stack.currentIndex()] == "Filters",
      "the button opens that Settings tab")
check(sp._tab_button_group.checkedId() == sp._settings_stack.currentIndex(), "...with the tab's own button shown as selected")
check(d.isVisible(), "the guide stays open (not modal)")
d.cards[3].go_button.click()
pump(0.5)
check(w.stack.currentIndex() == _LIBRARY_INDEX, "Open Library goes to the Library")
check(sp.show_tab("Advanced") and not sp.show_tab("Nope"), "show_tab reports unknown tabs")

state["fixed"] = True
d.recheck_button.click()
check(not d.recheck_button.isEnabled(), "Check Again runs the checks again")
d.wait_for_checks()
check(state["calls"] == 2 and d.cards[0].badge.state == sc.OK
      and d.progress_label.text().startswith("All 2 steps are set up"), "a fixed step turns ready; all done is said")
d.close()

# from Settings > Advanced, and only one at a time
w.open_settings_tab("Advanced")
pump(0.3)
import afterglow.gui.setup_guide as sg
sg.setup_checks.run_checks = fake_checks
w.settings_page.setup_guide_btn.click()
pump(0.3)
g1 = w._setup_guide
check(g1 is not None and g1.isVisible(), "Settings > Advanced > Open Setup Guide opens it")
check(w.show_setup_guide() is g1, "opening it again raises the open one")
g1.wait_for_checks()
g1.close()
pump(0.2)

# first start: once, and only on an empty library
s = config.load()
check(s.setup_guide_seen is False, "a new install hasn't seen the guide")
w._setup_guide = None
w.maybe_show_setup_guide()
pump(1.0)
check(config.load().setup_guide_seen is True and w._setup_guide is not None and w._setup_guide.isVisible(),
      "first start with an empty library: the guide opens by itself")
w._setup_guide.wait_for_checks()
w._setup_guide.close()
pump(0.2)
w._setup_guide = None
w.maybe_show_setup_guide()
pump(0.9)
check(w._setup_guide is None, "...and never again by itself")
w.close()

print("\n%d failed" % len(fails) if fails else "\nall passed")
os._exit(1 if fails else 0)
