"""
Previewer: (1) leaving fullscreen restores the app's prior window state/size,
(2) Favorite + Filters (Editor features) work from the previewer, (3) renders
screenshots of the restyled trim bar. Needs Xvfb + a window manager (openbox):

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX & DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_previewer_fullscreen_filters.py
Set AFTERGLOW_SRC to test another checkout.
"""
import os, sys, tempfile, subprocess, time
HOME = tempfile.mkdtemp(prefix="prev_fs_home_"); os.environ["HOME"] = HOME
sys.path.insert(0, os.environ.get("AFTERGLOW_SRC") or os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config; db.init_db()
_clips = config.load().clips_path(); _clips.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast",
                "-g", "15", "-c:a", "aac", str(_clips / "ten second clip.mp4")], check=True)
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
app = QApplication([])
from afterglow.gui.main_window import MainWindow
from afterglow import library
OUT = os.environ.get("SHOTS", "/tmp/shots"); os.makedirs(OUT, exist_ok=True)
def pump(s):
    end = time.perf_counter() + s
    while time.perf_counter() < end: app.processEvents(); time.sleep(0.005)
fails = []
def check(c, m): print(("PASS " if c else "FAIL ") + m); c or fails.append(m)

library.scan_and_ingest_new_videos()
v = next(x for x in library.list_videos() if x.title.startswith("ten second"))

def run_mode(mode):
    w = MainWindow(); w.resize(1300, 820); w.move(120, 90)
    if mode == "maximized": w.showMaximized()
    elif mode == "fullscreen": w.showFullScreen()
    else: w.show()
    pump(2.5)
    before = (w.isFullScreen(), w.isMaximized(), w.geometry().size().width(), w.geometry().size().height())
    w._show_preview_overlay(v, None); pump(2)
    c = w._preview_overlay.content
    QTest.mouseClick(c.fullscreen_btn, Qt.LeftButton); pump(1.5)
    if mode != "fullscreen":
        check(w.isFullScreen(), f"[{mode}] entered fullscreen")
    QTest.keyClick(w._preview_overlay, Qt.Key_Escape); pump(2.5)
    after = (w.isFullScreen(), w.isMaximized(), w.geometry().size().width(), w.geometry().size().height())
    check(after == before, f"[{mode}] Esc restores window: before={before} after={after}")
    check(w._preview_overlay is not None, f"[{mode}] first Esc only leaves fullscreen, overlay stays")
    return w

for mode in os.environ.get("MODES", "normal,maximized,fullscreen").split(","):
    w = run_mode(mode)
    if mode == "normal":
        # ---- favorite / filters / screenshots, in the normal window
        c = w._preview_overlay.content
        QTest.mouseClick(c.favorite_btn, Qt.LeftButton); pump(0.3)
        check(library.get_video(v.id).favorite and "★" in c._title_label.text(), "Favorite button sets favorite + star in title")
        QTest.mouseClick(c.favorite_btn, Qt.LeftButton); pump(0.3)
        check(not library.get_video(v.id).favorite, "Favorite button toggles back off")
        library.create_tag("clutch"); library.create_tag("funny")
        QTest.mouseClick(c.filters_btn, Qt.LeftButton); pump(0.5)
        panel = c._filters_panel
        check(panel is not None and panel.isVisible(), "Filters button opens the in-window panel")
        from afterglow.gui.custom_checkbox import CustomCheckBox
        boxes = {b.text(): b for b in panel.findChildren(CustomCheckBox)}
        check({"clutch", "funny"} <= set(boxes), f"panel lists known filters {sorted(boxes)}")
        boxes["clutch"].setChecked(True); pump(0.2)
        boxes["funny"].setChecked(True); pump(0.2)
        check(c._filters_panel is not None and c._filters_panel.isVisible(), "panel stays open while toggling several filters")
        check(set(library.get_video(v.id).tags) >= {"clutch", "funny"} and "clutch" in c._info_label.text(),
              f"tags saved and info line updated: {c._info_label.text()!r}")
        c.window().grab().save(f"{OUT}/preview_filters.png")
        QTest.keyClick(w._preview_overlay, Qt.Key_Escape); pump(0.3)
        check(c._filters_panel is None and w._preview_overlay is not None, "Esc closes the panel first, not the preview")
        c.video_widget.pause(); c.trim_timeline.set_range(2.0, 7.0); c.trim_timeline.set_playhead(4.0); pump(0.3)
        w.grab().save(f"{OUT}/preview_trimbar.png")
        c.trim_timeline.grab().save(f"{OUT}/trimbar_only.png")
    w.close(); pump(0.5)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED"); sys.exit(1 if fails else 0)
