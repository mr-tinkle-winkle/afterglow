"""
Previewer trimming, driven with REAL mouse events and the REAL mpv player.
Needs an X display with OpenGL (mpv renders through OpenGL). Headless:

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_previewer_trim.py

(On a desktop, just run it.) Uses a throwaway HOME and a generated clip.
"""
import os, sys, tempfile, subprocess
HOME = tempfile.mkdtemp(prefix="prev_trim_home_")
os.environ["HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import time
from afterglow import db, config; db.init_db()
_clips = config.load().clips_path(); _clips.mkdir(parents=True, exist_ok=True)
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
                "-f", "lavfi", "-i", "sine=frequency=440", "-t", "10", "-c:v", "libx264", "-preset", "ultrafast",
                "-g", "15", "-c:a", "aac", str(_clips / "ten second clip.mp4")], check=True)
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint
app = QApplication([])
from afterglow.gui.main_window import MainWindow
from afterglow import library
def pump(s):
    end = time.perf_counter() + s
    while time.perf_counter() < end: app.processEvents(); time.sleep(0.005)
def dur(path): return float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",path]))
fails = []
def check(c, m): print(("PASS " if c else "FAIL ") + m); c or fails.append(m)

w = MainWindow(); w.resize(1600, 900); w.show(); pump(3)
library.scan_and_ingest_new_videos()
v = next(x for x in library.list_videos() if x.title.startswith("ten second"))
w._show_preview_overlay(v, None); pump(2.5)
c = w._preview_overlay.content; tl = c.trim_timeline
check(abs(c._duration - 10) < 0.1 and abs(tl.end - c._duration) < 0.01, f"duration known + full trim range ({c._duration:.2f})")
check(not c.undo_edits_btn.isEnabled(), "Undo Edits disabled for an unedited clip")
y = tl.height() // 2
def xy(t): return QPoint(round(tl._time_to_x(t)), y)
def right_drag(t_from, t_to):
    QTest.mousePress(tl, Qt.RightButton, Qt.NoModifier, xy(t_from)); pump(0.05)
    for i in range(1, 11):
        t = t_from + (t_to - t_from) * i / 10
        QTest.mouseMove(tl, xy(t)); pump(0.03)
    QTest.mouseRelease(tl, Qt.RightButton, Qt.NoModifier, xy(t_to)); pump(0.6)

c.video_widget.pause(); pump(0.3)
right_drag(9.95, 5.0)
mpv_pos = c.video_widget._mpv.time_pos
check(abs(tl.end - 5.0) < 0.15 and abs(c._preview_end - tl.end) < 1e-6, f"right-drag end handle -> end={tl.end:.2f}, committed on release")
check(c.video_widget.is_paused, "dragging a handle pauses playback")
check(mpv_pos is not None and abs(mpv_pos - tl.end) < 0.2, f"PLAYER itself is at the dragged handle (mpv time-pos={mpv_pos})")
right_drag(0.05, 2.0)
check(abs(c.video_widget._mpv.time_pos - tl.start) < 0.2, f"PLAYER follows the start handle too (mpv time-pos={c.video_widget._mpv.time_pos:.2f})")
check(abs(tl.start - 2.0) < 0.15, f"right-drag start handle -> start={tl.start:.2f}")
check("Selected: 0:03" in c.trim_range_label.text(), f"range label: {c.trim_range_label.text()!r}")

QTest.mouseClick(c.play_pause_btn, Qt.LeftButton); pump(4.5)
check(c.video_widget.is_paused and abs(c._current_pos - tl.end) < 0.3, f"playback stops at trim end (pos={c._current_pos:.2f}, end={tl.end:.2f})")
QTest.mouseClick(c.play_pause_btn, Qt.LeftButton); pump(0.6)
check(abs(c._current_pos - tl.start) < 0.8 and not c.video_widget.is_paused, f"play at end restarts from trim start (pos={c._current_pos:.2f})")
c.video_widget.pause(); pump(0.2)

QTest.mouseClick(c.save_trim_btn, Qt.LeftButton); pump(2)
d = dur(v.path); v2 = library.get_video(v.id)
check(abs(d - 3.0) < 0.35 and v2.has_edit, f"Save Trim -> file is ~3s ({d:.2f}s), marked edited")
check(abs(c._duration - d) < 0.2 and abs(c.trim_timeline.end - c._duration) < 0.01, f"previewer reloads the trimmed clip ({c._duration:.2f}s, full range)")
check(c.undo_edits_btn.isEnabled(), "Undo Edits enabled after trimming")
QTest.mouseClick(c.undo_edits_btn, Qt.LeftButton); pump(2)
check(abs(dur(v.path) - 10) < 0.1 and abs(c._duration - 10) < 0.2, f"Undo Edits restores the 10s original ({dur(v.path):.2f}s)")

c.frame_perfect_checkbox.setChecked(True)
tl.set_range(2.3, 4.7); c._on_trim_drag_finished(2.3, 4.7)
QTest.mouseClick(c.save_trim_btn, Qt.LeftButton); pump(3)
check(abs(dur(v.path) - 2.4) < 0.1, f"Frame Perfect trim is exact ({dur(v.path):.3f}s vs 2.4)")
QTest.mouseClick(c.undo_edits_btn, Qt.LeftButton); pump(2)

QTest.mouseClick(c.fullscreen_btn, Qt.LeftButton); pump(1.2)
check(c._transport_box.parent() is c and c.trim_timeline.isVisible() and c.save_trim_btn.isVisible(),
      "fullscreen: trim controls float with the transport controls")
QTest.mouseClick(c.fullscreen_btn, Qt.LeftButton); pump(1)
check(c._transport_box.parent() is not c or c._transport_box.isVisible(), "exit fullscreen restores layout")
w.close(); pump(0.5)
print("\nALL PASS" if not fails else f"\n{len(fails)} FAILED")

sys.exit(1 if fails else 0)
