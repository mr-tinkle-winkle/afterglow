"""
Input overlay (Puppetry integration), steps 1-4 of the plan: the vendored
module, afterglow's rotation layer, the DB/config fields, the capture hook
and sidecar lifecycle. Runs against tests/fake_puppetry_overlay.py (a
stand-in for the real `puppetry-overlay`, which isn't in the sandbox) and
real ffmpeg, in an isolated HOME.

    QT_QPA_PLATFORM=offscreen python3 tests/test_input_overlay.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="overlay_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from afterglow import clips, config, db, keyframes, library, overlay_support as osup  # noqa: E402
from afterglow import input_overlay as aio  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def ff(*args):
    return subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], capture_output=True, text=True)


def make_clip(path, seconds, gop=30):
    ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={seconds}", "-f", "lavfi", "-i",
       f"sine=f=440:d={seconds}", "-c:v", "libx264", "-g", gop, "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path)


def dur(path):
    return float(json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                                            str(path)], capture_output=True, text=True).stdout)["format"]["duration"])


def frames(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                        "stream=nb_read_packets", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return int(r.stdout.strip())


def pixel(path, t, x=2, y=2, extra=()):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", *extra, "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True).stdout
    w = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width",
                            "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip())
    i = (y * w + x) * 4
    return tuple(raw[i:i + 4])


def red_diff(path, a, b):
    """Red-channel change between piece times a and b, mod 256."""
    return (pixel(path, b)[0] - pixel(path, a)[0]) % 256


EXPECT_PER_S = 255 / 16.0
db.init_db()
config.load()   # creates config.toml and dirs
work = Path(tempfile.mkdtemp(prefix="overlay_work_"))

# ---------------------------------------------------------------- availability
ok, why = osup.available()
check(ok and "recording" in why, f"available() true with the fake helper ({why})")
os.environ["FAKE_PUPPETRY_OFF"] = "1"
ok, why = osup.available()
check(not ok and "Layered Replay Buffer" in why, "available() false with a reason when the buffer is off")
del os.environ["FAKE_PUPPETRY_OFF"]

# ---------------------------------------------------------------- placements + rotation
pl = osup.resolve_placements({"keyboard": {"x": 0.1, "rotation": 15}}, {"keyboard": {"w": 0.5}, "mouse": {"rotation": -10}})
check(pl["keyboard"]["x"] == 0.1 and pl["keyboard"]["w"] == 0.5 and pl["keyboard"]["rotation"] == 15,
      "resolve_placements merges clip type > global > built-in, with rotation")
check(pl["mouse"]["rotation"] == -10 and pl["controller"]["rotation"] == 0, "rotation defaults to 0 per piece")

# ---------------------------------------------------------------- capture + sidecar (module level)
clip = work / "clip.mp4"
make_clip(clip, 10)
t_save = time.time()
job = aio.start_clip(t_save, aio.OverlaySettings(pieces=["keyboard", "mouse"], placements=pl, fps=30))
manifest = aio.finish_clip(job, clip, clip_end=t_save)
job.cleanup()
sc = osup.load_sidecar(clip)
check(sc is not None and set(sc["pieces"]) == {"keyboard", "mouse"}, "finish_clip stores a sidecar with both pieces")
kb = Path(sc["dir"]) / "keyboard.mov"
check(abs(dur(kb) - dur(clip)) < 0.1, f"piece aligned to the clip's length ({dur(kb):.2f}s vs {dur(clip):.2f}s)")
check(abs(red_diff(kb, 1, 5) - EXPECT_PER_S * 4) < 8, "piece frames are time-coded as expected (baseline for the cut tests)")

osup.save_placements(sc, pl)
sc2 = osup.load_sidecar(clip)
check(osup.placements_of(sc2)["keyboard"]["rotation"] == 15 and osup.placements_of(sc2)["keyboard"]["x"] == 0.1,
      "rotation round-trips through the manifest")
check(aio.placements_of(sc2)["keyboard"]["x"] == 0.1, "Puppetry's own reader still understands the manifest")

# ---------------------------------------------------------------- preview graph (through real ffmpeg)
def run_graph(sc, enabled, shot):
    files, graph = osup.preview_graph(sc, 320, 180, enabled)
    g = graph
    for n in range(len(files) + 1, 0, -1):          # mpv's [vidN] -> ffmpeg's [N-1:v]
        g = g.replace(f"[vid{n}]", f"[{n - 1}:v]")
    ins = ["-i", str(clip)]
    for f in files:
        ins += ["-i", f]
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", g, "-map", "[vo]", "-frames:v", "1", shot],
                       capture_output=True, text=True)
    return r, graph


r, graph = run_graph(sc2, True, work / "shot.png")
check(r.returncode == 0 and "rotate=" in graph and "overlay=" in graph, "preview graph with rotation runs in ffmpeg")
r2, graph2 = run_graph(sc2, False, work / "shot_off.png")
check(r2.returncode == 0 and "overlay" not in graph2, "overlay off = plain passthrough graph")
files, g_norot = osup.preview_graph({**sc2, "pieces": {k: {**v, "placement": {**v["placement"], "rotation": 0}}
                                                      for k, v in sc2["pieces"].items()}}, 320, 180, True)
check("rotate=" not in g_norot, "no rotate step when rotation is 0")

# ---------------------------------------------------------------- DB / config
cfg = clips.create_clip_config("Ace", 30)
check(cfg.overlay_enabled is False and cfg.overlay_pieces == ["keyboard", "mouse"] and cfg.overlay_placements == {},
      "new clip configs default to overlay off with keyboard+mouse")
cfg = clips.update_clip_config(cfg.id, overlay_enabled=True, overlay_pieces=["keyboard", "controller"],
                               overlay_offset_ms=-40, overlay_visible_default=False,
                               overlay_placements={"keyboard": {"x": 0.3}})
check(cfg.overlay_enabled and cfg.overlay_pieces == ["keyboard", "controller"] and cfg.overlay_offset_ms == -40
      and not cfg.overlay_visible_default and cfg.overlay_placements == {"keyboard": {"x": 0.3}},
      "overlay settings persist on the clip config")
s = config.load()
s.overlay_placements = {"mouse": {"x": 0.5, "w": 0.2, "rotation": 5}}
config.save(s)
check(config.load().overlay_placements["mouse"]["rotation"] == 5, "global overlay placements persist in config.toml")
check(("input_overlay", "Input Overlay") in keyframes.PIPELINE_KEYFRAMES, "input overlay is an Advanced Sound keyframe")

# DB migration of a pre-overlay database
import sqlite3
old = work / "old.db"
c = sqlite3.connect(old)
c.execute("CREATE TABLE clip_configs (id INTEGER PRIMARY KEY, name TEXT, length_seconds INTEGER, sound_path TEXT, hotkey TEXT, sort_order INTEGER)")
c.execute("CREATE TABLE videos (id INTEGER PRIMARY KEY, favorite INTEGER)")
c.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, icon_path TEXT, category_id INTEGER, outline_color TEXT)")
c.execute("INSERT INTO clip_configs VALUES (1,'x',10,NULL,NULL,0)")
c.row_factory = sqlite3.Row
db._migrate_columns(c)
row = c.execute("SELECT * FROM clip_configs").fetchone()
check(row["overlay_enabled"] == 0 and row["overlay_pieces"] == '["keyboard","mouse"]', "migration adds overlay columns to an old DB")
c.close()

# ---------------------------------------------------------------- library lifecycle
clips_dir = config.load().clips_path()


def add_clip_with_overlay(name, seconds=10):
    path = clips_dir / f"{name}.mp4"
    make_clip(path, seconds)
    ts = time.time()
    j = aio.start_clip(ts, aio.OverlaySettings(pieces=["keyboard"], placements=pl, fps=30))
    aio.finish_clip(j, path, clip_end=ts)
    j.cleanup()
    return library.add_video(path, title=name)


v = add_clip_with_overlay("alpha")
side = osup.sidecar_dir(v.path)
check(side.is_dir(), "fixture clip has a sidecar")

# rename carries the sidecar
v2 = library.rename_video(v.id, title="Beta Clip")
check(not side.exists() and osup.sidecar_dir(v2.path).is_dir() and Path(v2.path).exists(), "rename moves the sidecar with the clip")
m = json.loads((osup.sidecar_dir(v2.path) / "manifest.json").read_text())
check(m["clip"] == Path(v2.path).name, "manifest's clip name follows the rename")

# fast trim
before_kb = osup.sidecar_dir(v2.path) / "keyboard.mov"
red_before = red_diff(before_kb, 0, 5)
t0 = pixel(before_kb, 4.0)[0]
v3 = library.apply_trim(v2.id, 4.0, 8.0, frame_perfect=True)
side3 = osup.sidecar_dir(v3.path)
kb3 = side3 / "keyboard.mov"
check(abs(dur(kb3) - dur(v3.path)) < 0.1, f"frame-perfect trim: piece length follows ({dur(kb3):.2f}s vs {dur(v3.path):.2f}s)")
check(abs(((pixel(kb3, 0.0)[0] - t0) % 256)) <= 6, "frame-perfect trim: piece starts at the same moment as the video")
bak = Path(v3.backup_path)
check(osup.backup_sidecar_dir(bak).is_dir(), "first edit backs the sidecar up beside the Edit Backup")
check(frames(kb3) == frames(kb3), "trimmed piece decodes")

# second trim keeps the ORIGINAL sidecar backup
v4 = library.apply_trim(v3.id, 1.0, 3.0, frame_perfect=True)
check(osup.backup_sidecar_dir(Path(v4.backup_path)).is_dir() and abs(dur(osup.sidecar_dir(v4.path) / "keyboard.mov") - dur(v4.path)) < 0.1,
      "second trim: sidecar follows again and the original backup is kept")
orig_len = dur(osup.backup_sidecar_dir(Path(v4.backup_path)) / "keyboard.mov")
check(abs(orig_len - 10) < 0.2, "the backed-up sidecar is the untouched original")

# undo restores
v5 = library.undo_edit(v4.id)
check(abs(dur(osup.sidecar_dir(v5.path) / "keyboard.mov") - 10) < 0.2 and not osup.backup_sidecar_dir(bak).exists(),
      "Undo Edits restores the original pieces with the original clip")

# fast (keyframe) trim: the video snaps back to the keyframe at/before 2.5 s (= 2.0 s here, GOP 30 @ 30 fps),
# and the pieces must start at that SAME moment
orig_clip_copy = work / "orig_for_fast.mp4"
shutil.copyfile(Path(v5.path), orig_clip_copy)
orig_piece_copy = work / "orig_piece_for_fast.mov"
shutil.copyfile(osup.sidecar_dir(v5.path) / "keyboard.mov", orig_piece_copy)
v6 = library.apply_trim(v5.id, 2.5, 7.0, frame_perfect=False)
kb6 = osup.sidecar_dir(v6.path) / "keyboard.mov"
check(abs(dur(kb6) - dur(v6.path)) < 0.1, f"fast trim: piece length follows the (keyframe-snapped) video ({dur(v6.path):.2f}s)")
vid_first = subprocess.run(["ffmpeg", "-v", "error", "-i", v6.path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True).stdout
vid_ref = subprocess.run(["ffmpeg", "-v", "error", "-ss", "2.0", "-i", str(orig_clip_copy), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
diff = sum(abs(a - b) for a, b in zip(vid_first, vid_ref)) / max(1, len(vid_ref))
check(diff < 2.0, f"fast trim: the video really starts at the 2.0 s keyframe (mean pixel diff {diff:.2f})")
check(abs((pixel(kb6, 0.0)[0] - pixel(orig_piece_copy, 2.0)[0]) % 256) <= 6,
      "fast trim: the pieces start at that same 2.0 s moment")
# clear backup removes the sidecar backup
bak6 = Path(v6.backup_path)
v7 = library.clear_edit_backup(v6.id)
check(not osup.backup_sidecar_dir(bak6).exists() and osup.sidecar_dir(v7.path).is_dir(),
      "Clear Edit Backup removes the sidecar backup, keeps the live sidecar")

# delete
path7 = Path(v7.path)
library.delete_video(v7.id)
check(not path7.exists() and not osup.sidecar_dir(path7).exists(), "delete removes the clip's sidecar")

# a clip with no sidecar is untouched by all of it
plain = clips_dir / "plain.mp4"
make_clip(plain, 6)
pv = library.add_video(plain, title="plain")
pv = library.apply_trim(pv.id, 1.0, 4.0, frame_perfect=True)
pv = library.rename_video(pv.id, title="plain renamed")
check(osup.load_sidecar(pv.path) is None and not osup.sidecar_dir(pv.path).exists(), "clips without a sidecar never gain one")
library.undo_edit(pv.id)

# ---------------------------------------------------------------- capture pipeline (fake OBS)
class FakeOBS:
    raw_dir = work / "obs"

    def __init__(self, settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_fps(self):
        return 30.0

    def save_replay_buffer(self, on_sent=None):
        FakeOBS.raw_dir.mkdir(exist_ok=True)
        if on_sent:
            on_sent(time.time())
        raw = FakeOBS.raw_dir / f"replay_{time.time_ns()}.mp4"
        make_clip(raw, 12)
        return raw


clips.OBSClient = FakeOBS
clips.POST_SAVE_SETTLE_SECONDS = 0
played = []
clips.play_sound = lambda p: played.append(p)
s = config.load()
s.error_sounds = {keyframes.INPUT_OVERLAY: "/tmp/overlay_error.wav"}
s.advanced_sounds = {keyframes.INPUT_OVERLAY: "/tmp/overlay_ok.wav"}
config.save(s)

cfg_on = clips.get_clip_config(cfg.id)
cfg_on = clips.update_clip_config(cfg_on.id, overlay_pieces=["keyboard", "mouse"], overlay_offset_ms=0,
                                  overlay_visible_default=True, overlay_placements={}, length_seconds=8)
calls_log = work / "calls.jsonl"
os.environ["FAKE_PUPPETRY_LOG"] = str(calls_log)
video = clips.trigger_clip(cfg_on.id)
clips.wait_for_overlays(120)
del os.environ["FAKE_PUPPETRY_LOG"]
renders = [json.loads(l) for l in calls_log.read_text().splitlines() if l.startswith('["render"')]
spans = [float(r[r.index("--end") + 1]) - float(r[r.index("--start") + 1]) for r in renders]
check(len(renders) == 2 and all(abs(sp - dur(video.path)) < 0.2 for sp in spans),
      f"each piece is rendered over the CLIP's span only, not the whole replay buffer ({spans})")
check(not any(l.startswith('["align"') for l in calls_log.read_text().splitlines()), "no align step")
sc = osup.load_sidecar(video.path)
check(sc is not None and set(sc["pieces"]) == {"keyboard", "mouse"}, "trigger_clip stores an overlay sidecar next to the clip")
check(Path(video.path).exists() and abs(dur(video.path) - 8) < 0.5 and abs(dur(Path(sc["dir"]) / "mouse.mov") - dur(video.path)) < 0.15,
      f"captured pieces match the trimmed clip length ({dur(video.path):.2f}s, from a 12 s raw file)")
check(osup.placements_of(sc)["mouse"]["rotation"] == 5 and osup.placements_of(sc)["mouse"]["x"] == 0.5,
      "new clips start at the resolved placements (global default, with rotation)")
check("/tmp/overlay_ok.wav" in played and "/tmp/overlay_error.wav" not in played, "success sound for the input-overlay keyframe played")
check(not (clips_dir / (Path(video.path).name + ".input.tmp")).exists(), "no temp leftovers")

# the clip reaches the library BEFORE its overlay is rendered (rendering is slow with the
# real Puppetry); and a rename while the pieces render carries the finished sidecar along
os.environ["FAKE_PUPPETRY_DELAY"] = "2"
t0 = time.time()
video_r = clips.trigger_clip(cfg_on.id)
returned_after = time.time() - t0
check(osup.load_sidecar(video_r.path) is None, "trigger_clip returns before the overlay is rendered")
renamed = library.rename_video(video_r.id, title="renamed while rendering")
clips.wait_for_overlays(120)
del os.environ["FAKE_PUPPETRY_DELAY"]
check(osup.load_sidecar(renamed.path) is not None and not osup.sidecar_dir(video_r.path).exists(),
      "renaming the clip while its overlay renders: the sidecar follows it")

# overlay off: no sidecar, no noise
played.clear()
cfg_off = clips.update_clip_config(cfg.id, overlay_enabled=False)
video2 = clips.trigger_clip(cfg_off.id)
clips.wait_for_overlays(120)
check(osup.load_sidecar(video2.path) is None and "/tmp/overlay_error.wav" not in played, "overlay off: no sidecar and no error noise")

# overlay on, tool unavailable: clip kept, error noise
played.clear()
cfg_on = clips.update_clip_config(cfg.id, overlay_enabled=True)
saved_env = os.environ["PUPPETRY_OVERLAY"]
os.environ["PUPPETRY_OVERLAY"] = "/nonexistent/puppetry-overlay"
os.environ["PATH_BACKUP"] = os.environ["PATH"]
try:
    aio.shutil_which = None
    orig_find = aio.find_tool
    aio.find_tool = lambda: None
    video3 = clips.trigger_clip(cfg_on.id)
    clips.wait_for_overlays(120)
finally:
    aio.find_tool = orig_find
    os.environ["PUPPETRY_OVERLAY"] = saved_env
check(Path(video3.path).exists() and osup.load_sidecar(video3.path) is None, "overlay on but Puppetry missing: the clip is still kept")
check("/tmp/overlay_error.wav" in played, "...and the input-overlay error noise plays")
check(not osup.sidecar_dir(video3.path).exists(), "...with no half-written sidecar left behind")

# one piece failing keeps the others
played.clear()
os.environ["FAKE_PUPPETRY_FAIL"] = "mouse"
video4 = clips.trigger_clip(cfg_on.id)
clips.wait_for_overlays(120)
sc4 = osup.load_sidecar(video4.path)
check(sc4 is not None and set(sc4["pieces"]) == {"keyboard"}, "one failed piece: the others are still stored")
del os.environ["FAKE_PUPPETRY_FAIL"]

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
