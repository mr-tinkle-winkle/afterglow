"""
Input overlay in the Advanced Editor engine (plan steps 6, 7, 7b): the
model (pieces attached to a clip's video part), rendering (preview draws
them, the export never does), Detach Input Overlay, and the overlay sidecar
an export writes. Uses tests/fake_puppetry_overlay.py for the captured
sidecar (time-coded pieces) and real ffmpeg/PyAV, in an isolated HOME.

    QT_QPA_PLATFORM=offscreen python3 tests/test_overlay_nle.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="overlay_nle_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow import config, db, editor, library, overlay_support as osup
from afterglow import input_overlay as aio
from afterglow.nle import media, model, ops, render, save, store, overlay_export
from afterglow.nle.history import History

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def ff(*a):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def dur(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]))


db.init_db()
config.load()
clips_dir = config.load().clips_path()
W, H = 320, 180


def make_library_clip(name, seconds=8, placements=None, pieces=("keyboard", "mouse")):
    path = clips_dir / f"{name}.mp4"
    ff("-f", "lavfi", "-i", f"color=c=0x1020a0:s={W}x{H}:r=30:d={seconds}", "-f", "lavfi", "-i", f"sine=f=330:d={seconds}",
       "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path)
    ts = time.time()
    pl = osup.resolve_placements(placements or {"keyboard": {"x": 0.1, "y": 0.2, "w": 0.5, "rotation": 0},
                                                "mouse": {"x": 0.8, "y": 0.5, "w": 0.1, "rotation": 0}})
    job = aio.start_clip(ts, aio.OverlaySettings(pieces=list(pieces), fps=30, placements=pl))
    aio.finish_clip(job, path, clip_end=ts)
    job.cleanup()
    osup.save_placements(osup.load_sidecar(path), pl)
    return library.add_video(path, title=name)


def piece_px(img, x, y):
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue(), c.alpha()


def red_mod(a, b):
    return (a - b) % 256


PER_S = 255 / 16.0

# ---------------------------------------------------------------- attach
v = make_library_clip("alpha")
project = save.open_for_library_video(v)
seg = next(s for s in project.all_segments())
part = seg.parts[0]
check([o.name for o in part.overlays] == ["keyboard", "mouse"], "opening a clip attaches its sidecar pieces to the video part")
kb = part.overlays[0]
check(abs(kb.x - 0.1) < 1e-9 and abs(kb.w - 0.5) < 1e-9 and kb.width == 540 and kb.height == 160 and kb.visible,
      "attached piece carries the manifest placement and the piece's pixel size")
d = model.Project.from_dict(json.loads(json.dumps(project.to_dict())))
check(d.all_segments().__next__().parts[0].overlays[0].name == "keyboard" and d.schema_version == model.SCHEMA_VERSION == 3,
      "projects with overlays round-trip through JSON (schema 3)")
old = project.to_dict()
old["schema_version"] = 2
for t in old["tracks"]:
    for s_ in t["segments"]:
        for p_ in s_["parts"]:
            p_.pop("overlays", None)
        s_.pop("overlay_piece", None)
check(model.Project.from_dict(old).all_segments().__next__().parts[0].overlays == [], "a schema-2 project loads (no overlays)")
no_sidecar = clips_dir / "plain.mp4"
ff("-f", "lavfi", "-i", f"color=c=green:s={W}x{H}:r=30:d=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", no_sidecar)
check(not any(p.overlays for s_ in save.new_project_for_file(str(no_sidecar)).all_segments() for p in s_.parts),
      "a clip without a sidecar has no overlays")

# ---------------------------------------------------------------- rendering
r_on = render.Renderer(project)
img = r_on.frame(1.0)
# keyboard at x 10%..60%, y 20%..: width 160 px -> 160x47; mouse at x=256, y=90
check(piece_px(img, 60, 50)[0] > 30 and piece_px(img, 300, 175)[0] < 30, "preview draws the attached piece at its placement, the rest stays video")
check(piece_px(img, 270, 100)[0] > 30, "second piece (mouse) is drawn at its own placement")
r_off = render.Renderer(project, overlays=False)
img_off = r_off.frame(1.0)
check(piece_px(img_off, 60, 50)[0] < 30 and piece_px(img_off, 270, 100)[0] < 30, "overlays=False renders the video clean (as if hidden)")
check(r_off.passthrough_frame(1.0, W, H) is not None and r_on.passthrough_frame(1.0, W, H) is None,
      "passthrough fast path stays valid for the clean export render, and is off while pieces are drawn")
ops.set_overlay(project, [seg.id], "keyboard", visible=False)
check(piece_px(render.Renderer(project).frame(1.0), 60, 50)[0] < 30, "a hidden piece isn't drawn")
ops.set_overlay(project, [seg.id], "keyboard", visible=True)

# the piece follows the clip's own timeline: piece time == source time
def kb_red(rend, t, name="keyboard", x=60):
    return piece_px(rend.frame_piece(t, name), x, 50)[0]


base = kb_red(r_on, 0.5)
check(red_mod(kb_red(r_on, 2.5), base) in range(int(2 * PER_S) - 8, int(2 * PER_S) + 9), "piece frames advance with the clip's time")

# ---------------------------------------------------------------- ops follow the clip
h = History(project)
src_t_at = lambda s, t: s.parts[0].source_time(t - s.start)
h.perform("split", lambda p: ops.split_at(p, 3.0, [seg.id]))
segs = sorted(project.all_segments(), key=lambda s: s.start)
check(len(segs) == 2 and all(len(s.parts[0].overlays) == 2 for s in segs), "split: both halves keep their pieces")
check(segs[0].parts[0].overlays[0] is not segs[1].parts[0].overlays[0], "...as independent copies")
rr = render.Renderer(project)
check(red_mod(kb_red(rr, 4.0), base) in range(int(3.5 * PER_S) - 8, int(3.5 * PER_S) + 9), "after the split, the right half's pieces still show their own source moment")
ops.set_overlay(project, [segs[1].id], "keyboard", x=0.6)
check(abs(segs[0].parts[0].overlays[0].x - 0.1) < 1e-9 and abs(segs[1].parts[0].overlays[0].x - 0.6) < 1e-9,
      "after a split each half's pieces are placed independently")
h.perform("trim", lambda p: ops.trim_start(p, segs[1].id, 4.0))
rr = render.Renderer(project)
tparts = segs[1].parts[0]
exp = (tparts.source_time(4.5 - segs[1].start) - 0.5) * PER_S
got = red_mod(kb_red(rr, 4.5, x=210), base)   # this half's keyboard was moved to x=60%
check(abs(tparts.src_in - 4.0) < 1e-6 and min(abs(got - exp % 256), 256 - abs(got - exp % 256)) <= 8,
      "trim: the pieces follow the video part's new in-point (they share its source timeline)")
h.perform("speed", lambda p: ops.set_speed(p, segs[0].id, 2.0))
rr = render.Renderer(project)
check(red_mod(kb_red(rr, 1.0), base) in range(int(1.5 * PER_S) - 10, int(1.5 * PER_S) + 11), "speed x2: the pieces play at the clip's speed")
h.perform("delete", lambda p: ops.delete(p, [segs[1].id]))
check(len(list(project.all_segments())) == 1, "delete removes the clip and its pieces with it")
h.undo(); h.undo(); h.undo(); h.undo()
check(len(list(project.all_segments())) == 1 and len(next(iter(project.all_segments())).parts[0].overlays) == 2,
      "undo restores the original single clip with both pieces")
seg = next(iter(project.all_segments()))
part = seg.parts[0]

# ---------------------------------------------------------------- detach
ops.set_transform(project, [seg.id], x=0.1, y=-0.05, scale=0.8, rotation=10.0)
ops.set_overlay(project, [seg.id], "keyboard", rotation=25.0)
before = {n: render.Renderer(project).frame_piece(2.0, n) for n in ("keyboard", "mouse")}
created = ops.detach_overlay(project, seg.id)
check(len(created) == 2 and {c.overlay_piece for c in created} == {"keyboard", "mouse"} and not part.overlays,
      "Detach Input Overlay turns each piece into its own overlay element and clears the attached list")
tr_idx = lambda s: project.track_index(project.find_segment(s.id)[0].id)
check(all(tr_idx(c) < tr_idx(seg) for c in created) and tr_idx(created[0]) != tr_idx(created[1]),
      "...on tracks above the video (one each, nearest free)")
check(all(abs(c.start - seg.start) < 1e-9 and abs(c.duration - seg.duration) < 1e-6 for c in created), "...with the same timing")
check(project.tracks[0].is_empty(), "the top buffer track is kept empty")
after = {n: render.Renderer(project).frame_piece(2.0, n) for n in ("keyboard", "mouse")}


def mean_diff(a, b):
    import numpy as np
    A = np.frombuffer(a.convertToFormat(QImage.Format_RGBA8888).constBits(), np.uint8).reshape(a.height(), -1, 4).astype(int)
    B = np.frombuffer(b.convertToFormat(QImage.Format_RGBA8888).constBits(), np.uint8).reshape(b.height(), -1, 4).astype(int)
    return np.abs(A[..., 3] - B[..., 3]).mean(), (A[..., 3] > 20).sum(), (B[..., 3] > 20).sum()


for n in ("keyboard", "mouse"):
    dm, ca, cb = mean_diff(before[n], after[n])
    check(ca > 100 and abs(ca - cb) / ca < 0.06 and dm < 4, f"detached {n} lands where the attached one was (alpha mean diff {dm:.2f}, area {ca} vs {cb}) incl. video transform + rotation")
check(created[0].overlay_piece in ("keyboard", "mouse"), "detached segments keep their overlay marker")
h2 = History(project)
copy_ids = h2.perform("dup", lambda p: ops.duplicate(p, [created[0].id]))
check(project.find_segment(copy_ids[0])[1].overlay_piece == created[0].overlay_piece, "a duplicated overlay element stays an overlay element")
h2.perform("split", lambda p: ops.split_at(p, 4.0, [created[1].id]))
check(all(s.overlay_piece == created[1].overlay_piece for s in project.all_segments() if s.name == created[1].name),
      "splitting an overlay element keeps both halves overlay")
try:
    ops.combine(project, [created[0].id, seg.id])
    check(False, "combining an overlay element with a clip is refused")
except ops.OpError:
    check(True, "combining an overlay element with a clip is refused")
h2.undo(); h2.undo()
project_detached = project
rend_clean = render.Renderer(project, overlays=False)
check(piece_px(rend_clean.frame(2.0), 60, 50)[0] < 30, "detached overlay elements are left out of the clean render too")
def n_diff(a, b):
    import numpy as np
    A = np.frombuffer(a.convertToFormat(QImage.Format_RGB32).constBits(), np.uint8).reshape(a.height(), -1, 4).astype(int)
    B = np.frombuffer(b.convertToFormat(QImage.Format_RGB32).constBits(), np.uint8).reshape(b.height(), -1, 4).astype(int)
    return int((np.abs(A - B).sum(axis=2) > 40).sum())


check(n_diff(render.Renderer(project).frame(2.0), rend_clean.frame(2.0)) > 3000, "...and drawn in the preview (the frame differs from the clean render)")
check(abs(project.video_duration - 8.0) < 0.05, "overlay elements don't lengthen the video")

# ---------------------------------------------------------------- export: static layout (real placements)
v2 = make_library_clip("beta")
p2 = save.open_for_library_video(v2)
s2 = next(iter(p2.all_segments()))
ops.trim_end(p2, s2.id, 6.0)
src_before = Path(v2.path)
orig_pieces = {n: (osup.sidecar_dir(v2.path) / f"{n}.mov") for n in ("keyboard", "mouse")}
orig_dur = dur(orig_pieces["keyboard"])
res = save.save_library_clip(p2)
sc = osup.load_sidecar(v2.path)
check(sc is not None and sc.get("derived") is True and "inputs" not in sc, "Replace writes an overlay sidecar for the output (derived, no inputs)")
check(abs(dur(v2.path) - 6.0) < 0.1 and abs(dur(Path(sc["dir"]) / "keyboard.mov") - dur(v2.path)) < 0.1,
      f"the output's pieces match the output video's length ({dur(Path(sc['dir']) / 'keyboard.mov'):.2f}s vs {dur(v2.path):.2f}s)")
pl = osup.placements_of(sc)
check(abs(pl["keyboard"]["x"] - 0.1) < 1e-9 and abs(pl["keyboard"]["w"] - 0.5) < 1e-9 and sc["pieces"]["keyboard"]["width"] == 540,
      "static layout: the manifest keeps the piece's REAL placement and native size (not a canvas-sized layer)")
# clean video
frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", v2.path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
px = lambda x, y: tuple(frame[(y * W + x) * 3:(y * W + x) * 3 + 3])
check(px(60, 50)[0] < 40 and px(270, 100)[0] < 40, f"the exported VIDEO is clean -- nothing burned in {px(60, 50)}")
# piece time still lines up with the output: source time 0.. same as original piece
def raw_red(path, t, x=60, y=20):
    out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True).stdout
    w = int(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width", "-of", "csv=p=0", str(path)]))
    return out[(y * w + x) * 4]


bak = Path(library.get_video(v2.id).backup_path)
bak_side = osup.backup_sidecar_dir(bak)
check(bak_side.is_dir() and (bak_side / "inputs.jsonl").exists() and json.loads((bak_side / "manifest.json").read_text()).get("derived") is None,
      "the original sidecar (with inputs) was kept beside the Edit Backup")
check(red_mod(raw_red(Path(sc["dir"]) / "keyboard.mov", 3.0), raw_red(Path(sc["dir"]) / "keyboard.mov", 1.0)) in range(int(2 * PER_S) - 8, int(2 * PER_S) + 9)
      and red_mod(raw_red(Path(sc["dir"]) / "keyboard.mov", 0.0), raw_red(bak_side / "keyboard.mov", 0.0)) <= 8,
      "the output's pieces show the same moments as the original (starts at the clip's first frame)")
stable = [o.source for s in p2.all_segments() for pt in s.parts for o in pt.overlays]
check(all(str(bak_side) in x for x in stable), "the project's piece sources were re-pointed at the backed-up sidecar")
# reopen: derived pieces attach again; project is reused
p2b = save.open_for_library_video(library.get_video(v2.id))
check(all(str(bak_side) in o.source for s in p2b.all_segments() for pt in s.parts for o in pt.overlays), "reopening the saved project keeps the original per-clip pieces")
# undo edits restores the original sidecar
library.undo_edit(v2.id)
check(osup.load_sidecar(v2.path).get("derived") is None and abs(dur(osup.sidecar_dir(v2.path) / "keyboard.mov") - orig_dur) < 0.05
      and not bak_side.exists(), "Undo Edits restores the original pieces with the original clip")

# ---------------------------------------------------------------- export: canvas layout (detached + keyframed)
v3 = make_library_clip("gamma")
p3 = save.open_for_library_video(v3)
s3 = next(iter(p3.all_segments()))
els = ops.detach_overlay(p3, s3.id)
kbe = next(e for e in els if e.overlay_piece == "keyboard")
ops.set_keyframe(p3, kbe.id, "x", 0.0, -0.3)
ops.set_keyframe(p3, kbe.id, "x", 4.0, 0.3)
mouse_el = next(e for e in els if e.overlay_piece == "mouse")
ops.delete(p3, [mouse_el.id])           # only the keyboard remains
names = overlay_export.overlay_names(p3)
check(names == ["keyboard"], "overlay_names lists only pieces with visible content")
out = save.save_library_separately(p3)
sc3 = osup.load_sidecar(out.path)
check(sc3 is not None and set(sc3["pieces"]) == {"keyboard"} and sc3["derived"], "Save Separately gives the NEW clip its own sidecar")
check(sc3["pieces"]["keyboard"]["width"] == W and sc3["pieces"]["keyboard"]["height"] == H
      and sc3["pieces"]["keyboard"]["placement"]["w"] == 1.0, "animated piece: canvas-sized layer, full-frame placement")
kp = Path(sc3["dir"]) / "keyboard.mov"
check(abs(dur(kp) - dur(out.path)) < 0.1, "...as long as the new clip")
check(osup.load_sidecar(v3.path) is not None and not osup.load_sidecar(v3.path).get("derived"), "the original clip keeps its own sidecar untouched")
frame3 = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", out.path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
check(all(max(frame3[i:i + 3]) < 190 and frame3[i] < 60 for i in range(0, len(frame3), 3 * 97)), "the new clip's video is clean (no overlay burned in)")
# the keyframed motion is in the layer: the keyboard's centre column moves right over time
import numpy as np
def alpha_cols(path, t):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(H, W, 4)[..., 3]
    xs = np.nonzero(a.max(axis=0) > 20)[0]
    return (xs.min() + xs.max()) / 2 if len(xs) else None
c0, c1 = alpha_cols(kp, 0.2), alpha_cols(kp, 3.8)
check(c0 is not None and c1 is not None and c1 - c0 > 80, f"the layer carries the keyframed motion (centre {c0:.0f} -> {c1:.0f})")
# reopening the new clip attaches the derived piece; no overlay content -> no stale sidecar
p3b = save.open_for_library_video(out)
o3 = next(iter(p3b.all_segments())).parts[0].overlays
check(len(o3) == 1 and o3[0].w == 1.0, "reopening an exported clip in the Editor attaches its overlay pieces")
p_none = save.new_project_for_file(str(no_sidecar))
res = overlay_export.write_sidecar(p_none, no_sidecar)
check(res is None and not osup.sidecar_dir(no_sidecar).exists(), "no overlay content -> no sidecar")

# hidden pieces are not exported
v4 = make_library_clip("delta")
p4 = save.open_for_library_video(v4)
s4 = next(iter(p4.all_segments()))
ops.set_overlay(p4, [s4.id], "mouse", visible=False)
check(overlay_export.overlay_names(p4) == ["keyboard"], "a hidden piece is treated as hidden: not exported")
save.save_library_clip(p4)
check(set(osup.load_sidecar(v4.path)["pieces"]) == {"keyboard"}, "...the exported sidecar only holds the visible piece")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
