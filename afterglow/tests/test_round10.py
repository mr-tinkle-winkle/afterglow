"""
Round 10 feedback: Save Edits vs Export, Clean Up, Copy/Paste Colors and
Properties.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_round10.py
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="r10_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                "-c:a", "aac", str(CLIPS / "clip.mp4")], check=True)

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication([])
from afterglow import library
from afterglow.gui.main_window import MainWindow
from afterglow.nle import ops, store
from afterglow.nle.model import (KIND_AV, KIND_TEXT, Part, Project, Segment, TextStyle, Track, Transition,
                                 default_project, empty_track)

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


def text_seg(start, dur, name, **st):
    return Segment(start=start, name=name, parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=dur,
                                                       has_video=True, has_audio=False,
                                                       text=TextStyle(text=name, **st))])


def av_seg(start, dur, name, video=True, audio=True):
    return Segment(start=start, name=name, parts=[Part(kind=KIND_AV, source="/x.mp4", src_in=0, src_out=dur,
                                                       has_video=video, has_audio=audio)])


def track(*segs, hidden=False):
    t = empty_track()
    t.segments = list(segs)
    t.hidden = hidden
    return t


# ---- Clean Up (model) ----------------------------------------------------------------------------------------------
p = default_project(640, 360, 30)
title = text_seg(0, 2, "title")
cap = text_seg(3, 2, "caption")               # doesn't overlap title: both texts end on one track
over = text_seg(1, 3, "over video")           # overlaps title in time and sits above it
vid = av_seg(0, 6, "video")
a1 = av_seg(0, 3, "music", video=False)
a2 = av_seg(4, 2, "sfx", video=False)
p.tracks = [empty_track(), track(over), empty_track(), track(title), empty_track(), track(cap), empty_track(),
            track(vid), empty_track(), empty_track(), track(a1), empty_track(), track(a2), empty_track()]
times = {s.id: (s.start, s.end) for s in p.all_segments()}
ops.clean_up(p)
names = [[s.name for s in t.sorted_segments()] for t in p.tracks]
check(names == [[], ["over video"], ["title", "caption"], ["video"], ["music", "sfx"], []],
      f"Clean Up packs everything toward the middle and drops empty tracks ({names})")
check(all(times[s.id] == (s.start, s.end) for s in p.all_segments()), "...without changing any times")
idx = {s.name: i for i, t in enumerate(p.tracks) for s in t.segments}
check(idx["over video"] < idx["title"] < idx["video"], "...and what was on top stays on top")

# transition chains move together; hidden tracks are left alone
p2 = default_project(640, 360, 30)
A = av_seg(0, 3, "A", audio=False)
B = av_seg(3, 3, "B", audio=False)
B.transition_in = Transition()
blocker = av_seg(3.5, 1, "under B", audio=False)
H = text_seg(0, 1, "hidden text")
p2.tracks = [empty_track(), track(H, hidden=True), empty_track(), track(A, B), empty_track(), track(blocker), empty_track()]
ops.clean_up(p2)
idx2 = {s.name: i for i, t in enumerate(p2.tracks) for s in t.segments}
check(idx2["A"] == idx2["B"] and idx2["A"] < idx2["under B"], f"a transition pair stays on one track ({idx2})")
check(any(t.hidden and [s.name for s in t.segments] == ["hidden text"] for t in p2.tracks),
      "hidden tracks are left as they are")
check(not p2.tracks[0].segments and not p2.tracks[-1].segments and len(p2.tracks) >= ops.MIN_TRACKS,
      "empty drop tracks stay at the top and bottom")

# ---- Copy / Paste (model) ---------------------------------------------------------------------------------------
p3 = default_project(640, 360, 30)
src = text_seg(0, 2, "src", color="#c8a060", outline_color="#402000", bubble="speech", bubble_fill="#f5e6c0",
               font_family="Bangers", font_size=0.09, bubble_variant="spiky", grow_in=0.4)
src.shadow = True
src.shadow_color = "#220011"
dst = text_seg(3, 2, "dst words")
v1 = av_seg(0, 6, "v1")
v1.transform.scale, v1.transform.crop_left, v1.fade_in, v1.volume = 0.8, 0.1, 0.5, 1.4
v2 = av_seg(0, 6, "v2")
p3.tracks = [empty_track(), track(src, dst), track(v1), track(v2), empty_track()]
colors = ops.copy_colors(src)
ops.paste_style(p3, [dst.id], colors)
check(dst.parts[0].text.color == "#c8a060" and dst.parts[0].text.bubble_fill == "#f5e6c0"
      and dst.shadow_color == "#220011" and dst.parts[0].text.font_family != "Bangers",
      "Paste Colors: colors only")
props = ops.copy_properties(src)
ops.paste_style(p3, [dst.id], props)
dt = dst.parts[0].text
check(dt.font_family == "Bangers" and dt.bubble == "speech" and dt.bubble_variant == "spiky" and dt.grow_in == 0.4
      and dt.text == "dst words" and dst.start == 3 and dst.shadow,
      "Paste Properties: the look and behavior, not the text or timing")
ops.paste_style(p3, [v2.id], ops.copy_properties(v1))
check(v2.transform.scale == 0.8 and v2.transform.crop_left == 0.1 and v2.fade_in == 0.5 and v2.volume == 1.4
      and v2.transform.x == 0.0, "video -> video: scale, crop, fades, volume (not position)")
ops.paste_style(p3, [dst.id], ops.copy_properties(v1))
check(dst.transform.crop_left == 0.0 and dst.volume == 1.0, "no crop or volume pasted onto text")
check(not ops.has_colors(v2) and ops.has_colors(src), "Copy Colors is for elements with colors (text, shadow)")

# ---- UI ---------------------------------------------------------------------------------------------------------
library.scan_and_ingest_new_videos()
v = library.list_videos()[0]
w = MainWindow()
w.resize(1600, 1000)
w.show()
pump(1.5)
w._open_in_editor(v.id)
pump(1.0)
page = w.editor_page
ctl = page.ctl
check(page.save_edits_btn.text() == "Save Edits" and page.save_btn.text() == "Export", "header: Save Edits + Export")
mtime = os.stat(v.path).st_mtime_ns
t1 = ctl.add_text("Speech bubble", t=0.5)
pump(0.3)
check(page.save_edits_btn.isEnabled() and "Unsaved" in page.unsaved_label.text(), "an edit: unsaved")
page.setFocus()
QTest.keyClick(page, Qt.Key_S, Qt.ControlModifier)
pump(0.3)
path = store.project_path_for_video(v.id)
saved = store.load_project(path)
check(not ctl.unsaved and saved is not None and not saved.unsaved_changes and
      any(s.id == t1.id for s in saved.all_segments()), "Ctrl+S = Save Edits: the project is saved")
check(os.stat(v.path).st_mtime_ns == mtime, "...the video itself is untouched")
check("not exported" in page.unsaved_label.text(), f"...and shows it isn't exported ({page.unsaved_label.text()!r})")
t2 = ctl.add_text("Caption", t=2.0)
pump(0.2)
page.discard_changes(confirm=False)
pump(0.3)
ids = {s.id for s in ctl.project.all_segments()}
check(t1.id in ids and t2.id not in ids, "Discard Changes goes back to the last Save Edits")

# Copy/paste in the UI
ctl.set_selection([t1.id])
pump(0.3)
props_panel = page.properties
check(all(k in props_panel._fields for k in ("paste_colors_btn", "paste_props_btn")),
      "Properties: Copy/Paste Colors and Properties buttons")
menu = page.timeline.view.build_context_menu(QPointF(page.timeline.view.x_of(1.0) if hasattr(page.timeline.view, "x_of") else 300,
                                                     page.timeline.view.track_top(1) + 10))
labels = [a.text() for a in menu.actions()] if menu else []
check({"Copy Colors", "Paste Colors", "Copy Properties", "Paste Properties"} <= set(labels),
      f"timeline right-click menu has them ({[l for l in labels if 'Col' in l or 'Prop' in l]})")
ctl.set_selection([t1.id])
st1 = ctl.project.find_segment(t1.id)[1]
ctl.perform("color", lambda p: setattr(p.find_segment(t1.id)[1].parts[0].text, "bubble_fill", "#e8d8a0"))
ctl.copy_style("colors")
t3 = ctl.add_text("Thought bubble", t=3.0)
pump(0.2)
ctl.set_selection([t3.id])
ctl.paste_style("colors")
check(ctl.project.find_segment(t3.id)[1].parts[0].text.bubble_fill == "#e8d8a0", "Paste Colors from the UI")
ctl.undo()
check(ctl.project.find_segment(t3.id)[1].parts[0].text.bubble_fill == "#ffffff", "...undoable")

# Clean Up from the toolbar
ctl.perform("spread", lambda p: ops.add_track(p, 1))
ctl.perform("spread", lambda p: ops.add_track(p, 1))
n_before = len(ctl.project.tracks)
QTest.mouseClick(page.cleanup_btn, Qt.LeftButton)
pump(0.3)
check(len(ctl.project.tracks) < n_before, f"Clean Up button: {n_before} -> {len(ctl.project.tracks)} tracks")
ctl.undo()
check(len(ctl.project.tracks) == n_before, "...undoable")
w.grab().save(f"{OUT}/r10_editor.png")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
