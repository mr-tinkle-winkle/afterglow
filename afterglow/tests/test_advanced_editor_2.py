"""
Advanced Editor, round 2 feedback: media drag, custom scroll bars, instant
playhead, subtitle placement, typewriter text, speech/thought bubbles,
gap line, double-click text editing, Save Separately, wheel-proof combo
boxes, the Position keyframe, and the Save dialog's sizing. Real events.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_advanced_editor_2.py
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="ae2_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)


def ff(*a):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *a], check=True)


for i in range(14):   # enough clips that the Media list scrolls
    ff("-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30", "-f", "lavfi", "-i", "sine", "-t", "4",
       "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(CLIPS / f"clip {i:02d}.mp4"))

from PySide6.QtCore import QPoint, QPointF, Qt, QMimeData
from PySide6.QtGui import QCursor, QWheelEvent, QDropEvent, QDragEnterEvent, QDragMoveEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

app = QApplication([])
from afterglow import library
from afterglow.gui.main_window import MainWindow
from afterglow.gui.advanced_editor import page as page_mod, browser as browser_mod
from afterglow.gui.custom_scrollbar import CustomScrollBar
from afterglow.nle import render as R

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
videos = sorted(library.list_videos(), key=lambda v: v.title)
v = videos[0]
w = MainWindow()
w.resize(1700, 1000)
w.show()
w.activateWindow()
pump(1.2)
QCursor.setPos(4000, 4000)
w._open_in_editor(v.id)
pump(2.5)
ed = w.editor_page
ctl = ed.ctl
view = ed.timeline.view
canvas = ed.preview.canvas
p = ctl.project


def F(seg):
    return p.find_segment(seg.id)[1]


def x_of(t):
    return int(round(view.t_to_x(t)))


def drag(widget, a, b, steps=10, mods=Qt.NoModifier):
    QTest.mousePress(widget, Qt.LeftButton, mods, a)
    pump(0.02)
    for i in range(1, steps + 1):
        QTest.mouseMove(widget, QPoint(a.x() + (b.x() - a.x()) * i // steps, a.y() + (b.y() - a.y()) * i // steps))
        pump(0.01)
    QTest.mouseRelease(widget, Qt.LeftButton, mods, b)
    pump(0.15)


# ---- 1 + 2: Media list: custom scroll bar, dragging a clip starts a drag (no scrolling) ---------------
ed.browser.load_library(force=True)
pump(1.5)
ml = ed.browser.media_list
check(isinstance(ml.verticalScrollBar(), CustomScrollBar), "Media list uses the app's own scroll bar")
check(all(isinstance(lst.verticalScrollBar(), CustomScrollBar) for lst in ed.browser.findChildren(browser_mod._DragList)),
      "... and so does every browser list")
check(ml.verticalScrollBar().maximum() > 0, "the Media list has enough clips to scroll")
started = {}
orig_exec = browser_mod.QDrag.exec


def fake_exec(self, *a, **k):
    started["mime"] = bytes(self.mimeData().data(browser_mod.MIME_ITEM)).decode()
    return Qt.CopyAction


browser_mod.QDrag.exec = fake_exec
ml.verticalScrollBar().setValue(0)
it = ml.item(1)
r = ml.visualItemRect(it)
a = r.center()
before_scroll = ml.verticalScrollBar().value()
drag(ml.viewport(), a, QPoint(a.x() + 10, a.y() + 160))
browser_mod.QDrag.exec = orig_exec
check("mime" in started and '"type": "file"' in started["mime"], f"dragging a clip starts a drag carrying the clip ({started.get('mime', '')[:60]})")
check(ml.verticalScrollBar().value() == before_scroll, "... and does NOT scroll the Media list")
# the payload the drag carries lands on the timeline
md = QMimeData()
md.setData(browser_mod.MIME_ITEM, started["mime"].encode())
drop_pt = QPoint(x_of(p.duration + 1.0), int(view.track_top(1) + view.track_height(1) / 2))
n0 = len(list(p.all_segments()))
for cls in (QDragEnterEvent, QDragMoveEvent):
    QApplication.sendEvent(view, cls(drop_pt, Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
QApplication.sendEvent(view, QDropEvent(QPointF(drop_pt), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
pump(0.3)
check(len(list(p.all_segments())) == n0 + 1, "... and dropping it on the timeline adds the clip")

# ---- 3: clicking an empty lane moves the playhead on PRESS -------------------------------------------------
view.zoom_to_fit()
pump(0.1)
empty_y = int(view.track_top(len(p.tracks) - 1) + 10)
QTest.mousePress(view, Qt.LeftButton, Qt.AltModifier, QPoint(x_of(2.5), empty_y))
pump(0.05)
at_press = ctl.playhead
QTest.mouseRelease(view, Qt.LeftButton, Qt.AltModifier, QPoint(x_of(2.5), empty_y))
check(abs(at_press - 2.5) < 0.05, f"the playhead is already under the cursor on mouse DOWN ({at_press:.3f})")

# ---- 4: subtitle placement --------------------------------------------------------------------------------
sub = ctl.add_text("Subtitle", t=0.0)
check(0.3 < F(sub).transform.y < 0.45, f"Subtitle defaults just above the bottom edge (y={F(sub).transform.y})")
ctl.set_playhead(1.0)
ctl.set_selection([sub.id], anchor=sub.id)
ed.preview._render_now()
pump(0.2)
geo = canvas.seg_geometry(F(sub))
trf, box, _k = geo
fr = canvas.frame_rect()
bottom = trf.mapRect(box).bottom()
check(fr.bottom() - fr.height() * 0.2 < bottom < fr.bottom(), "... and renders in the bottom fifth of the frame")

# ---- 5: typewriter in/out + cursor ---------------------------------------------------------------------------
from afterglow.nle.model import TextStyle
st = TextStyle(text="hello world", type_in=1.0, type_out=0.5, type_cursor=True)
check(R.typed_state(st, 0.0, 4.0)[0] == 0 and R.typed_state(st, 0.5, 4.0)[0] == 5 and R.typed_state(st, 2.0, 4.0)[0] == 11,
      "type in: 0 -> 5 -> all 11 characters")
check(R.typed_state(st, 3.75, 4.0)[0] == 6 and R.typed_state(st, 3.999, 4.0)[0] == 1, "type out deletes at the end")
check(R.typed_state(st, 0.5, 4.0)[1] and not all(R.typed_state(st, t, 4.0)[1] for t in (2.0, 2.5)),
      "the cursor shows while typing and blinks once typed")
props = ed.properties
props._fields["type_in"].setValue(2.0)
pump(0.9)
props._fields["type_cursor"].click()
pump(0.3)
check(F(sub).parts[0].text.type_in == 2.0 and F(sub).parts[0].text.type_cursor, "Properties > Typing sets type-in and the cursor")
rr = R.Renderer(p)
def text_pixels(t):
    img = rr.frame(t, 640, 360)
    n = 0
    for y in range(300, 360, 2):
        for x in range(0, 640, 2):
            c = img.pixelColor(x, y)
            if c.red() > 235 and c.green() > 235 and c.blue() > 235:
                n += 1
    return n
early, mid_, done = text_pixels(0.3), text_pixels(1.0), text_pixels(2.4)
check(early < mid_ < done, f"rendered subtitle grows as it types ({early} < {mid_} < {done} white px)")
rr.close()
w.grab().save(f"{OUT}/ae2_subtitle_typing.png")

# ---- 6: speech + thought bubbles ------------------------------------------------------------------------------
ctl.set_playhead(1.0)
sp = ctl.add_text("Speech bubble", t=0.5)
check(F(sp).parts[0].text.bubble == "speech", "Speech bubble preset")
ed.preview._render_now()
pump(0.3)
img = canvas.image
geo = canvas.seg_geometry(F(sp))
trf, box, _k = geo
cw_ = trf.map(box.center())
fr = canvas.frame_rect()
ix = int((cw_.x() - fr.left()) / fr.width() * img.width())
iy = int((box.top() * 0 + cw_.y() - fr.top()) / fr.height() * img.height())
edge_pt = trf.map(QPointF(box.left() + box.width() * 0.12, box.center().y()))
ex = int((edge_pt.x() - fr.left()) / fr.width() * img.width())
c_edge = img.pixelColor(ex, iy)
check(c_edge.red() > 230 and c_edge.green() > 230 and c_edge.blue() > 230, f"the bubble body is drawn (white fill) {c_edge.name()}")
tip = canvas.tail_point(F(sp))
check(tip is not None, "the preview shows a tail handle for the bubble")
bx0, by0 = F(sp).transform.x, F(sp).transform.y
tx0, ty0 = F(sp).parts[0].text.tail_x, F(sp).parts[0].text.tail_y
drag(canvas, tip.toPoint(), QPoint(int(tip.x()) - 80, int(tip.y()) + 30))
s2 = F(sp)
check(s2.parts[0].text.tail_x < tx0 - 0.03 and abs(s2.transform.x - bx0) < 1e-9 and abs(s2.transform.y - by0) < 1e-9,
      f"dragging the tail moves only the tail tip ({tx0:.3f} -> {s2.parts[0].text.tail_x:.3f}); the bubble stays")
w.grab().save(f"{OUT}/ae2_speech.png")
# tail keyframes: add, move playhead, drag again -> second key
props._fields["kf_prop"].setCurrentIndex(props._fields["kf_prop"].findData("tail"))
props._add_keyframe()
ctl.set_playhead(2.5)
pump(0.2)
tip = canvas.tail_point(F(sp))
drag(canvas, tip.toPoint(), QPoint(int(tip.x()) + 120, int(tip.y())))
kx = F(sp).keyframes.get("tail_x", [])
check(len(kx) == 2 and len(F(sp).keyframes.get("tail_y", [])) == 2, "the tail tip is keyframable (two keys after moving it later)")
ctl.set_playhead(1.0)
t1 = canvas.tail_point(F(sp))
ctl.set_playhead(2.5)
t2 = canvas.tail_point(F(sp))
check(t2.x() - t1.x() > 60, "the tail animates between its keyframes")
th = ctl.add_text("Thought bubble", t=4.5)
ctl.set_playhead(5.0)
ed.preview._render_now()
pump(0.3)
check(F(th).parts[0].text.bubble == "thought", "Thought bubble preset")
w.grab().save(f"{OUT}/ae2_thought.png")
props._fields  # rebuilt for the thought bubble
kinds = [props._fields["bubble"].itemData(i) for i in range(props._fields["bubble"].count())]
check(kinds == ["", "speech", "thought"], "Properties > Bubble switches between none / speech / thought")

# ---- 8: double-click text in the preview edits it in place -----------------------------------------------------
ctl.set_playhead(5.0)
pump(0.2)
geo = canvas.seg_geometry(F(th))
trf, box, _k = geo
cpt = trf.map(box.center()).toPoint()
QTest.mouseDClick(canvas, Qt.LeftButton, Qt.NoModifier, cpt)
pump(0.3)
check(canvas._editing is not None and canvas._editing[1].isVisible(), "double-clicking text opens an editor on it")
edt = canvas._editing[1]
edt.selectAll()
QTest.keyClicks(edt, "so tired")
QTest.keyClick(edt, Qt.Key_S)          # would be Split if the page shortcut stole it
w.grab().save(f"{OUT}/ae2_inline_edit.png")
QTest.keyClick(edt, Qt.Key_Return)
pump(0.2)
check(F(th).parts[0].text.text == "so tireds" and canvas._editing is None, f"Enter commits the typed text ({F(th).parts[0].text.text!r})")

# ---- 11: Position keyframe -----------------------------------------------------------------------------------
ctl.set_selection([sub.id], anchor=sub.id)
pump(0.2)
kc = props._fields["kf_prop"]
check(kc.itemData(0) == "position", "Keyframes list offers Position (X + Y)")
kc.setCurrentIndex(0)
ctl.set_playhead(0.5)
props._add_keyframe()
pump(0.2)
s3 = F(sub)
check(len(s3.keyframes.get("x", [])) == 1 and len(s3.keyframes.get("y", [])) == 1, "adding a Position key sets X and Y together")
ctl.set_playhead(3.0)
geo = canvas.seg_geometry(F(sub))
trf, box, _k = geo
c0 = trf.map(box.center()).toPoint()
drag(canvas, c0, QPoint(c0.x() - 100, c0.y() - 60))
s3 = F(sub)
check(len(s3.keyframes["x"]) == 2 and len(s3.keyframes["y"]) == 2, "moving it later adds the second Position key")
rows = props._fields["kf_list"].findChildren(page_mod.QLabel)
check(any("%, " in lab.text() for lab in rows), "the Position key list shows X and Y together")

# ---- 10: the wheel never changes a combo box ----------------------------------------------------------------------
combo = props._fields["t_kind"]
idx = combo.currentIndex()
pos = QPointF(combo.width() / 2, combo.height() / 2)
for dy in (-120, -120, 120):
    QApplication.sendEvent(combo, QWheelEvent(pos, combo.mapToGlobal(pos), QPoint(), QPoint(0, dy), Qt.NoButton,
                                              Qt.NoModifier, Qt.NoScrollPhase, False))
    pump(0.05)
check(combo.currentIndex() == idx, "scrolling over a selection box doesn't change it")
sb = props.scroll.verticalScrollBar()
sb.setValue(0)
pump(0.1)
QApplication.sendEvent(combo, QWheelEvent(pos, combo.mapToGlobal(pos), QPoint(), QPoint(0, -360), Qt.NoButton,
                                          Qt.NoModifier, Qt.NoScrollPhase, False))
pump(0.5)
check(sb.value() > 0, f"... the page behind it scrolls instead ({sb.value()})")
settings_combo = w.settings_page.startup_window_mode_combo
i0 = settings_combo.currentIndex()
QApplication.sendEvent(settings_combo, QWheelEvent(QPointF(5, 5), settings_combo.mapToGlobal(QPoint(5, 5)), QPoint(),
                                                   QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
check(settings_combo.currentIndex() == i0, "... everywhere in the app (Settings too)")

# ---- 7: gap line (visual) --------------------------------------------------------------------------------------
view.zoom_to_fit()
pump(0.3)
w.grab().save(f"{OUT}/ae2_gaps.png")

# ---- 12 + 9: Save dialog sizing, Save Separately -------------------------------------------------------------------
dlg = page_mod.SaveDialog(p, "", ed, library_clip=True)
dlg.show()
pump(0.3)
ok_sizes = all(c.width() >= c.fontMetrics().horizontalAdvance(c.itemText(i)) + 30
               for c in (dlg.res, dlg.quality) for i in range(c.count()))
check(ok_sizes, f"Save dialog boxes fit their longest option ({dlg.res.width()}px, {dlg.quality.width()}px)")
dlg.grab().save(f"{OUT}/ae2_save_dialog.png")
check(dlg.separate_radio is not None and dlg.replace_radio.isChecked(), "Save dialog offers Replace / Save separately")
dlg.close()

orig_path = str(library.get_video(v.id).path)
orig_dur = dur(orig_path)
orig_mtime = os.stat(orig_path).st_mtime_ns
n_before = len(library.list_videos())


def fake_dialog_exec(self):
    self.separate_radio.setChecked(True)
    return QDialog.Accepted


page_mod.SaveDialog.exec = fake_dialog_exec
expect = p.duration
ed.save()
pump(0.5)
vids = library.list_videos()
new = [x for x in vids if x.title == f"{v.title} (edited)"]
check(len(vids) == n_before + 1 and len(new) == 1, "Save Separately adds a new clip to the Library")
check(new and abs(dur(str(new[0].path)) - expect) < 0.15 and new[0].has_edit,
      f"... with the edit rendered into it ({dur(str(new[0].path)) if new else 0:.2f}s vs {expect:.2f}s)")
check(os.stat(orig_path).st_mtime_ns == orig_mtime and abs(dur(orig_path) - orig_dur) < 0.01
      and not library.get_video(v.id).has_edit, "... and the original clip is untouched")
check(ed.unsaved_label.text() == "", "the unsaved marker clears")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
