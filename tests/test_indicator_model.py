"""
Clip indicator: the per-capture state machine and the stacking, driven with a fake clock
(pure Python -- no Qt windows).

    python3 tests/test_indicator_model.py
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow.indicator import model as M, draw, WATCHDOG

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def new(style=None, **kw):
    m = M.Model(**kw)
    return m


def st(m, id, t):
    m.tick(t)
    return m._inds[id].state if id in m._inds else "gone"


def states(m, id, times):
    return [st(m, id, t) for t in times]


# ---------------------------------------------------------------- plain flow, no overlay
m = new()
m.event("a", "start", 0.0, {"enter": "slide", "exit": "slide", "mode": "circle"})
check(st(m, "a", 0.1) == "enter", "start -> the enter animation plays")
m.event("a", "clap", 0.1)
check(st(m, "a", 0.2) == "enter", "OBS confirmed before the clapper landed: the clap WAITS for the enter animation")
check(st(m, "a", 0.31) == "clap", "...then claps the moment it has landed (slide = 300 ms)")
fr = m.frame(m._inds["a"], 0.35)
check(abs(fr.clap_ms - 50) < 1, f"the clap clock starts at landing, not at the event ({fr.clap_ms:.0f} ms at t=0.35)")
t_leave = 0.3 + draw.CLAP_TOTAL_MS / 1000.0
check(st(m, "a", t_leave - 0.01) == "clap" and st(m, "a", t_leave + 0.01) == "leave", "~400 ms after the clap: processing -> the exit animation")
check(st(m, "a", t_leave + 0.31) == "circle_in", "default mode: after the exit animation the gray circle fades in")
check(st(m, "a", t_leave + 0.55) == "circle", "...and keeps spinning while the clip is processed")
mm = new()
mm.event("a", "start", 0.0, {})
mm.event("a", "clap", 0.05)
mm.tick(0.98 + 0.10)
fr = mm.frame(mm._inds["a"], 0.98 + 0.10)
check(mm._inds["a"].state == "circle_in" and 0.4 < fr.circle_alpha < 0.6 and fr.circle_mix == 0.0, f"circle fades in over 200 ms, gray ({fr.circle_alpha:.2f} at halfway)")
m.event("a", "done", 3.0)
check(st(m, "a", 3.1) == "circle_out", "done -> the circle fades out")
check(st(m, "a", 3.26) == "gone" and len(m) == 0, "...and the capture is removed")

# ---------------------------------------------------------------- overlay flow (gray -> purple)
m = new()
m.event("a", "start", 0.0, {})
m.event("a", "clap", 0.05)
st(m, "a", 1.5)
check(m._inds["a"].state == "circle", "(gray circle up)")
m.event("a", "overlay", 2.0)
check(st(m, "a", 2.1) == "cross", "overlay -> the circle cross-fades gray -> purple")
fr = m.frame(m._inds["a"], 2.15)
check(abs(fr.circle_mix - 0.5) < 0.1, f"...300 ms cross-fade ({fr.circle_mix:.2f} halfway)")
check(st(m, "a", 2.4) == "overlay" and m.frame(m._inds["a"], 2.4).circle_mix == 1.0, "...then purple, still spinning")
m.event("a", "overlay_done", 5.0)
check(st(m, "a", 5.1) == "circle_out", "overlay_done -> the purple circle fades out")
st(m, "a", 5.3)
check(len(m) == 0, "...and it is gone")

# overlay failure: flash red + shake, not the launch
m = new()
m.event("a", "start", 0.0, {})
m.event("a", "clap", 0.05)
m.event("a", "overlay", 2.0)
st(m, "a", 3.0)
m.event("a", "overlay_fail", 3.0)
check(st(m, "a", 3.05) == "circle_fail", "overlay_fail -> the circle flashes red (NOT the launch: the clip is safe)")
fr = m.frame(m._inds["a"], 3.2)
check(fr.circle_red > 0.9 and fr.circle_dx != 0, "...red and shaking")
st(m, "a", 3.8)
check(len(m) == 0, "...then it is gone")

# ---------------------------------------------------------------- stay mode
m = new()
m.event("a", "start", 0.0, {"mode": "stay"})
m.event("a", "clap", 0.05)
check(st(m, "a", 1.0) == "stay" and m.frame(m._inds["a"], 1.0).idle_t > 0, "stay mode: the clapper stays (idle bob) after the clap")
check(st(m, "a", 20.0) == "stay", "...for as long as the clip is processing")
m.event("a", "done", 20.0)
check(st(m, "a", 20.1) == "leave", "done -> the exit animation")
st(m, "a", 20.5)
check(len(m) == 0, "...and no circle at all (the clip was done)")

m = new()
m.event("a", "start", 0.0, {"mode": "stay"})
m.event("a", "clap", 0.05)
st(m, "a", 5.0)
m.event("a", "overlay", 5.0)
check(st(m, "a", 5.1) == "leave", "stay + overlay: the clapper exits once the clip is in the library")
check(st(m, "a", 5.4) == "circle_in" and m.frame(m._inds["a"], 5.4).circle_mix == 1.0,
      "...and the PURPLE circle fades in (no gray flash)")
check(st(m, "a", 5.8) == "overlay", "...a circle that came in purple stays purple")
m.event("a", "overlay_done", 9.0)
st(m, "a", 9.4)
check(len(m) == 0, "...until the overlay is done")

# ---------------------------------------------------------------- everything arrives at once (fast capture)
m = new()
m.event("a", "start", 0.0, {})
for ev in ("clap", "done"):
    m.event("a", ev, 0.01)
st(m, "a", 0.05)
check(st(m, "a", 0.5) == "clap" and st(m, "a", 0.8) == "leave", "an early `done` is remembered: enter, clap, exit as usual")
check(st(m, "a", 1.2) == "gone" and len(m) == 0, "...and no circle is shown for a clip that is already done")

m = new()
m.event("a", "start", 0.0, {})
for ev in ("clap", "overlay", "overlay_done"):
    m.event("a", ev, 0.01)
check(st(m, "a", 3.0) == "gone", "overlay finished before the circle ever showed: nothing left to show")

# done without a clap implies the clap
m = new()
m.event("a", "start", 0.0, {})
m.event("a", "done", 0.5)
check(st(m, "a", 0.6) == "clap", "`done` with no clap event still claps (OBS event missing)")

# `processing` is accepted as an early trigger
m = new()
m.event("a", "start", 0.0, {})
m.event("a", "processing", 0.5)
check(st(m, "a", 0.6) == "clap", "`processing` is accepted (implies the clap)")

# ---------------------------------------------------------------- failure from every state
def fail_at(setup, t_fail, expect_first, expect_after=None, label=""):
    m = new()
    m.event("a", "start", 0.0, {})
    setup(m)
    m.tick(t_fail)
    m.event("a", "fail", t_fail)
    s = m._inds["a"].state
    check(s == expect_first, f"fail during {label}: {s} (expected {expect_first})")
    return m

m = fail_at(lambda m: None, 0.1, "enter", label="enter -- waits for the landing")
check(st(m, "a", 0.31) == "fail", "...then the launch begins as soon as it has landed")
st(m, "a", 0.31 + 0.95)
check(len(m) == 0, "...and it falls off screen (900 ms) and is removed")
m = fail_at(lambda m: None, 0.5, "fail", label="hold")
m = fail_at(lambda m: m.event("a", "clap", 0.1), 0.5, "fail", label="the clap")
m = fail_at(lambda m: (m.event("a", "clap", 0.1), m.tick(1.2)), 1.2, "popin", label="the exit animation -- popin after it")
st(m, "a", 1.0 + 0.3)
m2 = new()
m2.event("a", "start", 0.0, {"mode": "stay"})
m2.event("a", "clap", 0.05)
m2.tick(3.0)
m2.event("a", "fail", 3.0)
check(m2._inds["a"].state == "fail", "fail in stay mode: launched from where it sits")
m = new()
m.event("a", "start", 0.0, {})
m.event("a", "clap", 0.05)
st(m, "a", 2.0)
m.event("a", "fail", 2.0)
check(m._inds["a"].state == "popin", "fail while the circle is showing: the clapper pops back in at the circle's spot first")
fr = m.frame(m._inds["a"], 2.05)
check(fr.anim[:2] == ("enter", "pop") and fr.circle_alpha > 0, "...the circle fades as it pops")
check(st(m, "a", 2.35) == "fail", "...then it is launched")
m = new()
m.event("a", "start", 0.0, {})
m.event("a", "clap", 0.05)
m.event("a", "overlay", 2.0)
st(m, "a", 2.5)
m.event("a", "fail", 3.0)
check(m._inds["a"].state == "popin", "a capture timeout (fail) during the purple circle also pops + launches")

# ---------------------------------------------------------------- stacking
m = new()
for i, t in enumerate((0.0, 0.1, 0.2)):
    m.event(f"c{i}", "start", t, {"anchor": "bottom_right"}, screen_key="S")
key = ("S", "bottom_right")
check([i.id for i in m.visible(key)] == ["c0", "c1", "c2"], "rapid repeats stack, oldest first")
check([m._inds[f"c{i}"].slot_to for i in range(3)] == [0.0, 1.0, 2.0], "slot 0 at the anchor, each later one a slot further out")
check(m._inds["c2"].state == "enter" and m.frame(m._inds["c2"], 0.25).slot == 2.0, "a new clapper enters straight into the next free slot")
for i in range(3):
    m.event(f"c{i}", "clap", 0.5)
m.event("c0", "done", 1.0)
m.tick(1.3)
st(m, "c0", 2.0)
check("c0" not in m._inds, "the first finishes and is removed")
check(m._inds["c1"].slot_to == 0.0 and m._inds["c2"].slot_to == 1.0, "the ones above it close the gap")
t_removed = None
fr_mid = m.frame(m._inds["c2"], m._inds["c2"].slot_t0 + 0.1)
check(abs(fr_mid.slot - 1.5) < 0.05, f"...sliding down over 200 ms ({fr_mid.slot:.2f} halfway between slot 2 and 1)")
check(m.frame(m._inds["c2"], m._inds["c2"].slot_t0 + 0.25).slot == 1.0, "...and settled")

# a slot is held for the whole life: clapper, then gray, then purple circle
m = new()
for i in range(2):
    m.event(f"c{i}", "start", 0.0, {"anchor": "top"}, screen_key="S")
    m.event(f"c{i}", "clap", 0.1)
m.tick(2.0)
check(all(i.state == "circle" for i in m.visible(("S", "top"))) and [i.slot_to for i in m.visible(("S", "top"))] == [0.0, 1.0],
      "circles stack the same way (slots held through the whole capture)")

# cap of 5 visible, the rest in a +N badge
m = new()
for i in range(8):
    m.event(f"c{i}", "start", 0.0, {}, screen_key="S")
key = ("S", "bottom_right")
check(len(m.visible(key)) == 5 and m.overflow(key) == 3, "5 visible, the 6th and beyond collapse into a +N badge")
check(all(m._inds[f"c{i}"].state == "queued" for i in range(5, 8)), "(the extras haven't started)")
m.event("c0", "clap", 0.5)
m.event("c0", "done", 0.5)
m.tick(2.0)
check("c0" not in m._inds and len(m.visible(key)) == 5 and m.overflow(key) == 2,
      "a finishing capture frees a slot: the next queued one enters, badge shrinks")
m.event("c7", "fail", 2.5)
check("c7" not in m._inds and m.overflow(key) == 1, "a capture that failed while hidden is dropped silently")
for i in (1, 2, 3, 4, 5):
    m.event(f"c{i}", "clap", 3.0)
    m.event(f"c{i}", "done", 3.0)
m.tick(8.0)
m.event("c6", "clap", 8.0)
m.event("c6", "done", 8.0)
m.tick(12.0)
check(len(m) == 0, "everything finishes -> nothing left (the surface can hide)")

# separate screens / anchors get separate stacks
m = new()
m.event("a", "start", 0.0, {"anchor": "top_left"}, screen_key="S1")
m.event("b", "start", 0.0, {"anchor": "top_left"}, screen_key="S2")
check(len(m.stack_keys()) == 2 and m._inds["b"].slot_to == 0.0, "a capture on another screen gets its own stack")

# ---------------------------------------------------------------- watchdog + late events
m = new(watchdog=100.0)
m.event("a", "start", 0.0, {"mode": "stay"})
m.event("a", "clap", 0.1)
m.tick(50.0)
check("a" in m._inds, "inside the watchdog window it stays")
m.tick(101.0)
m.tick(103.0)
check(len(m) == 0, "no event for the watchdog time -> the indicator winds down on its own (nothing hangs on screen)")
m = new(watchdog=100.0)
m.event("a", "start", 0.0, {})
m.tick(150.0)
m.tick(155.0)
check(len(m) == 0, "...even one still waiting for its clap")
m.event("a", "done", 160.0)
m.event("zzz", "clap", 1.0)
check(len(m) == 0, "events for unknown / finished ids are ignored")
m.event("a", "start", 161.0, {})
check(len(m) == 0, "a finished id can't be started again by a stray late start")

# unknown values in style fall back to defaults
s = M.Style.from_dict({"enter": "nope", "exit": "nope", "anchor": "middle", "style": "x", "mode": "y", "size": 99999, "padding_x": -5})
check((s.enter, s.exit, s.anchor, s.style, s.mode) == ("slide", "slide", "bottom_right", "clapper", "circle") and s.size == 512 and s.padding_x == 0,
      "bad style values are clamped / defaulted")


# ---------------------------------------------------------------- style defaults
d0 = M.Style.from_dict({})
check(d0.size == 156 and d0.opacity == 1.0 and d0.pulse is True, "style defaults: 156 px, fully opaque, ring pulse on")
check(M.Style.from_dict({"anchor": "center"}).enter == "fade" and M.Style.from_dict({"anchor": "center"}).exit == "fade", "the centre defaults to fading in and out")
check(M.Style.from_dict({"anchor": "center", "enter": "pop"}).enter == "pop" and M.Style.from_dict({"anchor": "top"}).enter == "slide", "...an explicit choice wins; other anchors default to slide")
check(M.Style.from_dict({"opacity": 0}).opacity == 0.05 and M.Style.from_dict({"opacity": 3}).opacity == 1.0 and M.Style.from_dict({"opacity": 0.4}).opacity == 0.4, "clapper opacity is clamped to 5%..100%")
check(M.Style.from_dict({"pulse": False}).pulse is False, "the pulse can be switched off")

# ---------------------------------------------------------------- ready until the clap, shut after
m = new()
m.event("a", "start", 0.0, {})
m.tick(0.1)
check(m.frame(m._inds["a"], 0.1).clapped is False and m.frame(m._inds["a"], 0.1).clap_ms < 0, "entering: ready to clap (not shut)")
m.tick(0.31)
check(m.frame(m._inds["a"], 0.31).clapped is False, "waiting for OBS: still ready")
m.event("a", "clap", 0.5)
m.tick(0.55)
fr = m.frame(m._inds["a"], 0.55)
check(fr.state == "clap" and fr.clap_ms >= 0, "the clap runs from the ready pose")
m.tick(1.0)
check(m.frame(m._inds["a"], 1.0).state == "leave" and m.frame(m._inds["a"], 1.0).clapped, "afterwards it rests shut while it leaves")
m = new()
m.event("a", "start", 0.0, {"mode": "stay"})
m.event("a", "clap", 0.1)
m.tick(1.5)
check(m._inds["a"].state == "stay" and m.frame(m._inds["a"], 1.5).clapped, "stay mode: shut, bobbing")
m = new()
m.event("a", "start", 0.0, {})
m.event("a", "done", 0.1)                 # `done` without a clap implies one
m.tick(0.35)
check(m._inds["a"].state == "clap" and m._inds["a"].has("clap"), "a `done` without a clap implies the clap")
m.tick(0.9)
check(m.frame(m._inds["a"], 0.9).clapped, "...so the item leaves shut")
m = new()
m.event("a", "start", 0.0, {})
m.tick(0.31)
m.event("a", "fail", 0.4)
m.tick(0.45)
check(m._inds["a"].state == "fail" and not m.frame(m._inds["a"], 0.45).clapped, "a failure before OBS confirmed: launched from the ready pose (never clapped)")

# ---------------------------------------------------------------- the ring pulses as each stage completes
def pulse_at(m, id, t):
    m.tick(t)
    return m.frame(m._inds[id], t).circle_pulse if id in m._inds else None


m = new()
m.event("a", "start", 0.0, {})
m.event("a", "clap", 0.05)
t_ring = 0.3 + draw.CLAP_TOTAL_MS / 1000.0 + 0.3          # the ring arrives after the exit animation
m.tick(t_ring + 0.01)
check(0 <= pulse_at(m, "a", t_ring + 0.05) < 0.3, "the ring pulses as it arrives (the clip is saved)")
check(pulse_at(m, "a", t_ring + 0.2) > 0.3, "...the pulse runs over ~0.4 s")
check(pulse_at(m, "a", t_ring + 0.6) == -1.0, "...and then it is over")
m.event("a", "overlay", t_ring + 1.0)
check(0 <= pulse_at(m, "a", t_ring + 1.05) < 0.3 and m._inds["a"].state == "cross", "the ring pulses as it turns purple (the clip is in the library)")
m.event("a", "overlay_done", t_ring + 2.5)
check(0 <= pulse_at(m, "a", t_ring + 2.55) < 0.3 and m._inds["a"].state == "circle_out", "...and bursts as it leaves (the overlay finished)")
m = new()
m.event("a", "start", 0.0, {})
m.event("a", "clap", 0.05)
m.tick(t_ring + 1.0)
m.event("a", "done", t_ring + 1.0)
check(0 <= pulse_at(m, "a", t_ring + 1.05) < 0.3, "no overlay: it bursts as it leaves when the clip is in the library")
m = new()
m.event("a", "start", 0.0, {"pulse": False})
m.event("a", "clap", 0.05)
m.tick(t_ring + 0.05)
m.event("a", "overlay", t_ring + 1.0)
m.event("a", "overlay_done", t_ring + 2.5)
res = [pulse_at(m, "a", t_ring + dt) if "a" in m._inds else -1.0 for dt in (0.05, 0.1, 1.05, 1.1, 2.55, 2.6)]
check(all(r == -1.0 for r in res), "pulse off: the ring never pulses")
m = new()
m.event("a", "start", 0.0, {})
m.event("a", "clap", 0.05)
m.tick(t_ring + 1.0)
m.event("a", "overlay", t_ring + 1.0)
m.tick(t_ring + 1.4)
m.event("a", "overlay_fail", t_ring + 1.5)
check(pulse_at(m, "a", t_ring + 1.6) == -1.0 and m._inds["a"].state == "circle_fail", "a failing overlay shakes red instead of pulsing")


# ---- the Hands style: a longer clap (wind-up first), idling age, which hand is in front
mh = M.Model()
mh.event("h", "start", 0.0, {"style": "hands", "enter": "fade", "hands_front": "left"}, "s")
mh.tick(0.5)
mh.event("h", "clap", 0.5)
mh.tick(0.5 + draw.CLAP_TOTAL_MS / 1000.0 + 0.01)
check(mh._inds["h"].state == "clap", "hands: still clapping after the clapper's clap length (the wind-up comes first)")
mh.tick(0.5 + draw.HANDS_CLAP_TOTAL_MS / 1000.0 + 0.01)
check(mh._inds["h"].state != "clap", "hands: the clap ends after the hands' own clap length")
fr = mh.frame(mh._inds["h"], 1.2)
check(abs(fr.age - 1.2) < 1e-6 and fr.clapped, "frames carry the capture's age (the idle wobble) and the clapped flag")
check(mh._inds["h"].style.hands_front == "left" and M.Style.from_dict({"hands_front": "junk"}).hands_front == "right",
      "hands_front is carried in the style, unknown values fall back to right")
check(M.Style.from_dict({}).hands_look == "retro" and M.Style.from_dict({"hands_look": "cel"}).hands_look == "cel"
      and M.Style.from_dict({"hands_look": "junk"}).hands_look == "retro", "hands_look: retro by default, cel when chosen, junk falls back")
fr0 = mh.frame(mh._inds["h"], 0.5)
check(abs(fr0.since_clap - 0.0) < 1e-6, "since_clap starts at the clap")
fr2 = mh.frame(mh._inds["h"], 2.0)
check(abs(fr2.since_clap - 1.5) < 1e-6, "...and keeps counting after the clap state ends (the clasp's idle runs on it)")
m0 = M.Model()
m0.event("z", "start", 0.0, {"style": "hands", "enter": "fade"}, "s")
m0.tick(0.3)
check(m0.frame(m0._inds["z"], 0.3).since_clap == -1.0, "...and is -1 before the clap")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("ALL PASS")
