"""
Round 7 feedback: bubble text starts early in the grow / thought clouds grow
uniformly, the save bar shows something from the start, Filters in the
editor header, the previewer/editor video hugs its frame (no side bars),
plain wheel scrolls the timeline's tracks.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_round7.py
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="r7_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
OUT = os.environ.get("SHOTS", "/tmp/shots")
os.makedirs(OUT, exist_ok=True)
for name, size in (("wide", "640x360"), ("tall", "360x640")):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30",
                    "-f", "lavfi", "-i", "sine", "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-g", "30",
                    "-c:a", "aac", str(CLIPS / f"{name}.mp4")], check=True)

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication([])
from afterglow import library
from afterglow.gui.main_window import MainWindow
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


library.scan_and_ingest_new_videos()
vids = {x.title: x for x in library.list_videos()}
w = MainWindow()
w.resize(1600, 1000)
w.show()
pump(2.0)

# ---- 1 + 2: bubble timing ------------------------------------------------------------------------------
check(R.TEXT_IN_AT <= 0.25, f"speech text starts early in the grow ({R.TEXT_IN_AT})")
check(R.THOUGHT_TEXT_IN_AT < R.THOUGHT_CLOUD_END, "thought text starts while the cloud is still growing")
body = QRectF(-100, -40, 200, 80)
rects = [R.bubble_shape("thought", body, None, g)[0].boundingRect() for g in (0.35, 0.5, 0.65, 1.0)]
fc = rects[3].center()
check(all(abs(r.center().x()) <= abs(fc.x()) + 1 and abs(r.center().y()) <= abs(fc.y()) + 1 for r in rects[:3]),
      "the thought cloud grows centered on its middle")
check(rects[0].width() < rects[1].width() < rects[2].width() <= rects[3].width() + 1
      and all(abs(r.width() / max(r.height(), 1e-6) - rects[3].width() / rects[3].height()) < 0.35 for r in rects[1:3]),
      f"...uniformly (widths {[round(r.width()) for r in rects]})")

# ---- 3: save progress bar is never blank ------------------------------------------------------------------
from afterglow.gui.advanced_editor.page import ProgressDialog
pd = ProgressDialog("Saving…", w)
check(pd.bar.value() == 0 and pd.bar.text() == "Preparing…", f"progress bar starts as 'Preparing…' ({pd.bar.text()!r})")
pd.set_fraction(0.25)
check(pd.bar.text() == "25%", f"...then shows the percent ({pd.bar.text()!r})")
pd.deleteLater()

# ---- 6: previewer frame hugs the video ------------------------------------------------------------------
for title in ("wide", "tall"):
    w._show_preview_overlay(vids[title], None)
    pump(2.0)
    c = w._preview_overlay.content
    vw = c.video_widget
    ar = vw.width() / max(vw.height(), 1)
    want = 640 / 360 if title == "wide" else 360 / 640
    check(abs(ar - want) < 0.03, f"previewer ({title}): the player is the video's shape ({ar:.3f} vs {want:.3f})")
    if title == "wide":
        # scaled up into the old side bars: as wide as the box allows
        cw, _ch = c.video_chrome()
        check(c._video_frame.width() >= c.width() - cw - 4, f"previewer (wide): the video spans the box's width "
              f"({c._video_frame.width()} of {c.width() - cw})")
        w.grab().save(f"{OUT}/r7_preview_wide.png")
    else:
        w.grab().save(f"{OUT}/r7_preview_tall.png")
    w._preview_overlay.close_overlay(immediate=True)
    pump(0.5)

# ---- 5 + 7: editor Filters + wheel -------------------------------------------------------------------------
w._open_in_editor(vids["wide"].id)
pump(1.5)
page = w.editor_page
check(page.filters_btn.isEnabled() and page.filters_btn.isVisible(), "editor: Filters button in the header")
hdr = page.filters_btn.mapTo(page, QPoint(page.filters_btn.width(), 0))
check(hdr.x() > page.width() - 60 and hdr.y() < 60, f"...at the top right ({hdr.x()},{hdr.y()} of {page.width()})")
QTest.mouseClick(page.filters_btn, Qt.LeftButton)
pump(0.3)
check(page._filters_panel is not None and page._filters_panel.isVisible(), "Filters opens the filters panel")
w.grab().save(f"{OUT}/r7_editor_filters.png")
QTest.mouseClick(page.filters_btn, Qt.LeftButton)
pump(0.2)
check(page._filters_panel is None, "...and closes it again")

view = page.timeline.view
for _ in range(6):
    page.ctl.perform("add track", lambda p: p.tracks.append(type(p.tracks[0])()))
pump(0.3)
view.set_scroll_y(0)
t0, y0 = view.scroll_t, view.scroll_y
pos = QPointF(view.lanes_left() + 100, view.height() / 2)
QApplication.sendEvent(view, QWheelEvent(pos, view.mapToGlobal(pos), QPoint(), QPoint(0, -240), Qt.NoButton,
                                         Qt.NoModifier, Qt.NoScrollPhase, False))
pump(0.1)
check(view.scroll_y > y0 and abs(view.scroll_t - t0) < 1e-9,
      f"plain wheel scrolls the tracks (y {y0:.0f} -> {view.scroll_y:.0f}), not time")

# editor preview: no dark bars around the frame
cv = page.preview.canvas
img = cv.grab().toImage()
fr = cv.frame_rect()
side = img.pixelColor(int(fr.left()) - 3, int(fr.center().y())) if fr.left() > 4 else None
if side is None:
    side = img.pixelColor(int(fr.center().x()), int(fr.top()) - 3) if fr.top() > 4 else None
check(side is None or side.lightness() > 12, f"editor preview: no black letterbox bars ({side.name() if side else 'none'})")
w.grab().save(f"{OUT}/r7_editor.png")

w.close()
pump(0.3)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")
sys.exit(1 if fails else 0)
