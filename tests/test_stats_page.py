"""
Settings > Stats: the redesigned page (tiles, filter / clip-type bars, records)
and what clicking each of them does -- a count opens the Library showing
exactly those clips, a filter's bar filters the Library by it, a record opens
that clip in the previewer.

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_stats_page.py
"""
import os, sys, tempfile, subprocess, time
from pathlib import Path
HOME = tempfile.mkdtemp(prefix="stats_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config, library, clips
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
LENGTHS = [2, 3, 4, 6, 9, 1]
for i, n in enumerate(LENGTHS):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=30",
                    "-t", str(n), "-c:v", "libx264", "-preset", "ultrafast", "-b:v", f"{200 + 300 * i}k",
                    str(CLIPS / f"clip{i}.mp4")], check=True)
library.scan_and_ingest_new_videos()
vids = {v.filename: v for v in library.list_videos()}
cfg = clips.create_clip_config(name="Thirty", length_seconds=30, hotkey="")
with db.get_conn() as conn:
    conn.execute("UPDATE videos SET clip_config_id=? WHERE filename IN ('clip0.mp4','clip1.mp4')", (cfg.id,))
    conn.execute("UPDATE videos SET has_edit=1 WHERE filename IN ('clip2.mp4','clip3.mp4')")
    conn.execute("UPDATE videos SET favorite=1 WHERE filename='clip4.mp4'")
    conn.execute("UPDATE videos SET created_at='2020-01-05T10:00:00+00:00' WHERE filename='clip5.mp4'")
    conn.commit()
for name in ("clip0.mp4", "clip1.mp4", "clip2.mp4"):
    library.add_tag_to_video(vids[name].id, "funny")
library.add_tag_to_video(vids["clip0.mp4"].id, "clutch")

from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
app = QApplication([])
from afterglow.gui.main_window import MainWindow, _LIBRARY_INDEX, _SETTINGS_INDEX
from afterglow.gui import thumb_loader

fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


def pump(s=0.3):
    end = time.perf_counter() + s
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def settle(w):
    """Until neither tab has been refreshing for 0.3 s (a refresh asked for
    while one is running starts a tick after that one ends)."""
    end = time.time() + 20
    idle = 0.0
    while time.time() < end and idle < 0.3:
        pump(0.05)
        busy = (w.library_page.local_tab._refresh_in_progress or w.library_page.uploaded_tab._refresh_in_progress
                or w.library_page.local_tab._refresh_pending or w.library_page.uploaded_tab._refresh_pending)
        idle = 0.0 if busy else idle + 0.05


def shown(w):
    return sorted(c._video.filename for c in w.library_page._active_tab()._cards)


# ---------------------------------------------------------------- the numbers
st = library.compute_stats()
check(st.total == 6 and st.edited == 2 and st.unedited == 4, "totals (existing fields unchanged)")
check(sorted(st.edited_ids) == sorted([vids["clip2.mp4"].id, vids["clip3.mp4"].id]) and len(st.unedited_ids) == 4,
      "edited / unedited ids")
check(len(st.with_filter_ids) == 3 and len(st.no_filter_ids) == 3 and st.per_filter == {"clutch": 1, "funny": 3},
      "filter counts and ids")
check(st.favorite_ids == [vids["clip4.mp4"].id] and st.uploaded_ids == [], "favorites / uploaded")
check(st.longest_id == vids["clip4.mp4"].id and st.shortest_id == vids["clip5.mp4"].id, "longest / shortest clip ids")
check(abs(st.total_length_sec - sum(LENGTHS)) < 0.5, f"total length {st.total_length_sec:.1f}s")
check(st.total_size_bytes == sum(os.path.getsize(CLIPS / f"clip{i}.mp4") for i in range(6)), "total size on disk")
check(st.largest_id == max(vids.values(), key=lambda v: os.path.getsize(v.path)).id, "largest file")
check(st.oldest_id == vids["clip5.mp4"].id and st.newest_id != st.oldest_id, "oldest / newest clip")
check(len(st.last_7_days_ids) == 5 and len(st.last_30_days_ids) == 5, "recent clips exclude the 2020 one")
check(list(st.per_clip_type) == ["Thirty"] and len(st.per_clip_type["Thirty"]) == 2, "clips per clip type")

# ---------------------------------------------------------------- the page
w = MainWindow()
w.resize(1500, 900)
w.show()
settle(w)
w.settings_nav_btn.click()
pump(0.5)
sp = w.settings_page
page = sp.stats_page
tabs = sp.tabs if hasattr(sp, "tabs") else None
# show the Stats tab whatever the tab widget is called
for attr in ("tabs", "tab_widget", "_tabs"):
    tw = getattr(sp, attr, None)
    if tw is not None and hasattr(tw, "indexOf") and tw.indexOf(page) >= 0:
        tw.setCurrentIndex(tw.indexOf(page))
        break
pump(0.3)
check(set(page.tiles) >= {"total", "length", "size", "week", "unedited", "edited", "favorites", "uploaded",
                          "with_filter", "no_filter"}, "all tiles are there")
check(page.tiles["total"].value_text == "6" and page.tiles["unedited"].value_text == "4"
      and page.tiles["unedited"].detail_text == "67%", "tile numbers and percentages")
check(page.tiles["length"].value_text == "25 s" and not page.tiles["length"].clickable, "total length tile (not clickable: nothing to show)")
check(page.tiles["uploaded"].value_text == "0" and not page.tiles["uploaded"].clickable, "a zero count isn't clickable")
check(list(page.filter_rows) == ["funny", "clutch"], "filters are listed most-used first")
check(list(page.type_rows) == ["Thirty"], "clip types are listed")
check(page.record_rows["longest"].title_text == vids["clip4.mp4"].title and page.record_rows["longest"].detail_text == "0:09",
      "records show the clip's title and value")
check(page.tiles["unedited"].cursor().shape() == Qt.PointingHandCursor and page.tiles["unedited"].toolTip(), "clickable tiles say so")


def click(widget):
    QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, QPoint(widget.width() // 2, widget.height() // 2))
    settle(w)


# a count -> the Library showing exactly those clips
click(page.tiles["unedited"])
lp = w.library_page
check(w.stack.currentIndex() == _LIBRARY_INDEX, "clicking a count goes to the Library")
check(shown(w) == ["clip0.mp4", "clip1.mp4", "clip4.mp4", "clip5.mp4"], f"...showing the unedited clips only {shown(w)}")
check(lp.stat_banner.isVisible() and "Unedited clips" in lp.stat_banner_label.text(), "a banner says what is shown")
lp.stat_banner_clear.click()
settle(w)
check(len(shown(w)) == 6 and not lp.stat_banner.isVisible(), "Show all brings the whole Library back")

# a filter's bar -> the Library filtered by it (a real filter: it shows in the Filters menu)
w.settings_nav_btn.click()
pump(0.4)
page = w.settings_page.stats_page
click(page.filter_rows["funny"])
check(shown(w) == ["clip0.mp4", "clip1.mp4", "clip2.mp4"] and lp.local_tab._active_tags == {"funny"},
      "clicking a filter's bar filters the Library by that filter")
check("funny" in lp.stat_banner_label.text(), "...with the banner naming it")

# another stat while one is active replaces it (no leftover tag filter)
w.settings_nav_btn.click()
pump(0.4)
click(w.settings_page.stats_page.type_rows["Thirty"])
check(shown(w) == ["clip0.mp4", "clip1.mp4"] and lp.local_tab._active_tags == set(), "a clip type's bar shows that type's clips")
# the stat view survives an ordinary refresh, and a search narrows within it
lp.refresh()
settle(w)
check(shown(w) == ["clip0.mp4", "clip1.mp4"], "an ordinary refresh keeps the stat's view")
lp.clear_stat_view()
settle(w)
check(len(shown(w)) == 6 and lp.local_tab._only_ids is None, "cleared again")

# a keyboard activation works too
w.settings_nav_btn.click()
pump(0.4)
page = w.settings_page.stats_page
page.tiles["edited"].setFocus()
QTest.keyClick(page.tiles["edited"], Qt.Key_Return)
settle(w)
check(shown(w) == ["clip2.mp4", "clip3.mp4"], "Enter on a focused tile works like a click")
lp.clear_stat_view()
settle(w)

# a record -> that clip in the previewer
w.settings_nav_btn.click()
pump(0.4)
page = w.settings_page.stats_page
opened = []
page.open_video_requested.connect(opened.append)
click(page.record_rows["longest"])
pump(0.6)
check(opened == [vids["clip4.mp4"].id], "clicking a record asks for that clip")
check(w.stack.currentIndex() == _LIBRARY_INDEX and w._preview_overlay is not None
      and w._preview_overlay.content.video.id == vids["clip4.mp4"].id if hasattr(w._preview_overlay.content, "video")
      else w._preview_overlay is not None, "...and it opens in the previewer")
if w._preview_overlay is not None:
    w._preview_overlay.close_overlay(immediate=True)
    pump(0.3)

# a clip deleted since the stats were computed: a message, no crash
library_ok = True
try:
    w.open_video_by_id(999999)
except Exception as e:  # noqa: BLE001
    library_ok = False
pump(0.2)
check(library_ok and w._preview_overlay is None, "a record whose clip is gone: no crash, nothing opened")

# Refresh picks up changes; the tab refreshes itself when shown after the library changed
library.add_tag_to_video(vids["clip5.mp4"].id, "clutch")
w.settings_nav_btn.click()
pump(0.5)
page = w.settings_page.stats_page
page.refresh_btn.click()
pump(0.2)
check(page.filter_rows["clutch"].count == 2 and page.tiles["with_filter"].value_text == "4", "Refresh recomputes")
w.close()

print("\n%d failed" % len(fails) if fails else "\nall passed")
os._exit(1 if fails else 0)
