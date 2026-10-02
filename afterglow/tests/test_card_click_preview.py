"""
Library card: a plain click on the thumbnail / video box opens the preview
player (preview_requested); a click on the info box only selects; Ctrl-click
and double-click never preview.

    QT_QPA_PLATFORM=offscreen python3 tests/test_card_click_preview.py
"""
import os, sys, tempfile, subprocess
HOME = tempfile.mkdtemp(prefix="card_click_home_")
os.environ["HOME"] = HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication([])
from afterglow import config, db, library
from afterglow.gui.video_card import VideoCard

db.init_db()
clips = config.load().clips_path()
clips.mkdir(parents=True, exist_ok=True)
p = clips / "card.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)
video = library.add_video(p, title="card")
card = VideoCard(video)
card.resize(300, 260)
card.show()
app.processEvents()

FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)

got, sel = [], []
card.preview_requested.connect(lambda v, n: got.append(v.id))
card.clicked.connect(lambda i, m: sel.append(i))

def wait(ms=400):
    end = QTimer(); end.setSingleShot(True); end.start(ms)
    while end.isActive():
        app.processEvents()

box = card.video_box.geometry()
QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, box.center()); wait()
check(len(got) == 1, "click on the video box opens the preview")
got.clear()
QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, card.thumb_label.mapTo(card, card.thumb_label.rect().center())); wait()
check(len(got) == 1, "click on the thumbnail opens the preview")
got.clear()
QTest.mouseClick(card, Qt.LeftButton, Qt.ControlModifier, box.center()); wait()
check(not got, "Ctrl-click does not open the preview")
info_pt = QPoint(card.width() // 2, card.height() - 6)
check(not box.contains(info_pt), "(the probe point is below the video box)")
sel.clear()
QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, info_pt); wait()
check(not got and sel, "click on the info area selects only")
QTest.mouseDClick(card, Qt.LeftButton, Qt.NoModifier, box.center()); wait()
check(not got, "double-click does not preview (it opens the Editor)")
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
