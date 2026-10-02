"""
Clip indicator ("the clapper") hooks: what trigger_clip, the input-overlay render and the daemon
send to the helper, and when.  A fake OBS and a test sink in place of the helper's socket, an
isolated HOME, real ffmpeg for the trim.

    QT_QPA_PLATFORM=offscreen python3 tests/test_indicator_pipeline.py
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="indicator_pipeline_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["AFTERGLOW_INDICATOR_SOCKET"] = str(Path(HOME) / "none.sock")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from afterglow import clips, config, daemon, db, indicator_client as ic, keyframes  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def ff(*args):
    return subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], capture_output=True, text=True)


def make_clip(path, seconds):
    ff("-f", "lavfi", "-i", f"testsrc2=s=160x90:r=30:d={seconds}", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
       "-c:v", "libx264", "-g", 30, "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path)


work = Path(tempfile.mkdtemp(prefix="indicator_pipeline_"))
db.init_db()
s = config.load()
s.clips_dir = str(work / "clips")
s.default_sound_path = "/sounds/global.wav"
config.save(s)

MSGS: list = []
ic.set_test_sink(MSGS.append)
played: list = []
clips.play_sound = lambda p: played.append(p)
clips.POST_SAVE_SETTLE_SECONDS = 0


class FakeOBS:
    fail = False

    def __init__(self, settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_fps(self):
        return 30.0

    def save_replay_buffer(self, on_sent=None):
        if on_sent:
            on_sent(time.time())
        if FakeOBS.fail:
            raise clips.ClipError("OBS isn't running")
        raw = work / f"replay_{time.time_ns()}.mp4"
        make_clip(raw, 6)
        return raw


clips.OBSClient = FakeOBS

# clip files are named by the second: give every capture its own second
import datetime as _dt  # noqa: E402
_tick = [0]


class _FakeDatetime(_dt.datetime):
    @classmethod
    def now(cls, tz=None):
        _tick[0] += 1
        return _dt.datetime(2026, 1, 1, 12, 0, 0) + _dt.timedelta(seconds=_tick[0])


clips.datetime = _FakeDatetime


def events(cid=None):
    ic.flush()
    return [(m["event"]) for m in MSGS if cid is None or m["id"] == cid]


def reset():
    ic.flush()
    MSGS.clear()
    played.clear()


def set_ind(**kw):
    st = config.load()
    for k, v in kw.items():
        setattr(st.clip_indicator, k, v)
    config.save(st)


# ------------------------------------------------------------------ plain capture: start -> clap -> done
cfg = clips.create_clip_config(name="Ace", length_seconds=3, hotkey="ctrl+f9")
cfg = clips.update_clip_config(cfg.id, overlay_enabled=False, indicator_colors={"body": "#ff0000"},
                               indicator_icon_path="/icons/a.png", indicator_clap_sound="/sounds/clap.wav")
reset()
video = clips.trigger_clip(cfg.id)
ev = events()
check(ev == ["start", "clap", "done"], f"a plain capture sends start, clap, done in that order ({ev})")
check(len({m["id"] for m in MSGS}) == 1, "...all for one capture id")
st = MSGS[0]["style"]
from afterglow.indicator import draw as _draw
check(st["colors"].get("body") == "#ff0000" and st["icon"] == "/icons/a.png", "the start carries the clip type's colours and icon")
check(all(st["colors"].get(k) == v for k, v in _draw.afterglow_defaults(config.load().appearance).items()),
      "...over the afterglow theme's colours as the default look")
check(st["size"] == 156 and st["opacity"] == 1.0 and st["pulse"] is True, "...and the 156 px size, full opacity and ring pulse by default")
check(st["anchor"] == "bottom_right" and st["style"] == "clapper" and st["enter"] == "slide", "...and the global settings")
check(played.count("/sounds/clap.wav") == 1 and "/sounds/global.wav" not in played,
      f"the clip type's clap sound plays once and replaces the global one ({played})")

# clap sound falls back to the usual chain when unset
reset()
cfg2 = clips.update_clip_config(cfg.id, indicator_clap_sound="")
clips.trigger_clip(cfg2.id)
check(played.count("/sounds/global.wav") == 1, "no clap sound set: the global clip sound plays, once")

# the global clapper-only sound: beats the usual chain, loses to the clip type's own clap sound
set_ind(clap_sound="/sounds/global_clap.wav")
reset()
clips.trigger_clip(cfg2.id)
check(played.count("/sounds/global_clap.wav") == 1 and "/sounds/global.wav" not in played,
      f"a global clap sound replaces the usual clip sound, once ({played})")
reset()
clips.trigger_clip(clips.update_clip_config(cfg.id, indicator_clap_sound="/sounds/clap.wav").id)
check(played.count("/sounds/clap.wav") == 1 and "/sounds/global_clap.wav" not in played,
      "...but the clip type's own clap sound wins over it")
clips.update_clip_config(cfg.id, indicator_clap_sound="")
set_ind(clap_sound="", clapper_opacity=0.4, ring_pulse=False, size=200)
reset()
clips.trigger_clip(cfg2.id)
st = MSGS[0]["style"]
check(st["opacity"] == 0.4 and st["pulse"] is False and st["size"] == 200, "opacity, pulse and size from Settings reach the start event")
set_ind(clapper_opacity=1.0, ring_pulse=True, size=156)

# indicator disabled: nothing sent, and the old sound chain is untouched
set_ind(enabled=False)
reset()
cfg3 = clips.update_clip_config(cfg.id, indicator_clap_sound="/sounds/clap.wav", sound_path="/sounds/own.wav")
clips.trigger_clip(cfg3.id)
check(MSGS == [], "indicator disabled: no event is sent")
check(played.count("/sounds/own.wav") == 1 and "/sounds/clap.wav" not in played,
      "...and the clip type's own sound is used as before (the clap sound only applies with the indicator on)")
set_ind(enabled=True)
cfg = clips.update_clip_config(cfg.id, sound_path="", indicator_clap_sound="")

# ------------------------------------------------------------------ failure: OBS refuses
reset()
FakeOBS.fail = True
try:
    clips.trigger_clip(cfg.id)
    check(False, "a failing capture raises")
except clips.ClipError:
    check(True, "a failing capture still raises")
FakeOBS.fail = False
ev = events()
check(ev == ["start", "fail"], f"OBS failing before the save: start, fail -- no clap ({ev})")

# failure after the clap (trim produces nothing usable)
reset()
orig_probe = clips.probe_duration
clips.probe_duration = lambda p: (_ for _ in ()).throw(clips.ClipError("probe failed"))
try:
    clips.trigger_clip(cfg.id)
except clips.ClipError:
    pass
clips.probe_duration = orig_probe
ev = events()
check(ev == ["start", "clap", "fail"], f"failing after the save: start, clap, fail ({ev})")

# id supplied by the caller (the daemon) is used as is; None = no indicator
reset()
clips.trigger_clip(cfg.id, indicator_id="daemon-1")
check(events() == ["start"] * 0 + ["clap", "done"] and all(m["id"] == "daemon-1" for m in MSGS),
      "an id passed in by the daemon is used (the daemon sent the start itself)")
reset()
clips.trigger_clip(cfg.id, indicator_id=None)
check(MSGS == [], "indicator_id=None: no events")

# ------------------------------------------------------------------ input overlay: processing continues, purple
cfg_o = clips.update_clip_config(cfg.id, overlay_enabled=True, overlay_pieces=["keyboard"], overlay_offset_ms=0,
                                 overlay_visible_default=True, overlay_placements={})
reset()
os.environ["FAKE_PUPPETRY_DELAY"] = "1"
clips.trigger_clip(cfg_o.id)
ev_now = [m["event"] for m in MSGS]
ic.flush()
mid = [m["event"] for m in MSGS]
check(mid == ["start", "clap", "overlay"], f"with an input overlay the clip's arrival sends 'overlay', not 'done' ({mid})")
clips.wait_for_overlays(120)
del os.environ["FAKE_PUPPETRY_DELAY"]
ev = events()
check(ev == ["start", "clap", "overlay", "overlay_done"], f"...then overlay_done when the render finishes ({ev})")

# overlay render fails
reset()
os.environ["PUPPETRY_OVERLAY"] = "/nonexistent/puppetry"
from afterglow import input_overlay as aio  # noqa: E402
orig_find = aio.find_tool
aio.find_tool = lambda: None
clips.trigger_clip(cfg_o.id)
clips.wait_for_overlays(120)
aio.find_tool = orig_find
ev = events()
check(ev[-2:] == ["overlay", "overlay_fail"] and "done" not in ev, f"the overlay render failing: overlay, overlay_fail ({ev})")

# overlay timeout
reset()
cancel = ic.arm_overlay_timeout("t1", seconds=0.2)
time.sleep(0.5)
check(events("t1") == ["overlay_fail"], "arm_overlay_timeout sends overlay_fail when the render takes too long")
reset()
cancel = ic.arm_overlay_timeout("t2", seconds=0.3)
cancel()
time.sleep(0.6)
check(events("t2") == [], "...and nothing once cancelled (the render finished)")
check(ic.arm_overlay_timeout(None)() is None, "no capture id: a harmless no-op")

# ------------------------------------------------------------------ client behaviour
check(ic.begin(cfg.id) is not None, "begin() returns a capture id when the indicator is on")
set_ind(enabled=False)
check(ic.begin(cfg.id) is None, "begin() returns None when it is off")
ic.emit(None, "clap")
ic.emit("x", "start")
ic.emit("x", "bogus")
reset()
ic.emit(None, "clap"); ic.emit("x", "start"); ic.emit("x", "bogus")
check(events() == [], "emit ignores a missing id, 'start' and unknown events")
set_ind(enabled=True)

ids = {ic.new_id() for _ in range(200)}
check(len(ids) == 200, "capture ids are unique")

reset()
set_ind(screen="focused")
from afterglow.indicator import focus  # noqa: E402
orig_f = focus.focused_window_center
focus.focused_window_center = lambda: {"x": 10.0, "y": 20.0}
cid = ic.begin(cfg.id)
ic.flush()
check(MSGS and MSGS[0]["style"]["screen_hint"] == {"x": 10.0, "y": 20.0}, "'focused' screen mode: the focused window's centre rides along")
reset()
set_ind(screen="primary")
ic.begin(cfg.id)
ic.flush()
check(MSGS[0]["style"]["screen_hint"] is None, "'primary' screen mode: no hint is looked up")
focus.focused_window_center = orig_f
set_ind(screen="focused")

# order is preserved across a burst
reset()
for i in range(50):
    ic.emit("burst", "clap" if i % 2 == 0 else "done")
ic.flush()
check([m["event"] for m in MSGS] == ["clap" if i % 2 == 0 else "done" for i in range(50)], "a burst of events is delivered in order")

# never raises / blocks with no helper and no display
ic.set_test_sink(None)
os.environ.pop("WAYLAND_DISPLAY", None)
os.environ.pop("DISPLAY", None)
t0 = time.time()
ic.emit("nohelper", "clap")
ic.send_raw({"id": "nohelper", "event": "done"})
check(time.time() - t0 < 0.5, "emit / send_raw return immediately even with nobody listening")
ic.flush(15)
ic.set_test_sink(MSGS.append)

env_keys = ic.display_environment()
check(isinstance(env_keys, dict), "display_environment returns a dict")
os.environ["WAYLAND_DISPLAY"] = "wayland-9"
check(ic.display_environment().get("WAYLAND_DISPLAY") == "wayland-9", "...taking the display from the process environment first")
os.environ.pop("WAYLAND_DISPLAY")

check(isinstance(ic.helper_command(), list) and ic.helper_command(), "helper_command returns a launch command")

# ------------------------------------------------------------------ daemon hooks
d = daemon.ClipDaemon()
reset()
d._on_hotkey(cfg.id)
ic.flush()
check([m["event"] for m in MSGS] == ["start"], "the hotkey sends 'start' immediately (before the capture even begins)")
qid, qcid = d._trigger_queue.get_nowait()
check(qid == cfg.id and qcid == MSGS[0]["id"], "...and queues the same capture id for the worker")
set_ind(enabled=False)
reset()
d._on_hotkey(cfg.id)
ic.flush()
qid, qcid = d._trigger_queue.get_nowait()
check(MSGS == [] and qcid is None, "indicator off: the hotkey sends nothing and queues no id")
set_ind(enabled=True)

# the worker: a capture before trigger_clip got going (clip type deleted) -> fail
reset()
d2 = daemon.ClipDaemon()
d2._trigger_queue.put((99999, "gone-1"))
th = threading.Thread(target=d2._trigger_worker_loop, daemon=True)
th.start()
deadline = time.time() + 10
while time.time() < deadline and "fail" not in [m["event"] for m in MSGS]:
    time.sleep(0.1)
    ic.flush()
check([(m["id"], m["event"]) for m in MSGS] == [("gone-1", "fail")], "a clip type that no longer exists: the clapper is told 'fail'")

# the worker: a stuck capture times out -> fail, and the next queued press still gets processed
reset()
daemon.CAPTURE_TIMEOUT_SECONDS = 0.5
gate = threading.Event()
real_trigger = clips.trigger_clip
order = []


def slow_trigger(clip_id, indicator_id=None):
    order.append(indicator_id)
    if indicator_id == "stuck":
        gate.wait(5)
        raise clips.ClipError("late")
    return real_trigger(clip_id, indicator_id=indicator_id)


clips.trigger_clip = slow_trigger
d2._trigger_queue.put((cfg.id, "stuck"))
d2._trigger_queue.put((cfg.id, "next"))
deadline = time.time() + 20
while time.time() < deadline and "next" not in order:
    time.sleep(0.1)
ic.flush()
check(("stuck", "fail") in [(m["id"], m["event"]) for m in MSGS], "a stuck capture: the clapper is told 'fail' when the daemon stops waiting")
check("next" in order, "...and the next queued press is processed without waiting for it")
gate.set()
d2._stop_flag.set()
clips.trigger_clip = real_trigger
th.join(5)
# the "next" capture keeps running in the background: let it finish so it can't pollute what follows
deadline = time.time() + 120
while time.time() < deadline and not any(m["id"] == "next" and m["event"] in ("done", "overlay_done", "overlay_fail", "fail") for m in MSGS):
    time.sleep(0.2)
clips.wait_for_overlays(120)
time.sleep(0.3)

# ------------------------------------------------------------------ the CLI path (no daemon): start comes from trigger_clip itself
def settle(quiet=1.0, limit=60.0):
    """Wait until no event has arrived for `quiet` seconds: a capture abandoned by the watchdog above can
    still be finishing on its thread (slowly, on a loaded machine) and must not leak into this section."""
    ic.flush()
    n, since, end = len(MSGS), time.time(), time.time() + limit
    while time.time() < end and time.time() - since < quiet:
        time.sleep(0.1)
        ic.flush()
        if len(MSGS) != n:
            n, since = len(MSGS), time.time()
settle()
reset()
daemon.CAPTURE_TIMEOUT_SECONDS = 120
clips.update_clip_config(cfg.id, overlay_enabled=False)
clips.trigger_clip(cfg.id)
_ev = events()
check(_ev == ["start", "clap", "done"], f"a CLI / GUI trigger (no daemon) starts the indicator itself ({_ev})")

# DB / config round trip
c = clips.get_clip_config(cfg.id)
c2 = clips.update_clip_config(c.id, indicator_colors={"body": "#123456", "stripes": "#abcdef"}, indicator_icon_path="/x.png",
                              indicator_clap_sound="/y.wav")
c3 = clips.get_clip_config(c2.id)
check(c3.indicator_colors == {"body": "#123456", "stripes": "#abcdef"} and c3.indicator_icon_path == "/x.png"
      and c3.indicator_clap_sound == "/y.wav", "clip-type indicator fields round-trip through the database")
st = config.load()
st.clip_indicator.anchor = "top_left"
st.clip_indicator.circle_opacity = 0.3
config.save(st)
st2 = config.load()
check(st2.clip_indicator.anchor == "top_left" and st2.clip_indicator.circle_opacity == 0.3, "global indicator settings round-trip through config")


# ---- the Hands style: front hand in the payload; the clap sound waits for the impact
set_ind(style="hands", hands_front="left")
check(clips.clap_sound_delay(config.load()) == __import__("afterglow.indicator", fromlist=["x"]).HANDS_CONTACT_MS / 1000.0,
      "hands: the clap sound is held back until the hands meet")
set_ind(style="clapper")
check(clips.clap_sound_delay(config.load()) == 0.0, "clapper: the clap sound plays at once")
set_ind(style="hands", hands_front="left")
reset()
t0 = time.time()
clips.trigger_clip(clips.update_clip_config(cfg.id, indicator_clap_sound="/sounds/clap.wav").id)
check(MSGS and MSGS[0]["style"]["hands_front"] == "left" and MSGS[0]["style"]["style"] == "hands", "hands_front reaches the start event")
deadline = time.time() + 3
while time.time() < deadline and "/sounds/clap.wav" not in played:
    time.sleep(0.02)
check(played.count("/sounds/clap.wav") == 1, "hands: the delayed clap sound still plays, once")
set_ind(style="clapper", hands_front="right")
clips.update_clip_config(cfg.id, indicator_clap_sound="")

# ---- the Hands look: in the payload, and the glove colours follow it
set_ind(style="hands", hands_look="cel")
reset()
clips.trigger_clip(cfg.id)
check(MSGS and MSGS[0]["style"]["hands_look"] == "cel", "hands_look reaches the start event")
check(MSGS and MSGS[0]["style"]["colors"]["glove"] == _draw.afterglow_defaults(config.load().appearance, "cel")["glove"],
      "...with the cel look's glove colour as the default")
set_ind(style="hands", hands_look="retro")
reset()
clips.trigger_clip(cfg.id)
from afterglow.indicator import hands2d as _h2
check(MSGS and MSGS[0]["style"]["colors"]["glove"] == _h2.RETRO_COLORS["glove"] and MSGS[0]["style"]["colors"]["cuff"] == _h2.RETRO_COLORS["cuff"],
      "...and the retro look's cream gloves and red cuffs by default")
set_ind(style="clapper", hands_look="retro")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
