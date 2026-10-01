"""
Previewer input-overlay toggle, driven with the REAL previewer + REAL mpv
(needs an X display with OpenGL, like the other previewer suites):

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_overlay_previewer.py
"""
import os, sys, tempfile, subprocess, time
from pathlib import Path
HOME = tempfile.mkdtemp(prefix="prev_overlay_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config
db.init_db()
clips_dir = config.load().clips_path(); clips_dir.mkdir(parents=True, exist_ok=True)
for name, color in (("with overlay", "blue"), ("plain clip", "green")):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=30:d=8",
                    "-f", "lavfi", "-i", "sine=frequency=440:d=8", "-c:v", "libx264", "-preset", "ultrafast", "-g", "15",
                    "-c:a", "aac", str(clips_dir / f"{name}.mp4")], check=True)
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
app = QApplication([])
from afterglow.gui.main_window import MainWindow
from afterglow import library, input_overlay as aio, overlay_support as osup

def pump(s):
    end = time.perf_counter() + s
    while time.perf_counter() < end: app.processEvents(); time.sleep(0.005)
fails = []
def check(c, m): print(("PASS " if c else "FAIL ") + m); c or fails.append(m)

w = MainWindow(); w.resize(1600, 900); w.show(); pump(3)
library.scan_and_ingest_new_videos()
vids = {v.title: v for v in library.list_videos()}
v_ov, v_plain = vids["with overlay"], vids["plain clip"]
ts = time.time()
pl = osup.resolve_placements({"keyboard": {"x": 0.1, "y": 0.1, "w": 0.4, "rotation": 20}})
job = aio.start_clip(ts, aio.OverlaySettings(pieces=["keyboard", "mouse"], fps=30, placements=pl, visible_by_default=True))
aio.finish_clip(job, v_ov.path, clip_end=ts); job.cleanup()
osup.save_placements(osup.load_sidecar(v_ov.path), pl)

w._show_preview_overlay(v_plain, None); pump(2.5)
c = w._preview_overlay.content
check(c.overlay_btn.isHidden(), "a clip without an overlay sidecar shows no overlay toggle")
check(c.video_widget._mpv["lavfi-complex"] in ("", None), "...and no graph is set")
w._preview_overlay.close_overlay(immediate=True); pump(0.5)

w._show_preview_overlay(v_ov, None); pump(2.5)
c = w._preview_overlay.content
mpvh = c.video_widget._mpv
check(not c.overlay_btn.isHidden(), "a clip with an overlay shows the toggle in the header")
check(c._overlay_on and "overlay=" in str(mpvh["lavfi-complex"]) and "rotate=" in str(mpvh["lavfi-complex"]),
      "initial state = visible_by_default: graph set, with the rotation step")
check(len(mpvh["external-files"]) == 2, "both pieces were handed to mpv as external files")
tracks = [t for t in mpvh.track_list if t["type"] == "video"]
check(len(tracks) == 3, f"mpv sees the main video + 2 overlay tracks ({len(tracks)})")
tip_on = c.overlay_btn.toolTip()
QTest.mouseClick(c.overlay_btn, Qt.LeftButton); pump(0.5)
check(not c._overlay_on and "overlay=" not in str(mpvh["lavfi-complex"]) and c.overlay_btn.toolTip() != tip_on,
      "clicking the toggle turns the overlay off (graph swapped, tooltip/icon updated)")
QTest.mouseClick(c.overlay_btn, Qt.LeftButton); pump(0.5)
check(c._overlay_on and "overlay=" in str(mpvh["lavfi-complex"]), "clicking again turns it back on")

# off, then reload the clip (Save Trim): the choice sticks for this clip
QTest.mouseClick(c.overlay_btn, Qt.LeftButton); pump(0.3)
c.video_widget.pause(); c.trim_timeline.set_range(1.0, 5.0); c._on_trim_drag_finished(1.0, 5.0)
c.frame_perfect_checkbox.setChecked(True)
QTest.mouseClick(c.save_trim_btn, Qt.LeftButton); pump(3)
check(not c._overlay_on and not c.overlay_btn.isHidden(), "after Save Trim the overlay stays off and its toggle remains")
sc = osup.load_sidecar(library.get_video(v_ov.id).path)
d = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", v_ov.path]))
dk = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                    str(Path(sc["dir"]) / "keyboard.mov")]))
check(abs(d - dk) < 0.1, f"the sidecar was trimmed with the clip ({dk:.2f}s vs {d:.2f}s)")
QTest.mouseClick(c.overlay_btn, Qt.LeftButton); pump(0.5)
check(len(c.video_widget._mpv["external-files"]) == 2 and "overlay=" in str(c.video_widget._mpv["lavfi-complex"]),
      "trimmed clip + trimmed pieces play with the overlay on")
QTest.mouseClick(c.undo_edits_btn, Qt.LeftButton); pump(2.5)
check(abs(c._duration - 8) < 0.3, f"Undo Edits reloads the original clip ({c._duration:.2f}s)")

# switching to a clip without an overlay (what Prev/Next does) drops the graph
c._load_video(library.get_video(v_plain.id)); pump(1.5)
check(c.overlay_btn.isHidden() and c.video_widget._mpv["lavfi-complex"] in ("", None) and not c.video_widget._mpv["external-files"],
      "loading a clip without an overlay clears the previous clip's graph and files")
w._preview_overlay.close_overlay(immediate=True); pump(0.3)

# a just-captured clip: its overlay is still rendering when the previewer opens
# (`<clip>.input.new/` exists) -> it appears in place once it lands
late = clips_dir / "late.mp4"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=30:d=8",
                "-c:v", "libx264", "-preset", "ultrafast", str(late)], check=True)
v_late = library.add_video(late, title="late")
pending = osup.sidecar_dir(late).with_name(osup.sidecar_dir(late).name + ".new")
pending.mkdir()
w._show_preview_overlay(v_late, None); pump(2.0)
c = w._preview_overlay.content
check(c.overlay_btn.isHidden(), "overlay still rendering: no toggle yet")
ts2 = time.time()
job = aio.start_clip(ts2, aio.OverlaySettings(pieces=["keyboard"], fps=30, visible_by_default=True))
aio.finish_clip(job, late, clip_end=ts2); job.cleanup()
pending.rmdir()
pump(3.5)
check(not c.overlay_btn.isHidden() and "overlay=" in str(c.video_widget._mpv["lavfi-complex"])
      and len(c.video_widget._mpv["external-files"]) == 1,
      "when the overlay lands, the toggle appears and the overlay plays without reopening")
w._preview_overlay.close_overlay(immediate=True); pump(0.3)

print()
print("ALL PASS" if not fails else f"{len(fails)} FAILED")
sys.exit(1 if fails else 0)
