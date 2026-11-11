"""
Startup work moved off the critical path, and the faster input-overlay capture:

  * card thumbnails load on worker threads (placeholder first, picture after),
    from a small card-sized file; an older full-size thumbnail is reused
    instead of running ffmpeg again; get_thumbnail() still gives the full frame
  * the Settings and Editor pages are built on first use, not at startup
  * the overlay's frame-rate cap (render_fps) and the split start/install render

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_startup_perf.py
"""
import os, sys, tempfile, subprocess, time, json
from pathlib import Path
HOME = tempfile.mkdtemp(prefix="startup_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config, thumbnails
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
for i in range(6):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={'red' if i % 2 else 'blue'}:size=1280x720:rate=30", "-t", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", str(CLIPS / f"clip{i}.mp4")], check=True)

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from afterglow import library, overlay_support as osup
from afterglow.gui import thumb_loader, video_card
from afterglow.gui.main_window import MainWindow, _SETTINGS_INDEX, _EDITOR_INDEX, _LIBRARY_INDEX

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


def centre(pixmap):
    c = pixmap.toImage().pixelColor(pixmap.width() // 2, pixmap.height() // 2)
    return c.red(), c.green(), c.blue()


# ---------------------------------------------------------------- thumbnails
library.scan_and_ingest_new_videos()
videos = library.list_videos(sort_by="title") if "sort_by" in library.list_videos.__code__.co_varnames else library.list_videos()
v0 = next(v for v in videos if v.filename == "clip0.mp4")
v1 = next(v for v in videos if v.filename == "clip1.mp4")

# an older full-size thumbnail exists for clip1 (as left by earlier versions): green, unlike the clip
full1 = thumbnails.CACHE_DIR / f"{v1.id}_{int(Path(v1.path).stat().st_mtime)}.jpg"
thumbnails.CACHE_DIR.mkdir(parents=True, exist_ok=True)
img = QImage(1280, 720, QImage.Format_RGB32)
img.fill(0xFF00FF00)
img.save(str(full1), "JPG")

calls = []
orig_run = subprocess.run


def counting_run(cmd, *a, **k):
    if cmd and cmd[0] == "ffmpeg":
        calls.append(cmd)
    return orig_run(cmd, *a, **k)


thumbnails.subprocess.run = counting_run
t0 = time.perf_counter()
card0 = video_card.VideoCard(v0)
card1 = video_card.VideoCard(v1)
built_in = time.perf_counter() - t0
check(len(calls) == 0 or built_in < 0.5, "building a card doesn't wait for ffmpeg")
check(thumb_loader.loader().busy(), "the thumbnail is requested from the loader")
first = centre(card0.thumb_label.pixmap())
check(first[2] < 100, f"until it arrives the card shows the plain placeholder {first}")
check(thumb_loader.loader().wait_idle(30), "the loader finishes")
got0, got1 = centre(card0.thumb_label.pixmap()), centre(card1.thumb_label.pixmap())
check(got0[2] > 180 and got0[0] < 80, f"then the real thumbnail (blue clip) {got0}")
check(got1[1] > 180 and got1[0] < 80, f"an existing full-size thumbnail is reused for the card (green) {got1}")
ff_inputs = [c[c.index("-i") + 1] for c in calls]
check(str(v0.path) in ff_inputs and str(v1.path) not in ff_inputs,
      "ffmpeg ran for the clip without a thumbnail only, not for the one that had a full-size one")
small0 = thumbnails.card_thumbnail_path(v0.id, Path(v0.path))
small1 = thumbnails.card_thumbnail_path(v1.id, Path(v1.path))
check(small0.exists() and QImage(str(small0)).width() == thumbnails.CARD_THUMB_WIDTH,
      "the card thumbnail on disk is the small size")
check(small1.exists() and QImage(str(small1)).width() <= thumbnails.CARD_THUMB_WIDTH, "...also the one made from the full-size file")
check(card0.thumb_label.pixmap().size() == video_card.THUMB_SIZE, "the card picture is exactly the thumbnail size")
check(not list(thumbnails.CACHE_DIR.glob(".*tmp*")), "no temporary files left in the thumbnail folder")

calls.clear()
card0b = video_card.VideoCard(v0)
check(centre(card0b.thumb_label.pixmap()) == got0 and not thumb_loader.loader().busy() and not calls,
      "a second card for the same clip gets the picture at once from the cache")

full0 = thumbnails.get_thumbnail(v0.id, Path(v0.path))
check(full0 is not None and QImage(str(full0)).width() == 1280, "get_thumbnail() still returns the full-size frame")

# a card deleted before its picture arrives doesn't break the loader
video_card._THUMB_PIXMAP_CACHE.clear()
v2 = next(v for v in videos if v.filename == "clip2.mp4")
gone = video_card.VideoCard(v2)
kept = video_card.VideoCard(v2)
import shiboken6
shiboken6.delete(gone)
check(thumb_loader.loader().wait_idle(30), "a card deleted while its thumbnail loads: the loader still finishes")
check(centre(kept.thumb_label.pixmap())[2] > 180, "...and the other card for that clip still gets its picture")

# a missing file: "No preview", no request
class _Gone:
    pass
import dataclasses
vgone = dataclasses.replace(v2, path=str(CLIPS / "nope.mp4")) if dataclasses.is_dataclass(v2) else None
if vgone is not None:
    cg = video_card.VideoCard(vgone)
    check(not thumb_loader.loader().busy(), "a clip whose file is gone asks for nothing (placeholder)")

# cover scaling: a 4:3 source is centre-cropped to the exact size
p43 = Path(HOME) / "43.jpg"
im = QImage(480, 360, QImage.Format_RGB32)
im.fill(0xFF000000)
for y in range(45, 315):
    for x in range(480):
        im.setPixel(x, y, 0xFFFFFFFF)
im.save(str(p43), "JPG")
cov = thumb_loader.read_scaled_cover(p43, QSize(400, 224))
check(cov is not None and cov.size() == QSize(400, 224) and cov.pixelColor(200, 3).red() > 200,
      "a 4:3 source (letterboxed) is scaled to cover and centre-cropped, bars gone")
check(thumb_loader.read_scaled_cover(Path(HOME) / "missing.jpg", QSize(400, 224)) is None, "an unreadable file gives None")

# ---------------------------------------------------------------- lazy pages
video_card._THUMB_PIXMAP_CACHE.clear()
w = MainWindow()
w.resize(1400, 800)
w.show()
pump(0.3)
check(w._settings_page is None and w._editor_page is None, "Settings and the Editor are not built at startup")
check("afterglow.gui.settings_page" not in sys.modules or w._settings_page is None, "(their widgets don't exist yet)")
check(w.stack.count() == 3 and w.stack.currentIndex() == _LIBRARY_INDEX, "the stack still has three slots, Library showing")
deadline = time.time() + 30
while time.time() < deadline and (w.library_page.local_tab._refresh_in_progress or thumb_loader.loader().busy()):
    pump(0.05)
check(len(w.library_page.local_tab._cards) == 6, "the Library fills as before")
check(all(centre(c.thumb_label.pixmap()) != (0x1b, 0x1b, 0x1b) for c in w.library_page.local_tab._cards),
      "every card ends up with its picture")
w.resize(1500, 850)      # a resize before the Editor exists must not build it
pump(0.1)
check(w._editor_page is None, "resizing doesn't build the Editor")

w.settings_nav_btn.click()
pump(0.6)
check(w._settings_page is not None and w.stack.currentWidget() is w._settings_page and w.stack.count() == 3,
      "clicking Settings builds it and shows it")
check(w._editor_page is None, "...without building the Editor")
sp = w.settings_page
w.library_nav_btn.click()
pump(0.4)
w.settings_nav_btn.click()
pump(0.4)
check(w.settings_page is sp, "the Settings page is built once")
w.settings_page.rebuild_requested.emit()
pump(0.3)
check(w.settings_page is not sp and w.stack.currentWidget() is w.settings_page and w.stack.count() == 3,
      "Import/Reset still swaps in a fresh Settings page")

w.editor_nav_btn.click()
pump(0.6)
check(w._editor_page is not None and w.stack.currentWidget() is w._editor_page and w.stack.count() == 3,
      "clicking Editor builds it and shows it")
check(w.stack.indexOf(w._editor_page) == _EDITOR_INDEX and w.stack.indexOf(w._settings_page) == _SETTINGS_INDEX,
      "both pages sit at their fixed stack positions")
check(w._editor_page._neighbor_provider is not None, "the Editor is wired to the Library when it is built")
w.close()

w2 = MainWindow()
w2.show()
pump(0.3)
vid = library.list_videos()[0].id
w2._open_in_editor(vid)
pump(1.0)
check(w2._editor_page is not None and w2._editor_page.current_video_id == vid and w2.stack.currentIndex() == _EDITOR_INDEX,
      "opening a clip in the Editor from the Library builds the Editor and loads the clip")
w2.close()
w3 = MainWindow()
w3.show()
pump(0.2)
try:
    w3.close()
    ok = True
except Exception as e:  # noqa: BLE001
    ok = False
check(ok and w3._editor_page is None, "closing the window without ever opening the Editor works")

# ---------------------------------------------------------------- overlay speed
check(osup.render_fps(120, 60) == 60 and osup.render_fps(240, 60) == 60, "120 / 240 fps clips render the overlay at 60")
check(abs(osup.render_fps(144, 60) - 48) < 1e-6 and abs(osup.render_fps(165, 60) - 55) < 1e-6,
      "144 -> 48, 165 -> 55: a whole fraction of the clip's rate")
check(osup.render_fps(60, 60) == 60 and osup.render_fps(30, 60) == 30 and abs(osup.render_fps(59.94, 60) - 59.94) < 1e-6,
      "clips at or under the limit keep their own rate")
check(osup.render_fps(120, 0) == 120 and osup.render_fps(120, None) == 120, "0 = match the clip")
check(osup.render_fps(120, 30) == 30, "a 30 fps limit: 120 -> 30")
check(config.load().overlay_max_fps == 60, "the limit defaults to 60 fps")

clip = CLIPS / "clip3.mp4"
buf = Path(HOME) / "inputs.jsonl"
buf.write_text('{"format": "puppetry-input-buffer", "version": 1}\n')
log = Path(HOME) / "calls.jsonl"
os.environ["FAKE_PUPPETRY_LOG"] = str(log)
os.environ["FAKE_PUPPETRY_DELAY"] = "1.5"
end = time.time()
dest_new = osup.sidecar_dir(clip).with_name(osup.sidecar_dir(clip).name + ".new")
t0 = time.perf_counter()
job = osup.start_render(buf, dest_new, end - 2.0, end, 30, ["keyboard", "mouse"])
returned = time.perf_counter() - t0
check(returned < 0.3 and not job.rendered, f"start_render returns at once, pieces still rendering ({returned:.2f}s)")
check(dest_new.is_dir() and not osup.has_sidecar(clip), "meanwhile <clip>.input.new exists and <clip>.input doesn't")
man = osup.install_render(job, buf, clip, end, {})
took = time.perf_counter() - t0
check(set(man["pieces"]) == {"keyboard", "mouse"} and osup.has_sidecar(clip) and not dest_new.exists(),
      "install_render waits and writes <clip>.input")
check(took < 2.6, f"the two pieces rendered in parallel ({took:.2f}s for 2 x 1.5 s; one after the other would be 3 s+)")
sc = osup.load_sidecar(clip)
check(sc["pieces"]["keyboard"]["width"] > 0 and (Path(sc["dir"]) / "inputs.jsonl").exists(), "sizes and the input record are stored")
del os.environ["FAKE_PUPPETRY_DELAY"]
log.write_text("")
osup.delete_sidecar(clip)
man2 = osup.capture_clip(buf, clip, end, ["keyboard"], {}, clip_info=(2.0, 120.0), max_fps=60)
r = [json.loads(l) for l in log.read_text().splitlines() if l.startswith('["render"')]
check(len(r) == 1 and float(r[0][r[0].index("--fps") + 1]) == 60, "capture_clip: a 120 fps clip's overlay is rendered at 60 with the limit on")
log.write_text("")
osup.delete_sidecar(clip)
osup.capture_clip(buf, clip, end, ["keyboard"], {}, clip_info=(2.0, 120.0))
r = [json.loads(l) for l in log.read_text().splitlines() if l.startswith('["render"')]
check(float(r[0][r[0].index("--fps") + 1]) == 120, "...and at 120 without one")
os.environ["FAKE_PUPPETRY_FAIL"] = "keyboard"
osup.delete_sidecar(clip)
try:
    osup.capture_clip(buf, clip, end, ["keyboard"], {}, clip_info=(2.0, 30.0))
    raised = False
except osup.OverlayError:
    raised = True
check(raised and not dest_new.exists() and not osup.has_sidecar(clip), "every piece failing: OverlayError, nothing left behind")

print("\n%d failed" % len(fails) if fails else "\nall passed")
os._exit(1 if fails else 0)
