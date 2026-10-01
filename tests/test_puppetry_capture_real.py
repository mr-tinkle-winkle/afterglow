"""
The clip-capture pipeline (clips.trigger_clip, with a stand-in OBS) against
the REAL `puppetry-overlay` (Puppetry installed, or PUPPETRY_OVERLAY pointing
at it) with a realistic 1200 s replay buffer. Regression test for "the input
overlay never shows up": the overlay used to render the whole replay length
(~15 min per piece with the real renderer) before the clip was even added.

    PUPPETRY_OVERLAY=/path/to/puppetry-overlay QT_QPA_PLATFORM=offscreen \
        python3 tests/test_puppetry_capture_real.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="pup_real_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="pup_real_run_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from afterglow import input_overlay as aio  # noqa: E402

if not aio.find_tool() or "fake" in (aio.find_tool() or ""):
    print("SKIP: real puppetry-overlay not found (install Puppetry or set PUPPETRY_OVERLAY)")
    sys.exit(0)

from afterglow import clips, config, db, keyframes, library, overlay_support as osup  # noqa: E402

FAILS = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)


def ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], check=True)


def dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", str(p)]))


def frame(p, t):
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(p), "-frames:v", "1",
                           "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True).stdout


db.init_db()
s = config.load()
CLIP_LEN = 15
RAW_LEN = 20

# Puppetry's runtime files, as its helper writes them (docs/overlay_interface.md)
run = Path(os.environ["XDG_RUNTIME_DIR"]) / "puppetry"
run.mkdir(parents=True)
t_press = {}


def write_runtime(t_save_guess):
    """Status (1200 s replay from OBS) + a buffer where W is held 5-8 s into the clip."""
    clip_start = t_save_guess - CLIP_LEN
    t_press["down"], t_press["up"] = clip_start + 5, clip_start + 8
    lines = [{"format": "puppetry-input-buffer", "version": 1, "clock": "unix", "start": t_save_guess - 1205,
              "length_s": 1200.0, "held_at_start": {}},
             {"t": t_press["down"], "e": "kd", "k": "KEY_W", "s": "r"},
             {"t": t_press["up"], "e": "ku", "k": "KEY_W", "s": "r"}]
    (run / "input_buffer.jsonl").write_text("".join(json.dumps(l) + "\n" for l in lines))
    (run / "overlay_status.json").write_text(json.dumps({
        "pid": 1, "updated": time.time() + 3600, "daemon_connected": True,
        "replay": {"enabled": True, "length_s": 1200.0, "length_source": "obs", "extra_s": 5.0}}))


class FakeOBS:
    raw_dir = Path(HOME) / "obs"

    def __init__(self, settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_fps(self):
        return 60.0

    def save_replay_buffer(self, on_sent=None):
        FakeOBS.raw_dir.mkdir(exist_ok=True)
        t = time.time()
        write_runtime(t)
        if on_sent:
            on_sent(t)
        raw = FakeOBS.raw_dir / f"replay_{time.time_ns()}.mp4"
        ff("-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=60:d={RAW_LEN}", "-c:v", "libx264",
           "-preset", "ultrafast", "-pix_fmt", "yuv420p", raw)
        return raw


clips.OBSClient = FakeOBS
clips.POST_SAVE_SETTLE_SECONDS = 0
played = []
clips.play_sound = lambda p: played.append(p)
s.error_sounds = {keyframes.INPUT_OVERLAY: "/tmp/overlay_error.wav"}
config.save(s)

write_runtime(time.time())
check(osup.available()[0], f"Puppetry reports the replay buffer as available ({osup.available()[1]})")
cfg = clips.create_clip_config(name="Real", length_seconds=CLIP_LEN, hotkey="ctrl+f9",
                               overlay_enabled=True, overlay_pieces=["keyboard", "mouse"])
t0 = time.time()
video = clips.trigger_clip(cfg.id)
t_clip = time.time() - t0
t1 = time.time()
clips.wait_for_overlays(600)
t_overlay = time.time() - t1
print(f"  clip in library after {t_clip:.1f}s; overlay {t_overlay:.1f}s later")

sc = osup.load_sidecar(video.path)
check(sc is not None and set(sc["pieces"]) == {"keyboard", "mouse"}, "the real Puppetry produced a sidecar with both pieces")
check("/tmp/overlay_error.wav" not in played, "no overlay error noise")
check(t_overlay < CLIP_LEN * 3, f"overlay rendered in about clip time, not replay-buffer time ({t_overlay:.1f}s for a {CLIP_LEN}s clip)")
if sc:
    kb = Path(sc["dir"]) / sc["pieces"]["keyboard"]["file"]
    check(abs(dur(kb) - dur(video.path)) < 0.1, f"keyboard piece is as long as the clip ({dur(kb):.2f}s vs {dur(video.path):.2f}s)")
    idle, idle2, held = frame(kb, 2.0), frame(kb, 4.5), frame(kb, 6.5)
    check(idle == idle2 and idle != held, "W shows pressed 5-8 s into the clip, not before (the overlay lines up)")
    from afterglow.gui import video_preview_dialog  # noqa: F401 -- the previewer's own lookup
    check(osup.has_sidecar(video.path) and osup.load_sidecar(video.path) is not None,
          "the previewer finds it (its overlay toggle appears)")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
