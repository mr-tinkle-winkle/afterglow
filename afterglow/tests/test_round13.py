"""
Round 13 feedback: animated GIFs in text / bubbles play (and loop), with a
speed option.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_round13.py
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="r13_home_")
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
GIF = os.path.join(HOME, "rgb.gif")
subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "color=c=red:s=120x60:d=0.3:r=10",
                "-f", "lavfi", "-i", "color=c=lime:s=120x60:d=0.3:r=10",
                "-f", "lavfi", "-i", "color=c=blue:s=120x60:d=0.3:r=10",
                "-filter_complex", "[0][1][2]concat=n=3:v=1,split[a][b];[a]palettegen[p];[b][p]paletteuse",
                "-loop", "0", GIF], check=True)

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
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


def rgb(img):
    c = img.pixelColor(img.width() // 2, img.height() // 2)
    return max((("r", c.red()), ("g", c.green()), ("b", c.blue())), key=lambda kv: kv[1])[0]


frames = R._picture_frames(GIF)
total = sum(d for _i, d in frames)
check(R.image_is_animated(GIF) and abs(total - 0.9) < 0.05, f"a GIF loads all its frames ({len(frames)}, {total:.2f} s)")
seq = [rgb(R.bubble_image_at(GIF, t)) for t in (0.1, 0.4, 0.7, 1.0, 1.35, 1.65)]
check(seq == ["r", "g", "b", "r", "g", "b"], f"it plays with its own timing and loops ({seq})")
check(not R.image_is_animated(os.path.join(HOME, "nope.gif")), "a missing GIF is ignored")

# through the renderer, with speed
p = default_project(640, 360, 30)
for i, speed in enumerate((1.0, 2.0)):
    st = TextStyle(text="", bubble="speech", image_path=GIF, image_size=0.3, image_speed=speed)
    ops.place(p, Segment(parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=3, has_video=True,
                                     has_audio=False, text=st)], name=f"g{i}"), 1 + i, i * 4.0)
r = R.Renderer(p)


def at(t):
    img = r.frame(t, 640, 360)
    c = img.pixelColor(320, 180)
    return max((("r", c.red()), ("g", c.green()), ("b", c.blue())), key=lambda kv: kv[1])[0]


normal = [at(t) for t in (1.05, 1.35, 1.65, 1.95)]         # local 1.05.. -> 0.15, 0.45, 0.75, 0.15 in the loop
fast = [at(4.0 + t) for t in (1.1, 1.25, 1.4)]               # 2x: local*2 = 2.2, 2.5, 2.8 -> .4 .7 .1 in the loop
check(normal == ["r", "g", "b", "r"], f"in a bubble the GIF animates and repeats ({normal})")
check(fast == ["g", "b", "r"], f"GIF speed 2x plays twice as fast ({fast})")
r.close()

# ---- UI -------------------------------------------------------------------------------------------------------
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
seg = ctl.add_text("Speech bubble", t=0.0)
pump(0.3)
props = page.properties
check(not props._fields["image_speed"].isEnabled(), "GIF speed is off without an animated picture")
props._once("Add picture", lambda p_: props._set_text(p_, image_path=GIF))
props._rebuild()
pump(0.3)
sp = props._fields["image_speed"]
check(sp.isEnabled(), "...on with a GIF")
sp.setValue(1.5)
pump(0.8)
props._end_edit()
check(abs(ctl.project.find_segment(seg.id)[1].parts[0].text.image_speed - 1.5) < 1e-9, "GIF speed sets the model")
ctl.set_playhead(1.5)
pump(0.5)
w.grab().save(f"{OUT}/r13_editor.png")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
