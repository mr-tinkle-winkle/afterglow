"""
Round 12 feedback: pictures inside text / bubbles (any kind).

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_round12.py
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="r12_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine", "-t", "5", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                "-c:a", "aac", str(CLIPS / "clip.mp4")], check=True)

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

app = QApplication([])
from afterglow import library
from afterglow.gui.main_window import MainWindow
from afterglow.nle import ops, render as R
from afterglow.nle.model import KIND_TEXT, Part, Segment, TextStyle, default_project

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


PIC = os.path.join(HOME, "face.png")
img = QImage(200, 100, QImage.Format_ARGB32)
img.fill(QColor("#00c040"))
img.save(PIC)

# ---- layout ----------------------------------------------------------------------------------------------------
base = TextStyle(text="Hello", bubble="speech")
withpic = TextStyle(text="Hello", bubble="speech", image_path=PIC, image_size=0.2)
l0, l1 = R.text_layout(base, 1080), R.text_layout(withpic, 1080)
check(l1["image"] is not None and abs(l1["image_rect"].height() - 216) < 1 and abs(l1["image_rect"].width() - 432) < 1,
      "the picture is sized from Picture size (height share) keeping its shape")
check(l1["rect"].height() > l0["rect"].height() + 200 and l1["rect"].width() >= 432,
      "the text block grows to hold it (so the bubble does too)")
check(l1["image_rect"].bottom() < l1["text_offset"].y(), "Above: the picture is over the words")
for place, test in (("below", lambda L: L["image_rect"].top() > L["text_offset"].y()),
                    ("left", lambda L: L["image_rect"].right() < L["text_offset"].x()),
                    ("right", lambda L: L["image_rect"].left() > L["text_offset"].x())):
    L = R.text_layout(TextStyle(text="Hello", image_path=PIC, image_place=place), 1080)
    check(test(L), f"{place.capitalize()}: the picture is {place if place == 'below' else place + ' of'} the words")
body0 = R.bubble_body(l0)
body1 = R.bubble_body(l1)
check(body1.width() > body0.width() and body1.height() > body0.height(), "bubble body encloses the picture")
check(R.text_layout(TextStyle(text="Hi", image_path="/nope.png"), 1080)["image"] is None, "a missing picture is ignored")

# ---- rendering: every bubble kind -----------------------------------------------------------------------------
p = default_project(640, 360, 30)
kinds = [("", ""), ("speech", ""), ("speech", "intercom"), ("thought", ""), ("thought", "electronic")]
for i, (kind, var) in enumerate(kinds):
    st = TextStyle(text="Hey", bubble=kind, bubble_variant=var, image_path=PIC, image_size=0.2)
    ops.place(p, Segment(parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=2, has_video=True,
                                     has_audio=False, text=st)], name=f"s{i}"), 1, i * 2.0)
p.tracks[1].segments  # noqa
r = R.Renderer(p)
ok = []
for i, (kind, var) in enumerate(kinds):
    frame = r.frame(i * 2.0 + 1.5, 640, 360)
    # the picture sits above the words, centered: sample just above the middle
    c = frame.pixelColor(320, 180 - 30)
    ok.append(c.green() > 150 and c.red() < 80)
check(all(ok), f"the picture shows in plain text and every bubble kind ({ok})")
st_only = TextStyle(text="", bubble="speech", image_path=PIC)
p2 = default_project(640, 360, 30)
ops.place(p2, Segment(parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=2, has_video=True, has_audio=False,
                                  text=st_only)], name="pic"), 1, 0.0)
r2 = R.Renderer(p2)
c = r2.frame(1.5, 640, 360).pixelColor(320, 180)
check(c.green() > 150 and c.red() < 80, "a bubble with only a picture (no words) works")
r.close()
r2.close()
props = ops.copy_properties(Segment(parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=1, has_video=True,
                                                has_audio=False, text=withpic)]))
check(props["text"]["image_path"] == PIC, "the picture is part of Copy Properties / global presets")

# ---- UI ----------------------------------------------------------------------------------------------------------
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
seg = ctl.add_text("Thought bubble", t=0.0)
pump(0.3)
props_panel = page.properties
check("image_place" in props_panel._fields and not props_panel._fields["image_size"].isEnabled(),
      "Properties: Picture row (size/place off until there's a picture)")
props_panel._once("Add picture", lambda p: props_panel._set_text(p, image_path=PIC))
props_panel._rebuild()
pump(0.3)
props_panel._fields["image_place"].setCurrentIndex(props_panel._fields["image_place"].findData("left"))
pump(0.2)
props_panel._fields["image_size"].setValue(25)
pump(0.8)
props_panel._end_edit()
st = ctl.project.find_segment(seg.id)[1].parts[0].text
check(st.image_path == PIC and st.image_place == "left" and abs(st.image_size - 0.25) < 1e-9,
      "picking a picture, its place and size")
ctl.set_playhead(2.0)
pump(0.6)
geo = page.preview.canvas.seg_geometry(ctl.project.find_segment(seg.id)[1])
check(geo is not None and geo[1].width() > 300, "the preview's selection box covers the picture too")
w.grab().save(f"{OUT}/r12_editor.png")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
