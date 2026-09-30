"""
Round 8 feedback: the thought cloud waits for its trail, then grows outward
bump by bump (every side growing together); bubble styles (speech: spiky,
whisper, wiggly, intercom; thought: wobbly, dreamy, jagged, electronic) with
an "Animated" option.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_round8.py
"""
import os, sys, tempfile, subprocess, time, copy
HOME = tempfile.mkdtemp(prefix="r8_home_")
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

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath
from PySide6.QtTest import QTest
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


def area(path: QPainterPath) -> float:
    r = path.boundingRect()
    n, hit = 0, 0
    for i in range(40):
        for j in range(40):
            n += 1
            hit += path.contains(QPointF(r.left() + r.width() * (i + 0.5) / 40, r.top() + r.height() * (j + 0.5) / 40))
    return r.width() * r.height() * hit / max(n, 1)


body = QRectF(-100, -40, 200, 80)
tip = QPointF(160, 160)

# ---- 1: the thought cloud waits for its trail, then every side grows together -------------------------------
trail, _e, _a = R._thought_trail(body, tip)
cs = R.thought_cloud_start(len(trail))
before = R.bubble_shape("thought", body, tip, cs - 0.02)[0]
check(not before.contains(QPointF(0, 0)) and before.boundingRect().top() > 0,
      f"no cloud before the trail reaches it (cloud starts at g={cs:.2f})")
g_mid = cs + (R.THOUGHT_CLOUD_END - cs) * 0.4
mid = R.bubble_shape("thought", body, tip, g_mid)[0]
full = R.bubble_shape("thought", body, tip, 1.0)[0]
far = QPointF(-60, -30)          # top left: the side far from the bottom-right trail
near = QPointF(60, 25)
check(mid.contains(near) and area(mid) < area(full) * 0.95, "mid-cloud: still growing, trail side formed")
mr = mid.boundingRect()
check(mr.left() < -40 and mr.top() < -15, f"...and the far side is already growing too ({mr.left():.0f},{mr.top():.0f})")
check(R.THOUGHT_TEXT_IN_AT > cs, "thought text starts after the cloud has begun")

# ---- 2: styles --------------------------------------------------------------------------------------------
kinds = {"speech": [v for _l, v in R.BUBBLE_VARIANTS["speech"]], "thought": [v for _l, v in R.BUBBLE_VARIANTS["thought"]]}
check(kinds["speech"] == ["", "spiky", "whisper", "wiggly", "intercom"], "speech styles: neutral, spiky, whisper, wiggly, intercom")
check(kinds["thought"] == ["", "wobbly", "dreamy", "jagged", "electronic"], "thought styles: neutral, wobbly, dreamy, jagged, electronic")
for kind, variants in kinds.items():
    neutral = R.bubble_shape(kind, body, tip, 1.0)[0]
    for v in variants[1:]:
        p0 = R.bubble_shape(kind, body, tip, 1.0, variant=v, t=0.0, animated=True)[0]
        p1 = R.bubble_shape(kind, body, tip, 1.0, variant=v, t=0.37, animated=True)[0]
        s0 = R.bubble_shape(kind, body, tip, 1.0, variant=v, t=0.0, animated=False)[0]
        s1 = R.bubble_shape(kind, body, tip, 1.0, variant=v, t=0.37, animated=False)[0]
        dash = R.bubble_dash(v, 0.0, True), R.bubble_dash(v, 0.37, True), R.bubble_dash(v, 0.37, False)
        shape_differs = (p0 != neutral) or dash[0] is not None
        moves = (p0 != p1) or (dash[0] is not None and dash[0][1] != dash[1][1])
        still = (s0 == s1) and (dash[2] is None or dash[2][1] == 0.0)
        check(not p0.isEmpty() and shape_differs, f"{kind}/{v}: its own look")
        check(moves, f"{kind}/{v}: animated = keeps moving")
        check(still, f"{kind}/{v}: not animated = still")
check(R.bubble_dash("whisper", 0, True)[0][0] > 1 and R.bubble_dash("dreamy", 0, True)[2] == Qt.RoundCap,
      "whisper = dashed outline, dreamy = dotted outline")
check(R.valid_variant("thought", "spiky") == "" and R.valid_variant("speech", "spiky") == "spiky",
      "a style only applies to its own bubble kind")
check(TextStyle().bubble_animated is True and TextStyle().bubble_variant == "", "Animated is on by default")

# every style renders through the real renderer (preview + export path)
p = default_project(640, 360, 30)
y = 0.0
for kind, variants in kinds.items():
    for v in variants:
        st = TextStyle(text="Hi there", bubble=kind, bubble_variant=v, grow_in=0.5, grow_out=0.3)
        seg = Segment(parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=2, has_video=True, has_audio=False,
                                  text=st)], name=v or "n")
        ops.place(p, seg, 1, y)
        y += 2.0
r = R.Renderer(p)
ok = True
for k in range(int(y / 0.25)):
    try:
        r.frame(k * 0.25, 320, 180)
    except Exception as e:
        ok = False
        print("  render error", k * 0.25, e)
check(ok, "every style renders through its grow/animation/shrink")
r.close()

# ---- UI: Properties > Bubble > Style + Animated ----------------------------------------------------------------
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
combo = props._fields["bubble_variant"]
anim = props._fields["bubble_animated"]
items = [combo.itemData(i) for i in range(combo.count())]
check(items == kinds["speech"], f"Style lists the speech styles ({items})")
check(anim.isChecked() and not anim.isEnabled(), "Animated: on, greyed out for Neutral")
combo.setCurrentIndex(items.index("spiky"))
pump(0.3)
st = ctl.project.find_segment(seg.id)[1].parts[0].text
check(st.bubble_variant == "spiky", "picking a style sets it")
anim = props._fields["bubble_animated"]
check(anim.isEnabled(), "Animated enabled for a style")
QTest.mouseClick(anim, Qt.LeftButton, pos=anim.rect().center() if hasattr(anim, "rect") else None)
pump(0.3)
st = ctl.project.find_segment(seg.id)[1].parts[0].text
check(st.bubble_animated is False, "unticking Animated turns it off")
ctl.undo()
pump(0.3)
check(ctl.project.find_segment(seg.id)[1].parts[0].text.bubble_animated is True, "...undoable")
bub = props._fields["bubble"]
bub.setCurrentIndex(bub.findData("thought"))
pump(0.5)
st = ctl.project.find_segment(seg.id)[1].parts[0].text
combo = props._fields["bubble_variant"]
check(st.bubble == "thought" and st.bubble_variant == "" and
      [combo.itemData(i) for i in range(combo.count())] == kinds["thought"],
      "switching to Thought resets the style and lists the thought styles")
ctl.set_playhead(1.9)
pump(0.5)
combo = props._fields["bubble_variant"]
props.scroll.ensureWidgetVisible(combo, 0, 200)
pump(0.3)
w.grab().save(f"{OUT}/r8_props.png")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
