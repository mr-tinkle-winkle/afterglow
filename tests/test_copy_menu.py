"""
The card's Copy quick action: left click = the default (Copy as .mp4, the "Auto Copy as MP4"
setting), right-click = a menu with Copy / Copy as .mp4 / Copy with input overlay / Copy with input
overlay as .mp4 (the default marked; the overlay ones disabled for a clip without an overlay).
The overlay copies are REAL renders with the overlay burned in (checked by pixels), in the clip's
container or a real .mp4, cached until the clip or its overlay placements change; progress shows on
the card's badge.

    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_copy_menu.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="copy_menu_home_")
os.environ["HOME"] = HOME
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config, library, input_overlay as aio, overlay_support as osup  # noqa: E402

db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
for name, ext, acodec in (("With Overlay", "mkv", "pcm_s16le"), ("Plain", "mp4", "aac")):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=4",
                    "-f", "lavfi", "-i", "sine=d=4", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", acodec,
                    str(CLIPS / f"{name}.{ext}")], check=True)
library.scan_and_ingest_new_videos()
vids = {v.title: v for v in library.list_videos()}
v_ov, v_plain = vids["With Overlay"], vids["Plain"]
ts = time.time()
PL = osup.resolve_placements({"keyboard": {"x": 0.1, "y": 0.1, "w": 0.4, "rotation": 0}})
job = aio.start_clip(ts, aio.OverlaySettings(pieces=["keyboard", "mouse"], fps=30, placements=PL, visible_by_default=True))
aio.finish_clip(job, v_ov.path, clip_end=ts)
job.cleanup()
osup.save_placements(osup.load_sidecar(v_ov.path), PL)

from PySide6.QtCore import Qt, QPoint, QTimer  # noqa: E402
from PySide6.QtGui import QContextMenuEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication([])
from afterglow.gui.video_card import VideoCard  # noqa: E402
from afterglow.gui import copy_actions as ca  # noqa: E402

fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


def wait(cond, timeout=30.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def clip_file():
    urls = QApplication.clipboard().mimeData().urls()
    return Path(urls[0].toLocalFile()) if urls else None


def streams(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "json", str(p)],
                         capture_output=True, text=True).stdout
    return [s["codec_name"] for s in json.loads(out).get("streams", [])]


def overlay_pixels(p):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", "2", "-i", str(p), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return sum(1 for i in range(0, len(raw) - 2, 21) if not (raw[i] < 50 and raw[i + 1] < 50 and raw[i + 2] > 190))


card = VideoCard(library.get_video(v_ov.id))
card.resize(card.sizeHint())
card.show()
plain_card = VideoCard(library.get_video(v_plain.id))
plain_card.show()
app.processEvents()
check(hasattr(card, "copy_btn") and card.copy_btn.contextMenuPolicy() == Qt.CustomContextMenu,
      "the card's Copy quick action takes a right-click")

# ---- the menu (opened by a real context-menu event on the button)
seen = {}


def inspect_and_pick(c, pick=None):
    def go():
        m = c._copy_menu
        seen["items"] = [(a.text(), a.isEnabled()) for a in m.actions()]
        if pick is None:
            m.close()
        else:                               # as a user picks it: highlight, Enter
            from PySide6.QtTest import QTest
            m.setActiveAction(next(a for a in m.actions() if a.text().split("  ")[0] == pick))
            QTest.keyClick(m, Qt.Key_Return)
    QTimer.singleShot(300, go)


inspect_and_pick(card)
QApplication.sendEvent(card.copy_btn, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5)))
app.processEvents()
items = seen.get("items", [])
check([t.split("  ")[0] for t, _ in items] == ["Copy", "Copy as .mp4", "Copy with input overlay",
                                               "Copy with input overlay as .mp4"], f"four ways to copy ({items})")
check([t for t, _ in items if "(default)" in t] == ["Copy as .mp4  (default)"], "Copy as .mp4 is marked default")
check(all(e for _, e in items), "all enabled for a clip with an input overlay")
inspect_and_pick(plain_card)
QApplication.sendEvent(plain_card.copy_btn, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5)))
app.processEvents()
check([e for _, e in seen["items"]] == [True, True, False, False], "no overlay: the overlay copies are disabled")

# ---- left click = the default: as .mp4 (a rename, same bytes)
card.copy_btn.click()
f = clip_file()
check(f and f.suffix == ".mp4" and f.name == "With Overlay.mp4" and f.read_bytes() == Path(v_ov.path).read_bytes(),
      f"left click: copied as .mp4 (the same bytes, renamed) ({f})")

# ---- Copy (normally)
card._copy_as("plain")
check(clip_file() == Path(v_ov.path), "Copy: the clip's own file")

# ---- Copy with input overlay (same container)
QApplication.clipboard().clear()
badges = []
ca.bridge().progress.connect(lambda vid, fr: badges.append(card.upload_badge.text()))
inspect_and_pick(card, "Copy with input overlay")
QApplication.sendEvent(card.copy_btn, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5)))
check(wait(lambda: clip_file() is not None and clip_file().parent.parent == ca.CACHE_DIR, 60),
      "picked from the menu: the overlay copy lands on the clipboard when rendered")
f = clip_file()
check(f.suffix == ".mkv" and streams(f) == ["h264", "pcm_s16le"], f"...in the clip's container, audio kept ({f.name}, {streams(f)})")
check(overlay_pixels(f) > 500 and overlay_pixels(v_ov.path) == 0, "...with the overlay burned in (the original has none)")
check(any(b.startswith("Rendering overlay") for b in badges), f"progress shows on the card ({badges[:2]})")
check(wait(lambda: card.upload_badge.text() == "Copied with overlay", 5), "...then 'Copied with overlay'")
check(wait(lambda: card.upload_badge.isHidden(), 5), "...and the badge goes back to normal")
check(Path(v_ov.path).read_bytes() and osup.has_sidecar(v_ov.path), "the library clip is untouched")

# ---- Copy with input overlay as .mp4 (a real mp4)
QApplication.clipboard().clear()
card._copy_as("overlay_mp4")
check(wait(lambda: clip_file() is not None and clip_file().suffix == ".mp4", 60), "as .mp4: rendered")
f = clip_file()
check(streams(f) == ["h264", "aac"], f"...a real .mp4 (H.264 + AAC; the PCM audio converted) ({streams(f)})")
check(overlay_pixels(f) > 500, "...overlay burned in")

# ---- cache: reused, re-rendered after the placements change
mt = f.stat().st_mtime
card._copy_as("overlay_mp4")
app.processEvents()
check(clip_file() == f and f.stat().st_mtime == mt and not ca.is_rendering(v_ov.id), "copying again reuses the render")
time.sleep(1.1)
pl2 = osup.resolve_placements({"keyboard": {"x": 0.5, "y": 0.5, "w": 0.4, "rotation": 0}})
osup.save_placements(osup.load_sidecar(v_ov.path), pl2)
card._copy_as("overlay_mp4")
check(ca.is_rendering(v_ov.id), "moved the overlay: rendered again")
check(wait(lambda: not ca.is_rendering(v_ov.id), 60) and f.stat().st_mtime > mt, "...fresh file")

# ---- the default follows the setting
s = config.load()
s.auto_copy_as_mp4 = False
config.save(s)
inspect_and_pick(card)
QApplication.sendEvent(card.copy_btn, QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5)))
app.processEvents()
check([t for t, _ in seen["items"] if "(default)" in t] == ["Copy  (default)"], "Auto Copy as MP4 off: Copy is the default")
card.copy_btn.click()
check(clip_file() == Path(v_ov.path), "...and the left click copies the file as it is")

# ---- the Uploaded tab's Copy is "Copy link": no menu there
up = VideoCard(library.get_video(v_plain.id), uploaded_view=True)
check(not hasattr(up, "copy_btn"), "Uploaded cards keep their plain Copy link button")

print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
sys.exit(1 if fails else 0)
