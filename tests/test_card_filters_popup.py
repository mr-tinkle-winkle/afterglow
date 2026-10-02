"""
The video card's Filters button opens the sorter's filter selector (SortPopover chrome) applied to the
card's own video: toggling a tag tags / untags it, the popover stays open through several toggles and
closes only on an outside click, and the grid rebuild is held back while it is open.

    QT_QPA_PLATFORM=offscreen python3 tests/test_card_filters_popup.py
"""
import os, sys, tempfile, subprocess
HOME = tempfile.mkdtemp(prefix="card_filters_home_")
os.environ["HOME"] = HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication([])
from afterglow import config, db, library
from afterglow.gui.custom_button import CustomButton
from afterglow.gui.filters_popup import FiltersPopup
from afterglow.gui.sort_popover import SortPopover
from afterglow.gui.video_card import VideoCard

db.init_db()
clips = config.load().clips_path()
clips.mkdir(parents=True, exist_ok=True)
p = clips / "card.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:d=1",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)], check=True)
video = library.add_video(p, title="card")
for t in ("Ace", "Clutch", "Funny"):
    library.create_tag(t)
card = VideoCard(library.get_video(video.id))
card.resize(300, 300)
card.show()
app.processEvents()

FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)

events = []
card.context_menu_opened.connect(lambda: events.append("open"))
card.context_menu_closed.connect(lambda: events.append("close"))
changed = []
card.tags_changed.connect(lambda: changed.append(1))

btn = next(b for b in card.findChildren(CustomButton) if b.toolTip() == "Filters")
btn.click()
app.processEvents()
pops = [w for w in app.topLevelWidgets() if isinstance(w, FiltersPopup) and w.isVisible()]
check(len(pops) == 1, "the Filters button opens the filters popover")
pop = pops[0]
check(isinstance(pop, SortPopover), "...it is the sorter's popover (same chrome)")
check(set(pop.checkboxes) >= {"Ace", "Clutch", "Funny"}, "...listing every known tag")
check(events == ["open"], "the grid rebuild is held back while it is open")

pop.checkboxes["Ace"].setChecked(True)
pop.checkboxes["Funny"].setChecked(True)
app.processEvents()
check(set(library.get_video(video.id).tags) == {"Ace", "Funny"}, "ticking tags adds them to the video")
check(pop.isVisible() and events == ["open"], "...and the popover stays open between toggles")
check(len(changed) == 2, "...each toggle tells the library to refresh (held back until it closes)")
pop.checkboxes["Ace"].setChecked(False)
check(set(library.get_video(video.id).tags) == {"Funny"}, "unticking removes the tag")

pop.hide()
app.processEvents()
check(events == ["open", "close"], "closing it releases the grid rebuild")

card2 = VideoCard(library.get_video(video.id))
btn2 = next(b for b in card2.findChildren(CustomButton) if b.toolTip() == "Filters")
card2.show(); btn2.click(); app.processEvents()
pop2 = next(w for w in app.topLevelWidgets() if isinstance(w, FiltersPopup) and w.isVisible())
check(pop2.checkboxes["Funny"].isChecked() and not pop2.checkboxes["Ace"].isChecked(), "a fresh popover shows the video's current tags")
pop2.hide()

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
