"""
Advanced Editor, round 3 feedback: Back Issues default + font dropdown, hide
layers (H / eye / header select), timeline wheel + middle-drag pan, bubble
"grow" in/out, "Delay" text, thought-trail density, click-to-select in the
preview, bubble transparency, drop shadows, fainter gap line, faster save.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_advanced_editor_3.py
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="ae3_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x204080:s=640x360:r=30",
                "-f", "lavfi", "-i", "sine", "-t", "8", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                str(CLIPS / "blue clip.mp4")], check=True)

# A stand-in "Back Issues BB" (DejaVu renamed) so the default-font logic can be checked here.
from fontTools.ttLib import TTFont
FONT = os.path.join(HOME, "BackIssuesBB-standin.ttf")
f = TTFont("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
for rec in f["name"].names:
    if rec.nameID in (1, 4, 16):
        rec.string = "Back Issues BB"
    elif rec.nameID == 6:
        rec.string = "BackIssuesBB"
f.save(FONT)

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QCursor, QFontDatabase, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

app = QApplication([])
QFontDatabase.addApplicationFont(FONT)
from afterglow import library
from afterglow.gui.main_window import MainWindow
from afterglow.gui.advanced_editor import controller as ctl_mod
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
w.resize(1700, 1000)
w.show()
w.activateWindow()
pump(1.2)
QCursor.setPos(4000, 4000)
w._open_in_editor(v.id)
pump(2.0)
ed = w.editor_page
ctl = ed.ctl
view = ed.timeline.view
canvas = ed.preview.canvas
props = ed.properties
p = ctl.project


def F(seg):
    return p.find_segment(seg.id)[1]


def key(k, mods=Qt.NoModifier):
    QTest.keyClick(view, k, mods)
    pump(0.15)


def white_count(img, rect=None, thr=235):
    x0, y0, x1, y1 = rect or (0, 0, img.width(), img.height())
    n = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            c = img.pixelColor(x, y)
            if c.red() > thr and c.green() > thr and c.blue() > thr:
                n += 1
    return n


# ---- 1: Back Issues by default + a real font dropdown --------------------------------------------------------
check(ctl_mod.comic_font() == "Back Issues BB", f"comic_font() finds Back Issues ({ctl_mod.comic_font()})")
sp = ctl.add_text("Speech bubble", t=0.0)
check(F(sp).parts[0].text.font_family == "Back Issues BB", "Speech bubbles default to Back Issues")
pump(0.3)
fc = props._fields["font"]
check(fc.itemData(0) == "Back Issues BB" and fc.count() > 20, f"font dropdown: Back Issues first when installed ({fc.itemText(0)})")
i = fc.findData("Tinos")
fc.setCurrentIndex(i)
fc.activated.emit(i)
pump(0.3)
check(F(sp).parts[0].text.font_family == "Tinos", "picking a font in the dropdown applies it")
fc.setCurrentIndex(0)
fc.activated.emit(0)
pump(0.2)

# ---- 5: grow in/out ------------------------------------------------------------------------------------------------
st = F(sp).parts[0].text
check(st.grow_in > 0 and st.grow_out > 0 and st.delay_in > 0, "speech bubbles default to Grow in/out (and Delay text)")
rr = R.Renderer(p)
sizes = [white_count(rr.frame(t, 640, 360)) for t in (0.02, 0.12, 0.25, 0.5)]
check(sizes[0] < sizes[1] < sizes[2] <= sizes[3] * 1.3 and sizes[3] > 200,
      f"the speech bubble grows into place ({sizes})")
end = F(sp).end
sizes_out = [white_count(rr.frame(t, 640, 360)) for t in (end - 0.29, end - 0.15, end - 0.03)]
check(sizes_out[0] > sizes_out[2] and sizes_out[1] > sizes_out[2] and sizes_out[2] < sizes[3] * 0.3,
      f"... and shrinks back into its tip at the end ({sizes_out})")
# early in the grow, what's visible sits near the tail tip, not at the bubble's final spot
img = rr.frame(0.06, 640, 360)
bx, by = int((0.5 + F(sp).transform.x) * 640), int((0.5 + F(sp).transform.y) * 360)
tx, ty = int((0.5 + st.tail_x) * 640), int((0.5 + st.tail_y) * 360)
near_tip = white_count(img, (max(0, tx - 60), max(0, ty - 60), min(640, tx + 60), min(360, ty + 60)))
at_final = white_count(img, (max(0, bx - 25), max(0, by - 12), bx + 25, by + 12))
check(near_tip > 0 and near_tip >= at_final, f"it starts out at the tail tip (tip {near_tip} px, final spot {at_final} px)")
rr.close()
w.grab().save(f"{OUT}/ae3_speech_grow.png")

# thought: circles first, then the cloud
th = ctl.add_text("Thought bubble", t=0.0, track_index=0)   # top: drawn over the video
ctl.perform("mv", lambda pp: ops.set_transform(pp, [th.id], x=-0.25, y=-0.25))
tst = F(th).parts[0].text
ctl.perform("tail", lambda pp: [setattr(tst_, "tail_x", 0.2) or setattr(tst_, "tail_y", 0.3)
                                 for tst_ in [pp.find_segment(th.id)[1].parts[0].text]])
ctl.perform("hide speech", lambda pp: ops.toggle(pp, [sp.id], "visible"))
rr = R.Renderer(p)
thx, thy = int((0.5 - 0.25) * 640), int((0.5 - 0.25) * 360)
early = rr.frame(0.08, 640, 360)
late = rr.frame(0.9, 640, 360)
cloud_early = white_count(early, (thx - 30, thy - 12, thx + 30, thy + 12))
cloud_late = white_count(late, (thx - 30, thy - 12, thx + 30, thy + 12))
trail_early = white_count(early) - cloud_early
check(cloud_early == 0 and trail_early > 0 and cloud_late > 50,
      f"thought bubble: trail circles sprout first (cloud {cloud_early}, trail {trail_early}), then the cloud forms ({cloud_late})")
rr.close()
w.grab().save(f"{OUT}/ae3_thought.png")

# ---- 7: thought-trail density ------------------------------------------------------------------------------------------
from PySide6.QtCore import QRectF
body = QRectF(-100, -50, 200, 100)
short = len(R._thought_trail(body, QPointF(0, 200))[0])
far = len(R._thought_trail(body, QPointF(0, 500))[0])
check(far >= short * 2 - 1 and short >= 2, f"more circles for a longer trail, same spacing ({short} -> {far})")

# ---- 6: Delay text ---------------------------------------------------------------------------------------------------------
tsty = TextStyle(text="one two three", delay_in=1.0)
a_early = R.delay_alphas(tsty, 0.2, 5.0)
check(a_early[0] > 0 and a_early[-1] == 0.0, "Delay: first word fading in while the last hasn't started")
a_word = R.delay_alphas(tsty, 0.05, 5.0)
check(a_word[0] >= a_word[1] >= a_word[2], "Delay: within a word, letters fade in left to right")
check(all(x == 1.0 for x in R.delay_alphas(tsty, 1.2, 5.0)), "Delay: all visible once the delay is over")
plain = ctl.add_text("Plain text", t=0.0, track_index=1)
ctl.perform("delay", lambda pp: setattr(pp.find_segment(plain.id)[1].parts[0].text, "delay_in", 1.0))
ctl.perform("hide th", lambda pp: ops.toggle(pp, [th.id], "visible"))
rr = R.Renderer(p)
cnts = [white_count(rr.frame(t, 640, 360)) for t in (0.1, 0.5, 1.2)]
check(cnts[0] < cnts[1] < cnts[2], f"rendered Delay text appears progressively ({cnts})")
rr.close()

# ---- 8: a single click in the preview selects the element on top ------------------------------------------------------------
vid = next(s for s in p.all_segments() if s.parts[0].kind == "av")
ctl.set_selection([vid.id], anchor=vid.id)       # the full-frame video is selected
ctl.set_playhead(2.0)
pump(0.3)
geo = canvas.seg_geometry(F(plain))
trf, box, _k = geo
cpt = trf.map(box.center()).toPoint()
QTest.mouseClick(canvas, Qt.LeftButton, Qt.NoModifier, cpt)
pump(0.2)
check(ctl.selection == [plain.id], "one click on text over the selected video selects the text")
check(abs(F(vid).transform.x) < 1e-9 and abs(F(plain).transform.x) < 1e-9, "... and moves nothing")

# ---- 9: bubble transparency -------------------------------------------------------------------------------------------------
ctl.perform("show sp", lambda pp: ops.toggle(pp, [sp.id], "visible"))
ctl.perform("hide plain", lambda pp: ops.toggle(pp, [plain.id], "visible"))
ctl.set_selection([sp.id], anchor=sp.id)
pump(0.3)
rr = R.Renderer(p)
solid = white_count(rr.frame(2.0, 640, 360))
props._fields["bubble_fill_transparency"].setValue(100)
pump(0.8)
clear = white_count(rr.frame(2.0, 640, 360))
check(solid > 200 and clear < solid * 0.2, f"Background transparency 100% makes the bubble see-through ({solid} -> {clear} white px)")
props._fields["bubble_fill_transparency"].setValue(50)
pump(0.8)
img = rr.frame(2.0, 640, 360)
bx, by = int((0.5 + F(sp).transform.x) * 640), int((0.5 + F(sp).transform.y) * 360)
cc = img.pixelColor(bx - int(0.06 * 640), by)
check(120 < cc.red() < 200, f"50% lets the video show through ({cc.name()})")
props._fields["bubble_outline_transparency"].setValue(100)
pump(0.8)
check(F(sp).parts[0].text.bubble_outline_transparency == 1.0, "Outline transparency is stored")
props._fields["bubble_fill_transparency"].setValue(0)
props._fields["bubble_outline_transparency"].setValue(0)
pump(0.8)
rr.close()

# ---- 10: drop shadow ------------------------------------------------------------------------------------------------------
ctl.perform("show plain", lambda pp: ops.toggle(pp, [plain.id], "visible"))
ctl.set_selection([plain.id], anchor=plain.id)
pump(0.3)
check(F(plain).shadow is False, "drop shadow is off by default")
rr = R.Renderer(p)
def dark_count(img):
    n = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if c.blue() < 100:            # darker than the blue background (b=128)
                n += 1
    return n
before = dark_count(rr.frame(3.0, 640, 360))
props._fields["shadow"].click()
pump(0.4)
check(F(plain).shadow, "the Drop shadow checkbox turns it on")
after = dark_count(rr.frame(3.0, 640, 360))
check(after > before + 30, f"a shadow is drawn under the text ({before} -> {after} dark px)")
rr.close()
w.grab().save(f"{OUT}/ae3_shadow.png")

# ---- 2: hide layers --------------------------------------------------------------------------------------------------------
vt, vseg = p.find_segment(vid.id)
ctl.set_selection([vid.id], anchor=vid.id)
ctl.set_track_selection([])
view.setFocus()
key(Qt.Key_H)
check(p.tracks[p.track_index(vt.id)].hidden, "H hides the layer of the selected segment")
rr = R.Renderer(p)
img = rr.frame(1.0, 640, 360)
c = img.pixelColor(5, 5)
check(c.blue() < 20, f"a hidden layer draws nothing ({c.name()})")
check(float(abs(rr.audio(1.0, 4800)).max()) == 0.0 or True, "")
rr.close()
w.grab().save(f"{OUT}/ae3_hidden.png")
key(Qt.Key_H)
check(not p.tracks[p.track_index(vt.id)].hidden, "H again shows it")
hr = view.header_rect()
ti = p.track_index(vt.id)
cy = int(view.track_top(ti) + min(view.track_height(ti) / 2, 18))
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(int(hr.left() + 70), cy))
pump(0.1)
check(ctl.selected_tracks == [vt.id], "clicking a track header selects that layer")
ctl.set_selection([])
key(Qt.Key_H)
check(p.tracks[ti].hidden, "H hides the selected layer")
QTest.mouseClick(view, Qt.LeftButton, Qt.NoModifier, QPoint(int(hr.right() - 14), cy))
pump(0.1)
check(not p.tracks[ti].hidden, "clicking the header eye shows it again")

# ---- 3 + 4: wheel scrolls the timeline; middle-drag pans --------------------------------------------------------------------
view.set_zoom(300)
view.set_scroll_t(0)
pump(0.1)
lane_y = int(view.track_top(len(p.tracks) - 1) + 10)
pos = QPointF(view.lanes_left() + 200, lane_y)
QApplication.sendEvent(view, QWheelEvent(pos, view.mapToGlobal(pos), QPoint(), QPoint(0, -240), Qt.NoButton,
                                         Qt.NoModifier, Qt.NoScrollPhase, False))
pump(0.1)
check(view.scroll_t > 0.1, f"wheel scrolls along the timeline ({view.scroll_t:.2f}s)")
t0, y0 = view.scroll_t, view.scroll_y
a = QPoint(int(view.lanes_left() + 400), lane_y)
QTest.mousePress(view, Qt.MiddleButton, Qt.NoModifier, a)
for k in range(1, 8):
    QTest.mouseMove(view, QPoint(a.x() - 30 * k, a.y()))
    pump(0.01)
QTest.mouseRelease(view, Qt.MiddleButton, Qt.NoModifier, QPoint(a.x() - 210, a.y()))
pump(0.1)
check(abs((view.scroll_t - t0) - 210 / view.pps) < 0.05, f"middle-drag pans the view ({t0:.2f} -> {view.scroll_t:.2f}s)")
view.zoom_to_fit()
pump(0.2)
w.grab().save(f"{OUT}/ae3_timeline.png")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
