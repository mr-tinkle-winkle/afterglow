"""
Advanced Editor, driven through REAL mouse/keyboard events on the real
widgets (timeline, preview, properties, browser), plus a real Save render.
Needs an X display + window manager (for focus/shortcuts):

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_advanced_editor.py

Writes screenshots to $SHOTS (default /tmp/shots).
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="ae_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
MEDIA = tempfile.mkdtemp(prefix="ae_media_")
OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)


def ff(*a):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *a], check=True)


ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-f", "lavfi", "-i", "aevalsrc=0.5*sin(2*PI*440*t):s=48000",
   "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-g", "15", "-c:a", "aac", str(CLIPS / "clip one.mp4"))
TONE = os.path.join(MEDIA, "tone.wav")
ff("-f", "lavfi", "-i", "aevalsrc=0.4*sin(2*PI*330*t):s=48000:d=4", TONE)
PNG = os.path.join(MEDIA, "logo.png")
ff("-f", "lavfi", "-i", "color=c=0xff00ff:s=200x100:d=1", "-frames:v", "1", PNG)
GIF = os.path.join(MEDIA, "anim.gif")
ff("-f", "lavfi", "-i", "color=c=yellow:s=80x80:r=10:d=0.5", "-f", "lavfi", "-i", "color=c=cyan:s=80x80:r=10:d=0.5",
   "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", GIF)
IMPORT_SRC = os.path.join(MEDIA, "outside.mp4")
ff("-f", "lavfi", "-i", "testsrc=size=320x180:rate=30", "-f", "lavfi", "-i", "sine", "-t", "3",
   "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", IMPORT_SRC)

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

app = QApplication([])
from afterglow import library
from afterglow.gui.main_window import MainWindow
from afterglow.gui.advanced_editor import page as page_mod
from afterglow.nle import media, store

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


def dur(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", path]))


library.scan_and_ingest_new_videos()
v = library.list_videos()[0]
w = MainWindow()
w.resize(1700, 1000)
w.show()
w.activateWindow()
pump(1.5)
QCursor.setPos(4000, 4000)

# ---- entry point: previewer "Advanced Editor" button -------------------------
w._show_preview_overlay(v, None)
pump(2)
c = w._preview_overlay.content
check(c.advanced_edit_btn.isVisible(), "previewer has an Advanced Editor button")
QTest.mouseClick(c.advanced_edit_btn, Qt.LeftButton)
pump(2.5)
ed = w.editor_page
ctl = ed.ctl
view = ed.timeline.view
check(w._preview_overlay is None and w.stack.currentWidget() is ed and ed.current_video_id == v.id,
      "button closes the preview and opens the clip in the Editor")
p = ctl.project
check(p is not None and len(p.tracks) == 4 and abs(p.duration - 8) < 0.1,
      f"new project: 4 tracks, the clip on track 1 ({p.duration:.2f}s)")

# ---- layout sanity (real geometry) -----------------------------------------------
pg = ed.geometry()
cv = ed.preview.canvas.geometry()
check(cv.width() > pg.width() * 0.35 and cv.height() > pg.height() * 0.3,
      f"preview canvas gets real space ({cv.width()}x{cv.height()} of {pg.width()}x{pg.height()})")
check(view.height() > pg.height() * 0.25, f"timeline gets real space ({view.height()}px)")
w.grab().save(f"{OUT}/ae_open.png")


def seg_center(seg, frac_y=0.72, frac_x=0.4):
    t, s = p.find_segment(seg.id)
    r = view.seg_rect(p.track_index(t.id), s)
    return QPoint(int(r.left() + r.width() * frac_x), int(r.top() + r.height() * frac_y))


def F(seg):
    """Fresh object for a segment (drags rebuild the project's objects)."""
    return p.find_segment(seg.id)[1]


def x_of(t):
    return int(round(view.t_to_x(t)))


def drag(widget, a, b, steps=12, button=Qt.LeftButton, mods=Qt.NoModifier):
    QTest.mousePress(widget, button, mods, a)
    pump(0.02)
    for i in range(1, steps + 1):
        QTest.mouseMove(widget, QPoint(a.x() + (b.x() - a.x()) * i // steps, a.y() + (b.y() - a.y()) * i // steps))
        pump(0.01)
    QTest.mouseRelease(widget, button, mods, b)
    pump(0.15)


def key(k, mods=Qt.NoModifier, target=None):
    QTest.keyClick(target or view, k, mods)
    pump(0.15)


seg = p.tracks[1].segments[0]
# ---- selection + ruler playhead ------------------------------------------------
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(seg))
pump(0.1)
check(ctl.selection == [seg.id], "click selects the segment")
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(x_of(3.0), 15))
pump(0.1)
check(abs(ctl.playhead - 3.0) < 0.05, f"click on the ruler moves the playhead ({ctl.playhead:.3f})")

# ---- S split -----------------------------------------------------------------------
ctl.set_playhead(3.0)   # exact (a ruler click lands within a pixel of it)
key(Qt.Key_S)
segs = sorted(p.tracks[1].segments, key=lambda s: s.start)
check(len(segs) == 2 and abs(segs[1].start - 3.0) < 0.05, "S splits at the playhead")
left, right = segs
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(right))
pump(0.1)

# ---- drag move (with snapping off via Alt for an exact 1s move) ----------------------
a = seg_center(right)
drag(view, a, QPoint(a.x() + int(view.pps * 1.0), a.y()), mods=Qt.AltModifier)
_, right = p.find_segment(right.id)
check(abs(right.start - 4.0) < 0.06, f"dragging moves the segment ({right.start:.3f})")
check(ctl.history.undo_label() == "Move", "the whole drag is ONE undo step")

# ---- gap trash can -----------------------------------------------------------------------
mid = QPoint(x_of(3.5), a.y())
QTest.mouseMove(view, mid)
pump(0.2)
check(view._hover is not None and view._hover["kind"] == "gap", "hovering the gap middle shows the trash can")
w.grab().save(f"{OUT}/ae_gap.png")
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, mid)
pump(0.2)
_, right = p.find_segment(right.id)
check(abs(right.start - 3.0) < 1e-3, f"clicking the trash can closes the gap ({right.start:.3f})")

# ---- edge trim with snapping to the playhead -----------------------------------------------
ctl.set_playhead(6.0)
pump(0.1)
r = view.seg_rect(1, right)
edge = QPoint(int(r.right()) - 1, int(r.top() + r.height() * 0.3))
QTest.mouseMove(view, edge)
pump(0.1)
check(view._hover and view._hover.get("zone") == "right", "right edge is a trim handle")
drag(view, edge, QPoint(x_of(6.0) + 4, edge.y()))
_, right = p.find_segment(right.id)
check(abs(right.end - 6.0) < 1e-3, f"trimming the end snaps to the playhead ({right.end:.3f})")

# ---- volume line: drag + type an exact percent ------------------------------------------------
r = view.seg_rect(1, right)
vy = int(round(view._volume_y(right, r)))
vx = int(r.left() + r.width() * 0.6)
QTest.mouseMove(view, QPoint(vx, vy))
pump(0.1)
check(view._hover and view._hover.get("zone") == "volume", "the volume line is grabbable")
QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(vx, vy))
for i in range(1, 6):
    QTest.mouseMove(view, QPoint(vx, vy - i * 4))
    pump(0.02)
_, right = p.find_segment(right.id)
check(right.volume > 1.05, f"dragging the line up raises the volume ({right.volume:.2f})")
for ch in "150":
    QTest.keyClick(view, ch)
    pump(0.02)
w.grab().save(f"{OUT}/ae_volume.png")
QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(vx, vy - 20))
pump(0.1)
_, right = p.find_segment(right.id)
check(abs(right.volume - 1.5) < 1e-6, f"typing 150 while holding sets exactly 150% ({right.volume})")

# ---- undo / redo ------------------------------------------------------------------------------
key(Qt.Key_Z, Qt.ControlModifier)
_, right = p.find_segment(right.id)
check(abs(right.volume - 1.0) < 1e-6, "Ctrl+Z undoes the volume change (one step)")
key(Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
_, right = p.find_segment(right.id)
check(abs(right.volume - 1.5) < 1e-6, "Ctrl+Shift+Z redoes it")

# ---- drop onto the bottom buffer track creates a new track ------------------------------------------
n_before = len(p.tracks)
a = seg_center(right)
bottom_i = len(p.tracks) - 1
by = int(view.track_top(bottom_i) + view.track_height(bottom_i) / 2)
drag(view, a, QPoint(a.x(), by), mods=Qt.AltModifier)
t_of_right, right = p.find_segment(right.id)
ti = p.track_index(t_of_right.id)
check(len(p.tracks) == n_before + 1 and ti == len(p.tracks) - 2 and p.tracks[-1].is_empty(),
      f"dropping on the bottom buffer track makes a new track ({n_before} -> {len(p.tracks)}, on {ti})")
check(abs(right.start - 3.0) < 1e-3, "a straight vertical drag keeps the time")

# ---- ctrl-select + combine (inclusive merge across tracks) ---------------------------------------------
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(left))
QTest.mouseClick(view, Qt.LeftButton, Qt.ControlModifier, seg_center(right))
pump(0.1)
check(set(ctl.selection) == {left.id, right.id}, "Ctrl+click adds to the selection")
key(Qt.Key_C)
check(len(ctl.selection) == 1 and len(list(p.all_segments())) == 1, "C combines the two touching segments")
merged = ctl.selected_segments()[0]
check(len(merged.split_markers()) == 1 and abs(merged.split_markers()[0] - 3.0) < 1e-3,
      "the combined segment shows a split marker at the join")
mk = view.seg_rect(p.track_index(p.find_segment(merged.id)[0].id), merged)
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(x_of(3.0), int(mk.top() + mk.height() * 0.3)))
pump(0.1)
check(abs(ctl.playhead - 3.0) < 1e-3, "clicking the split marker moves the playhead there")

# ---- lock (L) blocks moves ---------------------------------------------------------------------------------
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(merged, 0.5))
key(Qt.Key_L)
merged = F(merged)
check(merged.locked, "L locks")
st = merged.start
a = seg_center(merged, 0.5)
drag(view, a, QPoint(a.x() + 150, a.y()), mods=Qt.AltModifier)
merged = F(merged)
check(abs(merged.start - st) < 1e-6, "a locked segment can't be dragged")
w.grab().save(f"{OUT}/ae_locked.png")
key(Qt.Key_L)
merged = F(merged)
check(not merged.locked, "L again unlocks")

# ---- shift-range selection across tracks ----------------------------------------------------------------------
ed.browser._group.button(1).click()
pump(0.2)
from afterglow.gui.advanced_editor.browser import _DragList
tl = ed.browser.stack.widget(1).findChild(_DragList)
ctl.set_playhead(1.0)
item = tl.item(0)
rect = tl.visualItemRect(item)
QTest.mouseClick(tl.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
QTest.mouseDClick(tl.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
pump(0.3)
texts = [s for s in p.all_segments() if any(pt.kind == "text" for pt in s.parts)]
check(len(texts) == 1 and abs(texts[0].start - 1.0) < 1e-6, "double-clicking a Text style adds it at the playhead")
txt = texts[0]
ttrack = p.track_index(p.find_segment(txt.id)[0].id)
mtrack = p.track_index(p.find_segment(merged.id)[0].id)
check(ttrack < mtrack, "text lands above the video (drawn on top)")
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(txt))
QTest.mouseClick(view, Qt.LeftButton, Qt.ShiftModifier, seg_center(merged))
pump(0.1)
check(set(ctl.selection) == {txt.id, merged.id}, "Shift+click selects the range across tracks")

# ---- properties panel edits ------------------------------------------------------------------------------------
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(txt))
pump(0.2)
props = ed.properties
te = props._fields.get("text")
check(te is not None, "properties show a text editor for a text segment")
te.setFocus()
te.selectAll()
QTest.keyClicks(te, "GG EZ")
pump(0.9)
txt = F(txt)
check(txt.parts[0].text.text == "GG EZ" and txt.name == "GG EZ", "typing in Properties changes the text (and its name)")
check(ctl.history.undo_label() == "Edit text", "a burst of typing is one undo step")
view.setFocus()

# ---- transform by dragging in the preview ------------------------------------------------------------------------
canvas = ed.preview.canvas
ctl.set_playhead(2.0)
pump(0.3)
geo = canvas.seg_geometry(txt)
tr, box, _k = geo
cpt = tr.map(box.center()).toPoint()
QTest.mouseMove(canvas, cpt)
drag(canvas, cpt, QPoint(cpt.x() + 60, cpt.y() + 30))
txt = F(txt)
check(txt.transform.x > 0.02 and txt.transform.y > 0.01, f"dragging the text in the preview moves it "
      f"(x={txt.transform.x:.3f}, y={txt.transform.y:.3f})")
geo = canvas.seg_geometry(txt)
tr, box, _k = geo
corner = tr.map(box.bottomRight()).toPoint()
before = txt.transform.scale
drag(canvas, corner, QPoint(corner.x() + 40, corner.y() + 20))
txt = F(txt)
check(txt.transform.scale > before * 1.05, f"dragging a corner scales it ({before:.2f} -> {txt.transform.scale:.2f})")
w.grab().save(f"{OUT}/ae_text.png")

# ---- keyframes -------------------------------------------------------------------------------------------------------
props._fields["kf_prop"].setCurrentIndex(props._fields["kf_prop"].findData("opacity"))
ctl.set_playhead(1.0)
props._add_keyframe()
ctl.set_playhead(3.0)
pump(0.1)
props._fields["opacity"].setValue(20)
pump(0.9)
kfs = F(txt).keyframes.get("opacity", [])
check(len(kfs) == 2 and abs(kfs[1].value - 0.2) < 1e-6, f"keyframes: add one, then changing the value elsewhere adds a second ({[(round(k.t,2), k.value) for k in kfs]})")

# ---- transition via properties ---------------------------------------------------------------------------------------
# split the merged clip so there's a back-to-back pair, then give the right half a crossfade
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(merged, 0.5))
ctl.set_playhead(4.5)
key(Qt.Key_S)
halves = sorted(p.find_segment(merged.id)[0].segments, key=lambda s: s.start)
merged = F(merged)
check(len(halves) == 2, "split again for a transition pair")
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(halves[1]))
pump(0.2)
kind = props._fields["t_kind"]
kind.setCurrentIndex(kind.findData("crossfade"))
pump(0.2)
h1 = F(halves[1])
check(h1.transition_in is not None and h1.transition_in.kind == "crossfade",
      "choosing Crossfade in Properties adds the transition")
check(ctl.selection == [h1.id], "clicking one half of a split selection selects just it")
w.grab().save(f"{OUT}/ae_transition.png")

# ---- more editing: ripple delete, copy/paste, duplicate, rubber band, wheel, tracks ----------------
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(halves[0]))
pump(0.1)
h0_end = F(halves[0]).end
key(Qt.Key_C, Qt.ControlModifier)
ctl.set_playhead(20.0)
key(Qt.Key_V, Qt.ControlModifier)
pasted = ctl.selected_segments()
check(len(pasted) == 1 and abs(pasted[0].start - 20.0) < 1e-6 and pasted[0].id != halves[0].id,
      "Ctrl+C then Ctrl+V pastes a copy at the playhead")
key(Qt.Key_D, Qt.ControlModifier)
dup = ctl.selected_segments()
check(len(dup) == 1 and abs(dup[0].start - (20.0 + F(halves[0]).duration)) < 1e-6, "Ctrl+D duplicates right after")
key(Qt.Key_Delete)
check(p.find_segment(dup[0].id)[1] is None, "Delete removes the selection")
# ripple: delete the first half, the second slides left to fill
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, seg_center(halves[0]))
second_start = F(halves[1]).start
key(Qt.Key_Delete, Qt.ShiftModifier)
check(p.find_segment(halves[0].id)[1] is None and abs(F(halves[1]).start - (second_start - h0_end)) < 1e-6,
      f"Shift+Delete ripple-deletes (the next segment slides left to {F(halves[1]).start:.2f})")
key(Qt.Key_Z, Qt.ControlModifier)
check(p.find_segment(halves[0].id)[1] is not None, "undo brings it back")
# rubber band on empty lane area selects what it touches
view.set_scroll_t(0)
ti_ = p.track_index(p.find_segment(halves[1].id)[0].id)
y_ = int(view.track_top(ti_) + view.track_height(ti_) * 0.85)
empty_y = int(view.track_top(len(p.tracks) - 1) + 10)
drag(view, QPoint(x_of(7.5), empty_y), QPoint(x_of(0.2), y_))
check(set(ctl.selection) >= {halves[0].id, halves[1].id}, "dragging a box on empty space selects the segments it touches")
# wheel: plain = one frame, ctrl = zoom
ctl.set_playhead(2.0)
QTest.mouseMove(view, QPoint(x_of(3.0), empty_y))
from PySide6.QtGui import QWheelEvent
from PySide6.QtCore import QPointF as _QPF
def wheel(dy, mods=Qt.NoModifier, pos=None):
    pos = pos or _QPF(x_of(3.0), empty_y)
    ev = QWheelEvent(pos, view.mapToGlobal(pos), QPoint(0, 0), QPoint(0, dy), Qt.NoButton, mods, Qt.NoScrollPhase, False)
    QApplication.sendEvent(view, ev)
    pump(0.05)
st0 = view.scroll_t
wheel(-120)
check(view.scroll_t > st0 and abs(ctl.playhead - 2.0) < 1e-9, f"plain wheel scrolls along the timeline ({st0:.2f} -> {view.scroll_t:.2f}s)")
wheel(120, Qt.ShiftModifier)
check(abs(ctl.playhead - (2.0 + 1 / p.fps)) < 1e-6, f"Shift+wheel steps the playhead one frame ({ctl.playhead:.4f})")
wheel(-120, Qt.ShiftModifier | Qt.ControlModifier)
check(abs(ctl.playhead - (1.0 + 1 / p.fps)) < 1e-6, "Ctrl+Shift+wheel steps one second")
pps0 = view.pps
wheel(120, Qt.ControlModifier)
check(view.pps > pps0 * 1.2, f"Ctrl+wheel zooms in ({pps0:.1f} -> {view.pps:.1f} px/s)")
view.zoom_to_fit()
pump(0.1)
# track header: collapse + drag to reorder
hr = view.header_rect()
ti_ = p.track_index(p.find_segment(halves[1].id)[0].id)
tid = p.tracks[ti_].id
cy = int(view.track_top(ti_) + 18)
chev_x = int(hr.left() + 34)
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(chev_x, cy))
pump(0.1)
check(p.tracks[p.track_index(tid)].collapsed and view.track_height(p.track_index(tid)) < 30,
      "clicking the header arrow collapses the track")
w.grab().save(f"{OUT}/ae_collapsed.png")
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(chev_x, int(view.track_top(p.track_index(tid)) + 12)))
pump(0.1)
check(not p.tracks[p.track_index(tid)].collapsed, "... and again expands it")
before_i = p.track_index(tid)
hx = int(hr.left() + 14)
ty = int(view.track_top(before_i) + view.track_height(before_i) / 2)
target_i = before_i + 1
drag(view, QPoint(hx, ty), QPoint(hx, int(view.track_top(target_i) + view.track_height(target_i) / 2)))
check(p.track_index(tid) != before_i, f"dragging the 3-line handle reorders tracks ({before_i} -> {p.track_index(tid)})")
key(Qt.Key_Z, Qt.ControlModifier)
check(p.track_index(tid) == before_i, "undo restores the track order")
# context menu contents (the real menu, captured instead of shown)
pos_ = seg_center(halves[1])
menu = view.build_context_menu(QPointF(pos_))
labels = [act.text().split("\t")[0] for act in menu.actions() if act.text()]
check(all(x in labels for x in ("Split at Playhead", "Lock", "Mute", "Hide", "Copy", "Duplicate", "Delete",
                                "Ripple Delete", "Transition In")), f"segment context menu has the actions ({labels})")


# ---- playback --------------------------------------------------------------------------------------------------------
ctl.set_playhead(0.5)
view.setFocus()
key(Qt.Key_Space)
t0 = time.perf_counter()
pump(1.2)
elapsed = time.perf_counter() - t0
moved = ctl.playhead - 0.5
check(ed.preview.playing and 0.6 < moved < elapsed + 0.3, f"Space plays: playhead advanced {moved:.2f}s in {elapsed:.2f}s")
key(Qt.Key_Space)
check(not ed.preview.playing, "Space pauses")

# ---- audio added anywhere ----------------------------------------------------------------------------------------------
ctl.set_playhead(0.0)
aseg = ctl.add_file(TONE)
check(aseg is not None and not aseg.has_video and p.track_index(p.find_segment(aseg.id)[0].id) >= len(p.tracks) - 3,
      "an audio file lands on a lower (audio) track")
pump(1.0)
w.grab().save(f"{OUT}/ae_full.png")

# ---- pictures / GIFs: added over the video, visible in the preview, resizable ---------------------------
ctl.set_playhead(0.5)
pic = ctl.add_file(PNG, track_index=1)
check(pic is not None and pic.parts[0].kind == "image", "a picture is added as an image element")
pt_i = p.track_index(p.find_segment(pic.id)[0].id)
vid_i = min(p.track_index(t.id) for t in p.tracks if any(s_.parts[0].kind == "av" and s_.has_video for s_ in t.segments))
check(pt_i < vid_i, "the picture sits on a track above the video (drawn over it)")
ctl.set_selection([pic.id], anchor=pic.id)
ctl.set_playhead(1.0)
pump(0.4)
ed.preview._render_now()
img = ed.preview.canvas.image
cc = img.pixelColor(img.width() // 2, img.height() // 2)
check(cc.red() > 200 and cc.green() < 60 and cc.blue() > 200, f"the picture shows in the preview ({cc.name()})")
geo = canvas.seg_geometry(F(pic))
tr, box, _k = geo
corner = tr.map(box.topLeft()).toPoint()
c0 = tr.map(box.center()).toPoint()
drag(canvas, corner, QPoint(c0.x() - (c0.x() - corner.x()) // 2, c0.y() - (c0.y() - corner.y()) // 2))
check(F(pic).transform.scale < 0.7, f"dragging a corner in toward the center shrinks the picture ({F(pic).transform.scale:.2f})")
gif = ctl.add_file(GIF, t=3.0, track_index=1)
check(gif is not None and gif.parts[0].kind == "gif", "a GIF is added as an animated element")
ctl.perform("stretch", lambda pp: __import__("afterglow.nle.ops", fromlist=["ops"]).trim_end(pp, gif.id, 6.0))
check(abs(F(gif).end - 6.0) < 1e-6, "a GIF can be stretched past its length (it loops)")
pump(0.8)
view.update()
pump(0.3)
w.grab().save(f"{OUT}/ae_elements.png")

# ---- crop handles ---------------------------------------------------------------------------------------------
vseg = next(s_ for s_ in p.all_segments() if s_.parts[0].kind == "av" and s_.has_video and s_.covers(1.0))
ctl.set_selection([vseg.id], anchor=vseg.id)
ctl.set_playhead(1.0)
QTest.mouseClick(ed.preview.crop_btn, Qt.LeftButton)
pump(0.3)
geo = canvas.seg_geometry(F(vseg))
tr, box, _k = geo
lh = tr.map(QPointF(box.left(), box.center().y())).toPoint()
drag(canvas, lh, QPoint(lh.x() + 80, lh.y()))
check(F(vseg).transform.crop_left > 0.05, f"with Crop on, dragging the left edge crops ({F(vseg).transform.crop_left:.3f})")
w.grab().save(f"{OUT}/ae_crop.png")
QTest.mouseClick(ed.preview.crop_btn, Qt.LeftButton)
key(Qt.Key_Z, Qt.ControlModifier)
check(F(vseg).transform.crop_left == 0.0, "undo removes the crop")

# ---- drag and drop: files from outside, transitions from the browser ------------------------------------------
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
import json as _json
from afterglow.gui.advanced_editor.timeline import MIME_ITEM


def drop(mime, pt):
    ptf = QPointF(pt)
    for cls in (QDragEnterEvent, QDragMoveEvent):
        ev = cls(pt, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(view, ev)
    ev = QDropEvent(ptf, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    QApplication.sendEvent(view, ev)
    pump(0.3)


n0 = len(list(p.all_segments()))
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(TONE)])
last_i = len(p.tracks) - 1
drop(md, QPoint(x_of(9.0), int(view.track_top(last_i) + 10)))
dropped = [s_ for s_ in p.all_segments() if abs(s_.start - 9.0) < 0.05 and s_.name == "tone.wav"]
check(len(list(p.all_segments())) == n0 + 1 and dropped, "dropping a file from outside adds it where it's dropped")
check(len(p.tracks) >= 5 and p.tracks[-1].is_empty(), "... dropping on the bottom buffer track created a new track below")
target = F(halves[0])
md = QMimeData()
md.setData(MIME_ITEM, _json.dumps({"type": "transition", "kind": "slide", "duration": 0.4, "target": "both",
                                   "direction": "right"}).encode())
drop(md, seg_center(target))
tg = F(target)
check(tg.transition_in is not None and tg.transition_in.kind == "slide" and tg.transition_in.direction == "right",
      "dropping a transition from the browser onto a segment applies it")

# ---- header on the right (Settings > General > Editor) ------------------------------------------------------------
view.header_side = "right"
view._invalidate()
pump(0.2)
hr = view.header_rect()
check(hr.right() >= view.width() - 1, "track headers can sit on the right")
w.grab().save(f"{OUT}/ae_headers_right.png")
view.header_side = "left"
view._invalidate()
pump(0.1)

# ---- autosave + unsaved marker ------------------------------------------------------------------------------------------
ctl.autosave_now()
proj_file = store.project_path_for_video(v.id)
check(proj_file.exists() and ed.unsaved_label.text() != "", "edits autosave to the project file; marker shows unsaved")

# ---- Save (real render) ------------------------------------------------------------------------------------------------
page_mod.SaveDialog.exec = lambda self: QDialog.Accepted
expect = p.duration
ed.save()
pump(0.5)
vid = library.get_video(v.id)
d = dur(str(vid.path))
check(abs(d - expect) < 0.15 and vid.has_edit and vid.backup_path, f"Save renders over the clip ({d:.2f}s vs {expect:.2f}s), backup kept")
check(ed.unsaved_label.text() == "", "saving clears the unsaved marker")
live = str(vid.path)
check(all(pt.source != live for s in p.all_segments() for pt in s.parts if pt.source.endswith(".mp4")),
      "after saving, the project reads from the stable backup, not the rendered file")
check(all(pt != live for step in ctl.history._undo for tr_ in step.before["tracks"]
          for sg in tr_["segments"] for pt in [q["source"] for q in sg["parts"]]),
      "... and so do the undo snapshots (undo after save can't double-apply edits)")

# ---- reopen: edits stay editable ----------------------------------------------------------------------------------------
n_segs = len(list(p.all_segments()))
w._open_in_editor(v.id)
pump(1.5)
check(len(list(ctl.project.all_segments())) == n_segs, "reopening the clip restores the editable timeline")

# ---- rename keeps the project pointing at the (renamed) file -------------------------------------------------------------
# ---- Import mode -----------------------------------------------------------------------------------------------------
ed.open_import(IMPORT_SRC)
pump(1.0)
check(ctl.mode == "import" and ctl.project.output_path.endswith("outside-edited.mp4"), "Import opens any file")
ed.save()
pump(0.3)
outp = os.path.join(MEDIA, "outside-edited.mp4")
check(os.path.exists(outp) and abs(dur(outp) - 3.0) < 0.15 and abs(dur(IMPORT_SRC) - 3.0) < 0.15,
      "saving an import writes <name>-edited next to it and leaves the original alone")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
