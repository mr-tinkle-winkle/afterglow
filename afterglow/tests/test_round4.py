"""
Round 4 feedback: previewer window not re-created on first open, previewer
keeps the keyboard after fullscreen, arrow keys (seek 5 s / volume 5 %),
bundled fonts + a real dropdown, text effects clamped to the element,
text effects overlapping the grow, per-word Delay keys, thought cloud
forming without a center oval.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_round4.py
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="r4_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine", "-t", "20", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                "-c:a", "aac", str(CLIPS / "twenty.mp4")], check=True)

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QCursor, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication([])
from afterglow import library
from afterglow.gui.main_window import MainWindow
from afterglow.gui import fonts as fonts_mod
from afterglow.nle import render as R, ops
from afterglow.nle.model import TextStyle

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


library.scan_and_ingest_new_videos()
v = library.list_videos()[0]
w = MainWindow()
events = []


class Spy(QObject):
    def eventFilter(self, o, e):
        if o is w and e.type() in (QEvent.Hide, QEvent.WinIdChange):
            events.append(e.type().name)
        return False


spy = Spy()
w.installEventFilter(spy)
w.resize(1600, 950)
w.show()
w.activateWindow()
pump(1.5)
QCursor.setPos(4000, 4000)

# ---- 1: opening the previewer the first time doesn't re-create the window -------------------------------
wid = w.winId()
events.clear()
w._show_preview_overlay(v, None)
pump(2.5)
check(w.winId() == wid and not events, f"first preview open keeps the same native window (events: {events})")
ov = w._preview_overlay
c = ov.content

# ---- 3: arrow keys --------------------------------------------------------------------------------------
c.video_widget.pause()
pump(0.3)
c._on_trim_seek_requested(3.0)
pump(0.4)
QTest.keyClick(ov, Qt.Key_Right)
pump(0.5)
check(abs(c._current_pos - 8.0) < 0.3, f"Right jumps 5 s forward ({c._current_pos:.2f})")
QTest.keyClick(ov, Qt.Key_Left)
pump(0.5)
check(abs(c._current_pos - 3.0) < 0.3, f"Left jumps 5 s back ({c._current_pos:.2f})")
vol = c.volume_slider.value()
QTest.keyClick(ov, Qt.Key_Down)
pump(0.1)
check(c.volume_slider.value() == vol - 5, f"Down lowers the volume 5 % ({vol} -> {c.volume_slider.value()})")
QTest.keyClick(ov, Qt.Key_Up)
pump(0.1)
check(c.volume_slider.value() == vol, "Up raises it 5 %")

# ---- 2: after leaving fullscreen, keys still drive the previewer ------------------------------------------
QTest.mouseClick(c.fullscreen_btn, Qt.LeftButton)
pump(1.5)
QTest.keyClick(ov, Qt.Key_Escape)
pump(1.5)
check(w._preview_overlay is ov and not c._is_expanded, "Esc left fullscreen, preview still open")
# whatever has focus now (even something on the page behind), keys go to the preview
w.library_page.setFocus()
pump(0.1)
target = QApplication.focusWidget() or w
paused_before = c.video_widget.is_paused
QTest.keyClick(target, Qt.Key_Space)
pump(0.4)
check(c.video_widget.is_paused != paused_before, "Space still plays/pauses the preview (not the page behind)")
pos0 = c._current_pos
QTest.keyClick(target, Qt.Key_Right)
pump(0.5)
check(c._current_pos > pos0 + 3, f"arrow keys still drive the preview ({pos0:.2f} -> {c._current_pos:.2f})")
c.video_widget.pause()
QTest.keyClick(ov, Qt.Key_Escape)
pump(0.6)
check(w._preview_overlay is None, "Esc again closes the preview")

# ---- 4: bundled fonts + dropdown ---------------------------------------------------------------------------
fams = set(QFontDatabase.families())
bundled = [f for _c, f in fonts_mod.FONT_CHOICES]
check(all(f in fams for f in bundled), f"all {len(bundled)} bundled fonts are registered")
w._open_in_editor(v.id)
pump(2.0)
ed = w.editor_page
ctl = ed.ctl
props = ed.properties
sp = ctl.add_text("Speech bubble", t=1.0)
pump(0.4)
check(sp.parts[0].text.font_family == "Permanent Marker", "bubbles default to the bundled Permanent Marker")
fc = props._fields["font"]
check(not fc.isEditable(), "the font picker is a plain dropdown (no typing needed)")
items = [fc.itemData(i) for i in range(fc.count()) if fc.itemData(i)]
check(set(items) == set(bundled) and len(items) == len(bundled), f"it offers exactly the {len(bundled)} bundled fonts")
check(fc.itemData(fc.currentIndex()) == "Permanent Marker", "and shows the current font")
check(all(fc.itemData(i, Qt.FontRole) is not None for i in range(fc.count()) if fc.itemData(i)),
      "each entry is drawn in its own typeface")
i = fc.findData("Tinos")
fc.setCurrentIndex(i)
fc.activated.emit(i)
pump(0.3)
check(ctl.project.find_segment(sp.id)[1].parts[0].text.font_family == "Tinos", "picking Tinos (Times New Roman) applies it")
w.grab().save(f"{OUT}/r4_fonts.png")
fc.showPopup()
pump(0.4)
popup = fc.view().window()
popup.grab().save(f"{OUT}/r4_font_dropdown.png")
fc.hidePopup()

# ---- 5: effects fitted into the element's own time ------------------------------------------------------------
st = TextStyle(text="hey there", bubble="speech", grow_in=0.35, grow_out=0.3, delay_in=0.6)
tm = R.text_timing(st, 0.4)
check(abs(tm["gi"] + tm["go"] - 0.4) < 1e-9, f"grow in+out squeezed into a 0.4 s element ({tm['gi']:.3f}+{tm['go']:.3f})")
check(R.grow_progress(st, 0.4, 0.4) == 0.0 and R.grow_progress(st, 5.0, 0.4) == 0.0,
      "at (and past) the element's end the bubble is fully shrunk")
check(abs(R.grow_progress(st, 0.2, 4.0) - 0.2 / 0.35) < 1e-9, "normal-length element: grow timing unchanged")
# rendered: the frame just before the end has almost nothing left
from afterglow.nle.model import default_project, Part, Segment, KIND_TEXT
pp = default_project(640, 360, 30)
seg = Segment(parts=[Part(kind=KIND_TEXT, src_in=0, src_out=2.0, has_audio=False, text=TextStyle(
    text="bye", bubble="speech", grow_in=0.35, grow_out=0.3, color="#111111"))])
ops.place(pp, seg, 1, 0.0)
rr = R.Renderer(pp)


def whites(img):
    n = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            cc = img.pixelColor(x, y)
            if cc.red() > 230 and cc.green() > 230 and cc.blue() > 230:
                n += 1
    return n


full, last = whites(rr.frame(1.0, 640, 360)), whites(rr.frame(2.0 - 1 / 30 + 1e-5, 640, 360))
check(last < full * 0.1, f"the last frame of the element shows the bubble all but gone ({full} -> {last} px)")
rr.close()

# ---- 6: text effects overlap the latter part of the grow ------------------------------------------------------
st = TextStyle(text="hello world", bubble="speech", grow_in=0.4, grow_out=0.4, type_in=0.5)
shown, _ = R.typed_state(st, 0.35, 5.0)
check(shown > 0 and R.grow_progress(st, 0.35, 5.0) < 1.0, f"typing is already under way while the bubble is still growing ({shown} chars)")
st = TextStyle(text="hello world", bubble="speech", grow_in=0.4, grow_out=0.4, delay_in=0.5)
a = R.delay_alphas(st, 0.35, 5.0)
check(a[0] > 0 and R.grow_progress(st, 0.35, 5.0) < 1.0, "Delay starts during the grow too")
st.delay_out = 0.4
a = R.delay_alphas(st, 5.0 - 0.3, 5.0)
check(R.grow_progress(st, 5.0 - 0.3, 5.0) < 1.0 and min(a) < 1.0 and max(a) > 0.0, "Delay out is still running as the shrink begins")

# ---- 7: per-word delay keys ---------------------------------------------------------------------------------------
st = TextStyle(text="one two three four five", delay_keyed=True, delay_word_times=[None, 1.0, None, None, 4.0])
starts = [t for t, _w in R.word_start_times(st, 6.0)]
check(abs(starts[1] - 1.0) < 1e-9 and abs(starts[4] - 4.0) < 1e-9, "keyed words start at their keys")
check(abs(starts[0] - 0.0) < 1e-9 and abs(starts[2] - 2.0) < 1e-9 and abs(starts[3] - 3.0) < 1e-9,
      f"unkeyed words are spread evenly between neighbours / the start ({[round(x, 2) for x in starts]})")
st2 = TextStyle(text="a b c", delay_keyed=True, delay_word_times=[None, 1.0, None])
s2 = [t for t, _w in R.word_start_times(st2, 3.0)]
check(abs(s2[2] - 2.0) < 1e-9, f"after the last key: spread toward the end ({[round(x, 2) for x in s2]})")
# through the UI: toggle, click words at the playhead
ctl.set_selection([sp.id], anchor=sp.id)
pump(0.3)
props._fields["delay_keyed"].click()
pump(0.4)
box = props._fields["words_box"]
from afterglow.gui.custom_button import CustomButton
chips = [b for b in box.findChildren(CustomButton)]
check(box.isVisible() and [b.text() for b in chips] == ["Speech!"], f"per-word timing shows each word as a button ({[b.text() for b in chips]})")
seg_ = ctl.project.find_segment(sp.id)[1]
seg_start = seg_.start
ctl.perform("txt", lambda p: setattr(p.find_segment(sp.id)[1].parts[0].text, "text", "no way dude"))
pump(0.4)
chips = [b for b in box.findChildren(CustomButton) if b.isVisible()]
check([b.text() for b in chips] == ["no", "way", "dude"], "the buttons follow the text")
ctl.set_playhead(seg_start + 2.0)
pump(0.2)
QTest.mouseClick(chips[1], Qt.LeftButton)
pump(0.4)
stt = ctl.project.find_segment(sp.id)[1].parts[0].text
check(stt.delay_keyed and abs(stt.delay_word_times[1] - 2.0) < 1e-6, f"clicking a word keys it at the playhead ({stt.delay_word_times})")
chips = [b for b in box.findChildren(CustomButton) if b.isVisible()]
chips[1].customContextMenuRequested.emit(QPoint(2, 2))
pump(0.3)
stt = ctl.project.find_segment(sp.id)[1].parts[0].text
check(stt.delay_word_times[1] is None, "right-clicking a word clears its key")
w.grab().save(f"{OUT}/r4_word_keys.png")

# ---- 8: thought cloud grows without a center oval -----------------------------------------------------------------
body = QRectF(-100, -40, 200, 80)
tip = QPointF(160, 160)
# (round 7: the cloud now grows uniformly out of its middle -- see test_round7)
full = R.bubble_shape("thought", body, tip, 1.0)[0]
check(full.contains(QPointF(0, 0)) and full.contains(QPointF(-80, -10)), "fully grown: the middle is solid")

# ---- round 5: the tail follows the bubble; thought cloud done before text; Permanent Marker ----------
from afterglow.nle.model import Keyframe, Project
pp = default_project(640, 360, 30)
st5 = TextStyle(text="hey!", font_size=0.07, color="#111111", bubble="speech", tail_x=-0.2, tail_y=0.25,
                grow_in=0.4, grow_out=0.4)
seg5 = Segment(parts=[Part(kind=KIND_TEXT, src_in=0, src_out=4, has_audio=False, text=st5)])
seg5.keyframes["x"] = [Keyframe(0, -0.3), Keyframe(4, 0.3)]
ops.place(pp, seg5, 1, 0.0)
rr = R.Renderer(pp)


def cx(img):
    xs = [x for y in range(0, img.height(), 2) for x in range(0, img.width(), 2)
          if img.pixelColor(x, y).red() > 230 and img.pixelColor(x, y).green() > 230]
    return sum(xs) / len(xs) if xs else None
tip_x = lambda t: (0.5 + (-0.3 + 0.6 * t / 4) - 0.2) * 640
xs = [cx(rr.frame(t, 640, 360)) for t in (3.7, 3.85, 3.95)]
tips = [tip_x(t) for t in (3.7, 3.85, 3.95)]
check(all(x is not None for x in xs[:2]) and xs[1] > tips[0] - 5 and abs(xs[1] - tips[1]) < abs(xs[0] - tips[0]),
      f"shrinking into a tail that moves with the keyframed bubble (centroid {[round(x or 0) for x in xs]}, tip {[round(t) for t in tips]})")
rr.close()
old = {"schema_version": 1, "tracks": [{"segments": [{"transform": {"x": 0.2, "y": -0.1}, "parts": [
    {"kind": "text", "src_out": 3, "text": {"text": "hi", "bubble": "speech", "tail_x": 0.0, "tail_y": 0.3}}]}]}]}
mp = Project.from_dict(old)
mst = mp.tracks[0].segments[0].parts[0].text
check(abs(mst.tail_x - (-0.2)) < 1e-9 and abs(mst.tail_y - 0.4) < 1e-9, "old projects: absolute tail tips become offsets")
body = QRectF(-100, -40, 200, 80)
check(R.THOUGHT_TEXT_IN_AT < R.THOUGHT_CLOUD_END and R.TEXT_IN_AT <= 0.25,
      "bubble text starts early in the grow (thought: before the cloud is done)")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
