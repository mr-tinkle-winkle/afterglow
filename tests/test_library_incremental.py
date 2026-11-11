"""
The Library keeps what it has:

  * a refresh after one clip changed (favourite, filter, rename, edit,
    delete, a new capture) builds ONLY that clip's card -- every other card
    is the same widget as before, in place, with the selection and the scroll
    position kept
  * sorting / searching re-orders or hides the existing cards
  * a change that affects every card (a settings change) still rebuilds all
  * an empty grid (the app just opened) fills progressively: the first
    screenful is there -- thumbnails included -- before the event loop runs

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_library_incremental.py
"""
import os, sys, tempfile, subprocess, time, shutil
from pathlib import Path
HOME = tempfile.mkdtemp(prefix="incr_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import db, config, library, thumbnails
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
N = 40
src = os.path.join(HOME, "_src.mp4")
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-t", "1",
                "-c:v", "libx264", "-preset", "ultrafast", src], check=True)
for i in range(N):
    dst = CLIPS / f"clip{i:02d}.mp4"
    shutil.copy(src, dst)
    os.utime(dst, (time.time() - i * 3600, time.time() - i * 3600))
library.scan_and_ingest_new_videos()
for v in library.list_videos():                 # thumbnails cached, as on every start but the first
    thumbnails.get_card_thumbnail(v.id, Path(v.path))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea
app = QApplication([])
from afterglow.gui.main_window import MainWindow
from afterglow.gui import video_card, thumb_loader

fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


def pump(s=0.2):
    end = time.perf_counter() + s
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.004)


def settle(tab):
    end = time.time() + 20
    idle = 0.0
    while time.time() < end and idle < 0.3:
        pump(0.05)
        idle = 0.0 if (tab._refresh_in_progress or tab._refresh_pending) else idle + 0.05


def placeholder_keys():
    return {video_card._loading_pixmap(r).cacheKey() for r in list(video_card._LOADING_PIXMAPS)}


# ---------------------------------------------------------------- opening
w = MainWindow()                                 # no event loop has run yet
tab = w.library_page.local_tab
first = list(tab._cards)
check(len(first) == tab._FIRST_BATCH, f"the first screenful ({len(first)} cards) exists as soon as the window is built")
check(tab._refresh_in_progress and len(tab._build_queue) == N - tab._FIRST_BATCH, "...while the rest is still to be built")
check(all(c.thumb_label.pixmap().cacheKey() not in placeholder_keys() for c in first),
      "its thumbnails are already in (read from the cached files, no placeholder)")
check(not thumb_loader.loader().busy(), "...without going through the background loader")
w.resize(1500, 900)
w.show()
settle(tab)
thumb_loader.loader().wait_idle(20)
check(len(tab._cards) == N and tab._cards[:len(first)] == first, "the rest is added underneath; the first cards are not rebuilt")
check([c._video.filename for c in tab._cards] == [f"clip{i:02d}.mp4" for i in range(N)], "in the right order (newest first)")
check(all(c.thumb_label.pixmap().cacheKey() not in placeholder_keys() for c in tab._cards), "every card has its thumbnail")
cols = tab._current_columns
pos_ok = all(tab.grid_layout.getItemPosition(tab.grid_layout.indexOf(c))[:2] == divmod(i, cols)
             for i, c in enumerate(tab._cards))
check(pos_ok, f"every card sits in its grid cell ({cols} columns)")

built = []
orig_make = tab._make_card


def counting_make(video, eager_thumbnail=False):
    built.append(video.filename)
    return orig_make(video, eager_thumbnail=eager_thumbnail)


tab._make_card = counting_make


def refresh_and_diff():
    before = {c.video_id: c for c in tab._cards}
    built.clear()
    tab.refresh()
    settle(tab)
    after = {c.video_id: c for c in tab._cards}
    same = [i for i in after if i in before and after[i] is before[i]]
    return before, after, same


by_name = lambda: {c._video.filename: c for c in tab._cards}
vid = lambda name: by_name()[name].video_id

# ---------------------------------------------------------------- one clip changes
bar = tab.scroll.verticalScrollBar()
bar.setValue(bar.maximum() // 2)
pump(0.2)
scroll_at = bar.value()
tab._on_card_clicked(vid("clip03.mp4"), Qt.NoModifier)
tab._on_card_clicked(vid("clip20.mp4"), Qt.ControlModifier)

library.set_favorite(vid("clip05.mp4"), True)
before, after, same = refresh_and_diff()
check(built == ["clip05.mp4"] and len(same) == N - 1, f"favouriting one clip builds one card ({built}); the other {len(same)} are kept")
check(by_name()["clip05.mp4"]._video.favorite, "...and the new card shows the change")
check(tab._selected_ids == {vid("clip03.mp4"), vid("clip20.mp4")} and by_name()["clip03.mp4"]._selected
      and by_name()["clip20.mp4"]._selected, "the selection survives")
check(abs(bar.value() - scroll_at) <= 2, f"the scroll position stays ({scroll_at} -> {bar.value()})")
check([c._video.filename for c in tab._cards] == [f"clip{i:02d}.mp4" for i in range(N)], "the order is unchanged")

library.add_tag_to_video(vid("clip07.mp4"), "funny")
before, after, same = refresh_and_diff()
check(built == ["clip07.mp4"] and len(same) == N - 1, "adding a filter to one clip builds one card")

id09 = vid("clip09.mp4")
library.rename_video(id09, title="A new name")          # (renames the file as well)
before, after, same = refresh_and_diff()
check(len(built) == 1 and after[id09] is not before[id09] and after[id09]._video.title == "A new name"
      and len(same) == N - 1, f"renaming one clip builds one card ({built})")

# an edit rewrites the file (new mtime) and flags the row
p10 = Path(by_name()["clip10.mp4"]._video.path)
os.utime(p10, (time.time() + 5, time.time() + 5))
before, after, same = refresh_and_diff()
check(built == ["clip10.mp4"] and len(same) == N - 1, "a clip whose file was rewritten (an edit) builds one card")
thumb_loader.loader().wait_idle(20)
check(by_name()["clip10.mp4"].thumb_label.pixmap().cacheKey() not in placeholder_keys(), "...which gets its new thumbnail")

# delete: nothing is built at all
gone_id = vid("clip20.mp4")
old_card = by_name()["clip20.mp4"]
library.delete_video(gone_id)
before, after, same = refresh_and_diff()
check(built == [] and len(tab._cards) == N - 1 and gone_id not in after and len(same) == N - 1,
      "deleting a clip builds nothing: its card is removed, the rest are kept")
check(not old_card.isVisible() and tab.grid_layout.indexOf(old_card) < 0, "the removed card is hidden and out of the layout")
check(tab._selected_ids == {vid("clip03.mp4")}, "the deleted clip leaves the selection, the other selected clip stays")
names = [c._video.filename for c in tab._cards]
check(all(tab.grid_layout.getItemPosition(tab.grid_layout.indexOf(c))[:2] == divmod(i, tab._current_columns)
          for i, c in enumerate(tab._cards)), "the cards after it move up to close the gap")

# a new capture: one card, at the front
new = CLIPS / "new_capture.mp4"
shutil.copy(src, new)
library.scan_and_ingest_new_videos()
before, after, same = refresh_and_diff()
check(built == ["new_capture.mp4"] and tab._cards[0]._video.filename == "new_capture.mp4" and len(same) == N - 1,
      "a newly captured clip builds one card, placed first")

# nothing changed: nothing happens
before, after, same = refresh_and_diff()
check(built == [] and len(same) == N, "a refresh with nothing changed touches nothing")

# ---------------------------------------------------------------- order and search
order_before = [c._video.filename for c in tab._cards]
objs = {c.video_id: c for c in tab._cards}
built.clear()
tab._set_sort_by(library.SORT_NAME_Z_TO_A)
settle(tab)
order_after = [c._video.filename for c in tab._cards]
check(order_after != order_before and sorted(order_after) == sorted(order_before) and built == []
      and all(objs[c.video_id] is c for c in tab._cards),
      f"changing the sort order re-orders the same cards, building none ({built})")
check(all(tab.grid_layout.getItemPosition(tab.grid_layout.indexOf(c))[:2] == divmod(i, tab._current_columns)
          for i, c in enumerate(tab._cards)), "...each in its new cell")

tab.search_edit.setText("clip1")
settle(tab)
shown = [c._video.filename for c in tab._cards]
check(built == [] and len(shown) == 10 and all(objs[c.video_id] is c for c in tab._cards),
      f"a search keeps the matching cards and builds none ({len(shown)} shown)")
tab.search_edit.setText("")
settle(tab)
check(len(tab._cards) == N and built == [] and all(objs[c.video_id] is c for c in tab._cards)
      and all(c.isVisible() for c in tab._cards),
      f"clearing it brings the hidden cards back without building any ({len(built)})")
check(all(tab.grid_layout.getItemPosition(tab.grid_layout.indexOf(c))[:2] == divmod(i, tab._current_columns)
          for i, c in enumerate(tab._cards)) and set(tab._pool) <= {gone_id},
      "...each back in its cell; the pool holds no clip that is on screen")

# a clip that changes while its card is hidden gets a new card when it comes back
tab.search_edit.setText("clip1")
settle(tab)
hidden_id = next(i for i in objs if i not in {c.video_id for c in tab._cards})
check(hidden_id in tab._pool and not tab._pool[hidden_id][0].isVisible(), "(cards a search hides wait in the pool)")
library.set_favorite(hidden_id, True)
built.clear()
tab.search_edit.setText("")
settle(tab)
now = {c.video_id: c for c in tab._cards}
check(len(built) == 1 and now[hidden_id] is not objs[hidden_id] and now[hidden_id]._video.favorite
      and hidden_id not in tab._pool,
      f"a clip changed while hidden is rebuilt when it returns, the rest are reused ({len(built)} built)")

# ---------------------------------------------------------------- everything changes
built.clear()
objs = {c.video_id: c for c in tab._cards}
tab._highlight_unedited = not tab._highlight_unedited          # a display option of the whole grid
tab.refresh()
settle(tab)
check(len(built) == N and all(objs[c.video_id] is not c for c in tab._cards) and not tab._pool,
      "a change that affects every card rebuilds every card")

# ---------------------------------------------------------------- down to nothing and back
tab.search_edit.setText("nothing matches this")
settle(tab)
check(len(tab._cards) == 0 and tab.empty_label.isVisible() and not tab.scroll.isVisible(), "no match: the empty state")
built.clear()
tab.search_edit.setText("")
settle(tab)
thumb_loader.loader().wait_idle(20)
check(len(tab._cards) == N and tab.scroll.isVisible() and not tab.empty_label.isVisible() and built == [],
      "and back to the full grid, again without building anything")
w.close()

print("\n%d failed" % len(fails) if fails else "\nall passed")
os._exit(1 if fails else 0)
