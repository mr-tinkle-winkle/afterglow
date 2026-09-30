"""
Round 11 feedback: global text presets and global audio, shared by every
project.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_round11.py
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="r11_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)
for name in ("first", "second"):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                    "-f", "lavfi", "-i", "sine", "-t", "5", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                    "-c:a", "aac", str(CLIPS / f"{name}.mp4")], check=True)
MUSIC = os.path.join(HOME, "theme song.wav")
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330", "-t", "3", MUSIC],
               check=True)

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

app = QApplication([])
from afterglow import library
from afterglow.gui.main_window import MainWindow
from afterglow.nle import globals as gl, ops

fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


def pump(s):
    end = time.perf_counter() + s
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def list_labels(lst):
    return [lst.item(i).text() for i in range(lst.count())]


def text_of(ctl, sid):
    return ctl.project.find_segment(sid)[1].parts[0].text


library.scan_and_ingest_new_videos()
vids = {v.title: v for v in library.list_videos()}
w = MainWindow()
w.resize(1600, 1000)
w.show()
pump(1.5)
w._open_in_editor(vids["first"].id)
pump(1.0)
page = w.editor_page
ctl = page.ctl
browser = page.browser

# ---- global text preset: save from one element ------------------------------------------------------------
t1 = ctl.add_text("Speech bubble", t=0.5)
pump(0.2)
ctl.perform("style", lambda p: [setattr(text_of(ctl, t1.id), k, v) for k, v in
                                 (("bubble_fill", "#e6d3a3"), ("color", "#5a3310"), ("font_family", "Bangers"),
                                  ("bubble_variant", "wiggly"))])
ctl.set_selection([t1.id])
check(ctl.save_global_preset("Blue"), "save the selected text's look as global preset “Blue”")
pump(0.3)
pr = gl.get_preset("Blue")
check(pr is not None and pr["props"]["text"]["bubble_fill"] == "#e6d3a3" and pr["props"]["text"]["font_family"] == "Bangers",
      "...stored for every project (colors, font, style)")
check(text_of(ctl, t1.id).global_preset == "Blue", "...and the element is linked to it")
labels = list_labels(browser.text_list)
check("Global presets" in labels and "★ Blue" in labels, f"Text tab lists it ({labels[-3:]})")

# add from the preset
t2 = ctl.add_text_from_global("Blue", t=2.5)
pump(0.2)
st2 = text_of(ctl, t2.id)
check(st2.bubble_fill == "#e6d3a3" and st2.font_family == "Bangers" and st2.bubble == "speech"
      and st2.bubble_variant == "wiggly" and st2.global_preset == "Blue", "adding “Blue” gives that look")

# update restyles every linked element
ctl.perform("recolor", lambda p: setattr(text_of(ctl, t1.id), "bubble_fill", "#a3c8e6"))
ctl.set_selection([t1.id])
ctl.save_global_preset("Blue")
pump(0.2)
check(text_of(ctl, t2.id).bubble_fill == "#a3c8e6", "updating the preset restyles the other elements using it")

# apply to a plain text
t3 = ctl.add_text("Plain text", t=4.0)
pump(0.2)
ctl.set_selection([t3.id])
ctl.apply_global_preset("Blue")
st3 = text_of(ctl, t3.id)
check(st3.bubble_fill == "#a3c8e6" and st3.global_preset == "Blue" and st3.text == "Text",
      "Apply to Selected: the look, not the words")
ctl.undo()
check(text_of(ctl, t3.id).global_preset == "", "...undoable")
from PySide6.QtWidgets import QMenu
from afterglow.gui.advanced_editor.timeline import add_global_actions
ctl.set_selection([t1.id])
m = QMenu()
add_global_actions(m, ctl, page)
names = [a.text() for a in m.actions()]
check("Save as Global Preset…" in names and "Apply Global Preset" in names,
      f"timeline right-click: Save as / Apply Global Preset ({names})")

# ---- global audio -------------------------------------------------------------------------------------------
seg_a = ctl.add_file(MUSIC, t=0.0)
pump(0.4)
ctl.set_selection([seg_a.id])
m = QMenu()
add_global_actions(m, ctl, page)
check("Make Audio Global…" in [a.text() for a in m.actions()], "timeline right-click on audio: Make Audio Global")
entry = ctl.make_audio_global(MUSIC, "Theme")
pump(0.3)
check(entry is not None and os.path.exists(entry["file"]) and entry["file"] != MUSIC,
      "Make Global stores a copy of the audio")
labels = list_labels(browser.audio_list)
check("Global audio" in labels and "★ Theme" in labels, f"Audio tab lists it ({labels})")
page.save_edits()
pump(0.2)

# ---- a different project sees both ----------------------------------------------------------------------------
w._open_in_editor(vids["second"].id)
pump(1.2)
check(ctl.project.library_video_id == vids["second"].id, "opened another clip")
labels_t = list_labels(browser.text_list)
labels_a = list_labels(browser.audio_list)
check("★ Blue" in labels_t and "★ Theme" in labels_a, "the other project has the global preset and audio")
t4 = ctl.add_text_from_global("Blue", t=1.0)
pump(0.2)
check(text_of(ctl, t4.id).bubble_fill == "#a3c8e6", "...and the preset gives the same look there")
row = next(i for i in range(browser.audio_list.count()) if browser.audio_list.item(i).text() == "★ Theme")
browser._activate_item(browser.audio_list.item(row))
pump(0.4)
check(any(pt.source == entry["file"] for s in ctl.project.all_segments() for pt in s.parts),
      "double-clicking global audio adds it")
browser._group.button(1).click()
pump(0.2)
browser.grab().save(f"{OUT}/r11_text_tab.png")
browser._group.button(2).click()
pump(0.2)
browser.grab().save(f"{OUT}/r11_audio_tab.png")

# rename / delete
gl.rename_preset("Blue", "Blue (friend)")
ctl.globals_changed.emit()
pump(0.2)
check("★ Blue (friend)" in list_labels(browser.text_list), "renaming a preset")
gl.delete_preset("Blue (friend)")
gl.remove_audio(entry["file"])
ctl.globals_changed.emit()
pump(0.2)
check("★ Blue (friend)" not in list_labels(browser.text_list) and
      "★ Theme" not in list_labels(browser.audio_list), "deleting a preset / removing global audio")
check(os.path.exists(entry["file"]), "...the stored audio copy stays (projects using it keep working)")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
