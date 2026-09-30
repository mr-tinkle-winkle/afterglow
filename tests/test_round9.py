"""
Round 9 feedback: harsh spike flicker (speech + angry thought), faster,
uneasier Uncertain with its own tail, animation speed, number boxes ignore
the wheel, jagged animated intercom tail, neutral thought wiggle, electronic
box pulse, Discard Changes.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_round9.py
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="r9_home_")
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

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

app = QApplication([])
from afterglow import library
from afterglow.gui.main_window import MainWindow
from afterglow.nle import render as R, ops
from afterglow.nle.model import Part, Segment, KIND_TEXT, TextStyle, default_project

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


body = QRectF(-100, -40, 200, 80)
tip = QPointF(160, 160)


def shape(kind, v, t, animated=True, g=1.0, tp=tip):
    return R.bubble_shape(kind, body, tp, g, variant=v, t=t, animated=animated)[0]


def radius_along(path, ang, rmax=260):
    """Distance from the center to the outline along direction ang (outermost hit)."""
    import math
    last = 0.0
    for i in range(1, 400):
        r = rmax * i / 400
        if path.contains(QPointF(r * math.cos(ang), r * math.sin(ang) * 0.4)):
            last = r
    return last


# ---- 1 + 7: harsh flicker ----------------------------------------------------------------------------------
dt = 1 / R.FLICKER_HZ
for kind, v in (("speech", "spiky"), ("thought", "jagged")):
    spikes = [radius_along(shape(kind, v, k * dt + 0.01, tp=None), 0.0) for k in range(8)]
    jumps = [abs(a - b) for a, b in zip(spikes, spikes[1:])]
    smooth = all(shape(kind, v, 0.013 + k / 60, tp=None) != shape(kind, v, 0.013 + (k + 1) / 60, tp=None)
                 for k in range(6))
    check(max(jumps) > 8 and smooth, f"{kind}/{v}: spikes jump in/out harshly (per-flick jumps {[round(j) for j in jumps]}), "
                                     f"moving on every 60 fps frame")

# ---- 2: Uncertain: faster, uneasy, own tail ------------------------------------------------------------------
wt = shape("speech", "wiggly", 0.0)
nt = shape("speech", "", 0.0)
tail_pt_diff = sum(1 for k in range(30) for j in range(30)
                   if (lambda q: wt.contains(q) != nt.contains(q))(QPointF(40 + k * 4, 30 + j * 4)))
check(tail_pt_diff > 15, f"Uncertain has its own wobbly tail ({tail_pt_diff} tail-area samples differ from Neutral)")
a0, a1 = shape("speech", "wiggly", 0.0), shape("speech", "wiggly", 0.05)
check(a0 != a1, "Uncertain moves visibly within 1/20 s (faster)")

# ---- 5: intercom tail -------------------------------------------------------------------------------------
i0, i1 = shape("speech", "intercom", 0.0), shape("speech", "intercom", 1 / 16 + 0.001)
check(i0 != i1 and all(shape("speech", "intercom", 0.013 + k / 60) != shape("speech", "intercom", 0.013 + (k + 1) / 60)
                       for k in range(6)), "Intercom tail crackles, moving on every 60 fps frame")
check(i0.elementCount() > 30, f"...and is jagged ({i0.elementCount()} outline points)")

# ---- 6: neutral thought wiggles, neutral speech doesn't --------------------------------------------------
check(shape("thought", "", 0.0) != shape("thought", "", 0.4), "neutral thought cloud wiggles when animated")
check(shape("thought", "", 0.0, animated=False) == shape("thought", "", 0.4, animated=False), "...and not when not")
check(shape("speech", "", 0.0) == shape("speech", "", 0.4), "neutral speech stays still")

# ---- 8: electronic box pulse ------------------------------------------------------------------------------
widths = [shape("thought", "electronic", k * 0.1).boundingRect().width() for k in range(10)]
boxes = []
for k in range(10):
    p_ = shape("thought", "electronic", k * 0.1)
    # box width along the center line
    xs = [x for x in range(-160, 160) if p_.contains(QPointF(x, 0))]
    boxes.append(max(xs) - min(xs))
check(max(boxes) - min(boxes) > 8, f"Electronic box pulses in size ({min(boxes)}..{max(boxes)})")

# ---- round 10: squarer boxes, calmer uncertain tail tip ---------------------------------------------------------
import math
for kind, v in (("speech", "intercom"), ("thought", "electronic")):
    p_ = shape(kind, v, 0.0, tp=None)
    r_ = p_.boundingRect()
    corner = QPointF(r_.left() + r_.height() * 0.03, r_.top() + r_.height() * 0.03)
    check(p_.contains(corner), f"{kind}/{v}: nearly square corners")
tips = [shape("speech", "wiggly", k * 0.05) for k in range(6)]
near_tip = QPointF(tip.x() - 6, tip.y() - 6)
check(all(t_.contains(QPointF(tip.x() - 12, tip.y() - 10)) == tips[0].contains(QPointF(tip.x() - 12, tip.y() - 10))
          for t_ in tips), "Uncertain tail tip keeps its shape while the rest wiggles")

# ---- 3: animation speed ------------------------------------------------------------------------------------
check(TextStyle().bubble_anim_speed == 1.0, "animation speed defaults to 1x")


def frame_img(speed, t):
    p = default_project(320, 180, 30)
    st = TextStyle(text="Hey", bubble="speech", bubble_variant="spiky", bubble_anim_speed=speed)
    ops.place(p, Segment(parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=4, has_video=True,
                                     has_audio=False, text=st)], name="t"), 1, 0.0)
    r = R.Renderer(p)
    img = r.frame(t, 320, 180)
    r.close()
    return img


check(frame_img(2.0, 0.51) == frame_img(1.0, 1.02) and frame_img(2.0, 0.51) != frame_img(1.0, 0.51),
      "speed 2x = the same animation twice as fast")

# ---- UI ----------------------------------------------------------------------------------------------------
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
seg = ctl.add_text("Speech bubble", t=0.5)
pump(0.4)
props = page.properties
spd = props._fields["bubble_anim_speed"]
check(not spd.isEnabled(), "Animation speed greyed out for neutral speech")
props._fields["bubble_variant"].setCurrentIndex(1)
pump(0.3)
spd = props._fields["bubble_anim_speed"]
check(spd.isEnabled(), "...enabled for a style")
spd.setValue(2.5)
pump(0.8)
props._end_edit()
check(abs(ctl.project.find_segment(seg.id)[1].parts[0].text.bubble_anim_speed - 2.5) < 1e-9, "Animation speed sets the model")

# ---- 4: number boxes ignore the wheel ------------------------------------------------------------------------
before = spd.value()
props.scroll.ensureWidgetVisible(spd)
pump(0.2)
bar = props.scroll.verticalScrollBar()
sb0 = bar.value()
for target in (spd, spd.lineEdit()):
    pos = QPointF(target.width() / 2, target.height() / 2)
    QApplication.sendEvent(target, QWheelEvent(pos, target.mapToGlobal(pos.toPoint()), QPoint(), QPoint(0, -120),
                                               Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
    pump(0.2)
check(spd.value() == before, f"wheel over a number box doesn't change it ({before} -> {spd.value()})")
check(bar.value() != sb0 or bar.maximum() == 0, "...the panel scrolls instead")
sp2 = w._preview_overlay if False else None

# ---- 9: Discard Changes ----------------------------------------------------------------------------------------
check(page.discard_btn.isVisible() and page.discard_btn.isEnabled(), "Discard Changes is there (unsaved edits)")
dx = page.discard_btn.mapTo(page, QPoint(0, 0)).x()
sx = page.save_btn.mapTo(page, QPoint(0, 0)).x()
check(0 < sx - dx < 200, "...next to Save")
page.discard_changes(confirm=False)
pump(0.5)
texts = [s for s in ctl.project.all_segments() if s.parts[0].kind == KIND_TEXT]
check(not texts and not ctl.unsaved and not page.discard_btn.isEnabled(),
      "never saved: discarding goes back to the plain clip")
# a "saved" state, then more edits
seg = ctl.add_text("Title", t=0.2)
pump(0.2)
ctl.mark_rendered()                      # what a successful Save does
pump(0.2)
seg2 = ctl.add_text("Caption", t=1.0)
pump(0.2)
ctl.autosave_now()
check(ctl.unsaved and page.discard_btn.isEnabled(), "an edit after Save is unsaved")
page.discard_changes(confirm=False)
pump(0.4)
names = sorted(s.name for s in ctl.project.all_segments() if s.parts[0].kind == KIND_TEXT)
check(names == ["Title"] and not ctl.unsaved, f"discarding goes back to the last Save ({names})")
from afterglow.nle import store
reloaded = store.load_project(store.project_path_for_video(v.id))
check(reloaded is not None and sorted(s.name for s in reloaded.all_segments() if s.parts[0].kind == KIND_TEXT) == ["Title"],
      "...on disk too (reopening shows the saved state)")
w.grab().save(f"{OUT}/r9_editor.png")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
