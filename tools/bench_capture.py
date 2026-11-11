"""
Capture-pipeline benchmark: `clips.trigger_clip` against a stand-in for OBS
(a pre-made "replay buffer" file handed back instantly), in a throwaway HOME,
with every stage timed.

    python3 tools/bench_capture.py                       # 1440p120, 2 s keyframes, 40 s buffer, 30 s clip
    python3 tools/bench_capture.py --keyint 1 --runs 5
    python3 tools/bench_capture.py --size 1920x1080 --fps 60 --buffer 60 --clip 30

Prints, per run: time from "OBS reported the file" to "clip is in the library"
(what afterglow itself costs), and where it went (every subprocess and the
named pipeline steps). The raw file is cached in --cache between runs.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--size", default="2560x1440")
ap.add_argument("--fps", type=int, default=120)
ap.add_argument("--keyint", type=float, default=2.0, help="keyframe interval of the fake OBS file, seconds")
ap.add_argument("--buffer", type=float, default=40.0, help="replay buffer length, seconds")
ap.add_argument("--clip", type=int, default=30, help="clip length, seconds")
ap.add_argument("--runs", type=int, default=3)
ap.add_argument("--cache", default="/tmp/afterglow_bench_cache")
ap.add_argument("--profile", default="")
args = ap.parse_args()

HOME = tempfile.mkdtemp(prefix="bench_capture_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

os.makedirs(args.cache, exist_ok=True)
RAW = Path(args.cache) / f"raw_{args.size}_{args.fps}_{args.keyint:g}_{args.buffer:g}.mp4"
if not RAW.exists():
    print(f"making {RAW.name} (once)...", flush=True)
    # +0.37 s so the trim start never lands exactly on a keyframe (the realistic case)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={args.size}:rate={args.fps}", "-f", "lavfi", "-i", "sine=f=440",
                    "-t", f"{args.buffer + 0.37:g}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-g", str(round(args.keyint * args.fps)), "-keyint_min", str(round(args.keyint * args.fps)),
                    "-sc_threshold", "0", "-bf", "0", "-c:a", "aac", str(RAW)], check=True)

from afterglow import clips, config, db, library  # noqa: E402

db.init_db()
OBS_DIR = Path(HOME) / "obs"
OBS_DIR.mkdir()
events = []          # (label, seconds)
T = {"reported": 0.0}


class FakeOBS:
    def __init__(self, settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_fps(self):
        return float(args.fps)

    def save_replay_buffer(self, on_sent=None):
        raw = OBS_DIR / f"replay_{time.time_ns()}.mp4"
        try:
            os.link(RAW, raw)                 # instant "save" (same disk)
        except OSError:
            shutil.copy(RAW, raw)
        if on_sent:
            on_sent(time.time())
        T["reported"] = time.perf_counter()
        return raw


clips.OBSClient = FakeOBS
clips.play_sound = lambda p: None
_orig_run = subprocess.run


def timed_run(cmd, *a, **k):
    t = time.perf_counter()
    try:
        return _orig_run(cmd, *a, **k)
    finally:
        if isinstance(cmd, (list, tuple)) and cmd:
            events.append((f"subprocess {Path(str(cmd[0])).name}", time.perf_counter() - t))


subprocess.run = timed_run


def wrap(module, name, label=None):
    fn = getattr(module, name)

    def inner(*a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            events.append((label or name, time.perf_counter() - t))
    setattr(module, name, inner)


for mod, name in ((clips, "_settle"), (clips, "probe_duration"), (clips, "commit_trim"), (clips, "add_video"),
                  (clips.autofilter, "compute_active_auto_tags")):
    if hasattr(mod, name):
        wrap(mod, name)

cfg = clips.create_clip_config(name="Bench", length_seconds=args.clip, hotkey="")
s = config.load()
config.save(s)


def one():
    events.clear()
    t0 = time.perf_counter()
    video = clips.trigger_clip(cfg.id, indicator_id=None)
    t1 = time.perf_counter()
    own = t1 - T["reported"]
    print(f"  hotkey -> library {t1 - t0:6.3f}s   OBS reported -> library {own:6.3f}s   "
          f"clip {video.duration_sec:.2f}s  {os.path.getsize(video.path) / 1e6:.0f} MB")
    agg = {}
    for label, sec in events:
        n, tot = agg.get(label, (0, 0.0))
        agg[label] = (n + 1, tot + sec)
    for label, (n, tot) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        print(f"      {label:34s} x{n}  {tot:6.3f}s")
    return own


print(f"{args.size} {args.fps} fps, keyframes every {args.keyint:g} s, {args.buffer:g} s buffer, {args.clip} s clip")
if args.profile:
    import cProfile
    cProfile.run("one()", args.profile)
else:
    times = [one() for _ in range(args.runs)]
    print(f"OBS reported -> library: best {min(times):.3f}s  median {sorted(times)[len(times) // 2]:.3f}s")
clips.wait_for_overlays(60)
time.sleep(0.3)
os._exit(0)
