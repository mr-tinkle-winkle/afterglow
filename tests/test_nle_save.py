"""
End-to-end save flow for the Advanced Editor against a real library DB
and real clips, in an isolated temporary HOME.

    QT_QPA_PLATFORM=offscreen python3 tests/test_nle_save.py
"""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="nle_save_home_")
os.environ["HOME"] = HOME                      # before importing afterglow (paths are computed at import)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow import config, db, library
from afterglow.nle import media, ops, save, store

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def make_clip(path, seconds):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=320x180:r=30:d={seconds}", "-f", "lavfi", "-i",
                    f"aevalsrc=0.4*sin(2*PI*330*t):s=48000:d={seconds}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "15", "-c:a", "aac", path], check=True)


db.init_db()
clips = config.load().clips_path()
clips.mkdir(parents=True, exist_ok=True)
make_clip(str(clips / "clip one.mp4"), 8)
make_clip(str(clips / "clip two.mp4"), 8)
library.scan_and_ingest_new_videos()
videos = {v.title: v for v in library.list_videos()}
one, two = videos["clip one"], videos["clip two"]
orig_hash = sha(one.path)

# ---- first save of an unedited clip --------------------------------------------
p = save.open_for_library_video(one)
check(len(list(p.all_segments())) == 1 and next(p.all_segments()).parts[0].source == str(one.path),
      "new project: one segment referencing the clip")
ops.split_at(p, 2.0)
ops.split_at(p, 5.0)
middle = [s for s in p.all_segments() if abs(s.start - 2.0) < 1e-6][0]
ops.ripple_delete(p, [middle.id])                        # remove 2-5 -> 5s result
check(abs(p.duration - 5.0) < 1e-6, "edited timeline is 5s")
v = save.save_library_clip(p)
backup = Path(one.path).parent / "Edit Backups" / "clip one.orig.mp4"
check(abs(media.probe(str(one.path))["duration"] - 5.0) < 0.1, "clip file overwritten with the 5s render")
check(v.has_edit and v.backup_path == str(backup) and backup.exists() and sha(backup) == orig_hash,
      "original kept byte-for-byte in Edit Backups; DB marks it edited")
check(abs(v.duration_sec - 5.0) < 0.1, "library duration updated")
saved = store.load_project(store.project_path_for_video(one.id))
srcs = {pt.source for s in saved.all_segments() for pt in s.parts}
check(srcs == {str(backup)}, "saved project renders from the backup, never from the clip itself")

# ---- reopen + re-save: renders from the ORIGINAL, not the previous render -------
p2 = save.open_for_library_video(library.get_video(one.id))
check(abs(p2.duration - 5.0) < 1e-6 and len(list(p2.all_segments())) == 2, "reopening restores the tracks and segments")
first, second = sorted(p2.all_segments(), key=lambda s: s.start)
ops.move_segments(p2, [second.id], 1.0)                  # make room after the first segment
ops.trim_end(p2, first.id, first.end + 1.0)              # reveal 1s the FIRST save had cut (src 2-3)
check(abs(first.parts[0].src_out - 3.0) < 1e-6 and abs(p2.duration - 6.0) < 1e-6,
      "extending an edge reveals footage the previous save cut out (src 2-3)")
v2 = save.save_library_clip(p2)
check(abs(media.probe(str(one.path))["duration"] - p2.duration) < 0.1,
      f"re-save renders the new timeline ({p2.duration:.1f}s) from the original footage")
check(sha(backup) == orig_hash and v2.backup_path == str(backup), "backup still the untouched original after re-save")
from afterglow.nle.render import Renderer
from afterglow.nle.model import default_project, Part, Segment
def frame_px(path, t):
    pr = default_project(320, 180, 30)
    i = media.probe(path)
    ops.place(pr, Segment(parts=[Part(source=path, src_out=i["duration"], source_duration=i["duration"])]), 1, 0.0)
    r = Renderer(pr); img = r.frame(t); r.close()
    return [img.pixelColor(x, 90).name() for x in range(20, 320, 40)]
a_px, b_px = frame_px(str(one.path), 2.5), frame_px(str(backup), 2.5)
c_px = frame_px(str(backup), 5.5)   # what the FIRST save showed right after 2.0s (the cut jumped to 5s)
from PySide6.QtGui import QColor
def dist(x, y): 
    cx, cy = QColor(x), QColor(y); return abs(cx.red()-cy.red()) + abs(cx.green()-cy.green()) + abs(cx.blue()-cy.blue())
close = sum(dist(a, b) <= 30 for a, b in zip(a_px, b_px))
check(any(dist(b, c) > 150 for b, c in zip(b_px, c_px)),
      "(sanity) the sampled pixels can tell the 2.5s frame from the 5.5s one")
check(close >= len(a_px) - 1 and any(dist(a, c) > 150 for a, c in zip(a_px, c_px)), "re-saved file at 2.5s shows the original's 2.5s frame (footage restored from the backup)")


# ---- Undo Edits discards the project ---------------------------------------------
library.undo_edit(one.id)
check(sha(one.path) == orig_hash, "Undo Edits restores the original bytes")
check(not store.project_path_for_video(one.id).exists(), "Undo Edits discards the Advanced Editor project")
p3 = save.open_for_library_video(library.get_video(one.id))
check(abs(p3.duration - 8.0) < 0.1, "reopening after undo starts fresh from the 8s original")

# ---- clip already quick-trimmed, then advanced-edited ------------------------------
library.apply_trim(two.id, 1.0, 7.0, frame_perfect=True)     # quick trim -> 6s, backup = 8s original
two = library.get_video(two.id)
two_backup_hash = sha(two.backup_path)
trimmed_hash = sha(two.path)
p4 = save.open_for_library_video(two)
check(abs(p4.duration - 6.0) < 0.1, "advanced editor opens the trimmed (6s) version, as shown in the library")
seg = next(p4.all_segments())
ops.set_speed(p4, seg.id, 2.0)
save.save_library_clip(p4)
snap = Path(two.path).parent / "Edit Backups" / "clip two.src1.mp4"
check(snap.exists() and sha(snap) == trimmed_hash, "the trimmed state was snapshotted as the project's source")
check(sha(two.backup_path) == two_backup_hash, "the original backup is untouched (Undo still returns the 8s original)")
check(abs(media.probe(str(two.path))["duration"] - 3.0) < 0.1, "2x speed render is 3s")
library.apply_trim(two.id, 0.5, 2.5)                      # quick trim after an advanced edit
check(not store.project_path_for_video(two.id).exists() and not snap.exists(),
      "a quick trim discards the project and its snapshot")
library.clear_edit_backup(two.id)

# ---- Import button: edited copy next to the original --------------------------------
ext_dir = tempfile.mkdtemp(prefix="nle_import_")
ext = os.path.join(ext_dir, "outside.mp4")
make_clip(ext, 4)
ext_hash = sha(ext)
pi = save.open_for_import(ext)
ops.set_fades(pi, [next(pi.all_segments()).id], fade_in=1.0)
out = save.save_import(pi, ext)
check(str(out) == os.path.join(ext_dir, "outside-edited.mp4") and out.exists(), "import saves <name>-edited.mp4 beside it")
check(sha(ext) == ext_hash, "the imported original is never modified")
check(store.load_project(store.project_path_for_import(ext)) is not None, "import project saved for reopening")

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
