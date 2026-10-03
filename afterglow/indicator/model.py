"""
The indicator's brain: one state machine per capture plus the stacking of rapid repeats.
Pure Python -- no Qt, no wall clock (every call takes ``now`` in seconds), so the whole
timeline can be driven with a fake clock in tests and the Qt surface just asks "what do I
draw at time t".

Per-capture states
  queued      beyond the visible stack ("+N" badge); starts when a slot frees up
  enter       the chosen enter animation
  hold        landed, waiting for the clap (OBS confirming the save)
  clap        the clap (draw.clap_total_ms(style): the hands wind up first, so theirs is longer)
  stay        "stay" mode: the clapper stays on screen, idle bob, until the clip is done
  leave       the chosen exit animation
  circle_in   the loading circle fades in where the clapper's centre was
  circle      gray circle while the clip is processed
  cross       gray -> purple (the input overlay is being rendered)
  overlay     purple circle
  circle_out  the circle fades out (done / overlay_done)
  circle_fail the circle flashes red, shakes and fades (overlay_fail -- the clip itself is safe)
  popin       the clapper pops back in at the circle's spot (so it can be launched)
  fail        launched into the air, falls off the screen
  gone        finished; removed from the stack

Events (the JSON protocol): start, clap, processing, done, overlay, overlay_done,
overlay_fail, fail -- see HANDOFF.md for which pipeline moment sends which.  They are
remembered as flags, so an event that arrives early (OBS can confirm in under 300 ms, i.e.
before the enter animation has landed) is simply honoured when its turn comes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import (ANCHORS, DEFAULT_ANCHOR, ENTER_ANIMATIONS, EXIT_ANIMATIONS, MAX_VISIBLE, PROCESSING_MODES,
               SCREEN_MODES, STYLES, WATCHDOG, HANDS_FRONT, HANDS_LOOKS, DEFAULT_HANDS_LOOK)
from . import draw

CIRCLE_FADE_IN = 0.200
CIRCLE_CROSS = 0.300
CIRCLE_FADE_OUT = 0.250
CIRCLE_FAIL = 0.700
SLOT_SLIDE = 0.200            # the 200 ms ease that closes a gap in the stack
POP_IN = draw.duration_ms("enter", "pop") / 1000.0
FAIL_TIME = draw.duration_ms("fail", "fail") / 1000.0

CLAPPER_STATES = ("enter", "hold", "clap", "stay", "leave", "popin", "fail")
CIRCLE_STATES = ("circle_in", "circle", "cross", "overlay", "circle_out", "circle_fail")
# while processing: the element showing it (the loading circle, or the item in "stay" mode) can be
# clicked to toggle the processing throttle's bypass
PROCESSING_STATES = ("circle_in", "circle", "cross", "overlay", "stay")
LIVE_STATES = CLAPPER_STATES + CIRCLE_STATES


@dataclass
class Style:
    """Everything a capture needs to draw itself (sent with ``start``; see indicator_client)."""
    style: str = "clapper"
    colors: dict = field(default_factory=dict)
    icon: str = ""
    anchor: str = DEFAULT_ANCHOR
    padding_x: float = 32.0
    padding_y: float = 32.0
    size: float = 156.0
    enter: str = "slide"
    exit: str = "slide"
    mode: str = "circle"
    circle_color: str = draw.DEFAULT_CIRCLE_COLOR
    overlay_circle_color: str = draw.DEFAULT_OVERLAY_CIRCLE_COLOR
    circle_opacity: float = 0.55
    opacity: float = 1.0                     # the clapper's own opacity
    pulse: bool = True                       # the ring pulses when a stage completes
    hands_front: str = "right"               # the Hands style: which glove ends up in front
    hands_look: str = DEFAULT_HANDS_LOOK     # the Hands style: "retro" / "cel"
    throttle: bool = False                   # processing throttle is on: the processing element toggles its bypass
    screen: str = "focused"
    screen_hint: "dict | None" = None        # {"x": .., "y": ..} = centre of the focused window

    @classmethod
    def from_dict(cls, d: "dict | None") -> "Style":
        d = d or {}
        s = cls()
        s.style = d.get("style") if d.get("style") in STYLES else "clapper"
        s.colors = dict(d.get("colors") or {})
        s.icon = str(d.get("icon") or "")
        s.anchor = d.get("anchor") if d.get("anchor") in ANCHORS else DEFAULT_ANCHOR
        s.padding_x = max(0.0, float(d.get("padding_x", 32)))
        s.padding_y = max(0.0, float(d.get("padding_y", 32)))
        s.size = min(512.0, max(24.0, float(d.get("size", 156))))
        fallback = "fade" if s.anchor == "center" else "slide"      # the centre has no edge to slide in from
        s.enter = d.get("enter") if d.get("enter") in ENTER_ANIMATIONS else fallback
        s.exit = d.get("exit") if d.get("exit") in EXIT_ANIMATIONS else fallback
        s.mode = d.get("mode") if d.get("mode") in PROCESSING_MODES else "circle"
        s.circle_color = str(d.get("circle_color") or draw.DEFAULT_CIRCLE_COLOR)
        s.overlay_circle_color = str(d.get("overlay_circle_color") or draw.DEFAULT_OVERLAY_CIRCLE_COLOR)
        s.circle_opacity = min(1.0, max(0.05, float(d.get("circle_opacity", 0.55))))
        s.opacity = min(1.0, max(0.05, float(d.get("opacity", 1.0))))
        s.pulse = bool(d.get("pulse", True))
        s.hands_front = d.get("hands_front") if d.get("hands_front") in HANDS_FRONT else "right"
        s.hands_look = d.get("hands_look") if d.get("hands_look") in HANDS_LOOKS else DEFAULT_HANDS_LOOK
        s.throttle = bool(d.get("throttle", False))
        s.screen = d.get("screen") if d.get("screen") in SCREEN_MODES else "focused"
        hint = d.get("screen_hint")
        s.screen_hint = dict(hint) if isinstance(hint, dict) else None
        return s


@dataclass
class Frame:
    """What to draw for one capture right now."""
    id: str
    state: str
    slot: float                                # fractional stack slot (0 = at the anchor)
    anim: "tuple[str, str, float] | None" = None   # (phase, kind, t) of the clapper, None = not drawn
    clap_ms: float = -1.0                      # >= 0 while clapping (feed to draw.clap_pose)
    idle_t: float = -1.0                       # >= 0 in "stay" mode (feed to draw.idle_bob)
    circle_alpha: float = 0.0
    circle_angle: float = 0.0
    circle_mix: float = 0.0                    # 0 = gray ... 1 = purple
    circle_dx: float = 0.0
    circle_red: float = 0.0
    circle_pulse: float = -1.0                 # 0..1 while the ring is pulsing (a stage just completed), else -1
    clapped: bool = False                      # the clap has happened (the item rests shut); False = ready / open
    age: float = 0.0                           # seconds since the capture appeared (the hands idle with it)
    since_clap: float = -1.0                   # seconds since the clap began, -1 before it (the clasp's idle runs on it)


class Indicator:
    def __init__(self, id: str, style: Style, screen_key: str, now: float):
        self.id = id
        self.style = style
        self.screen_key = screen_key
        self.state = "queued"
        self.t0 = now                    # when the current state began
        self.born = now
        self.last_event = now
        self.flags: "dict[str, float]" = {}          # event name -> time it arrived
        self.slot_from = self.slot_to = 0.0
        self.slot_t0 = now
        self.mix = 0.0                   # gray -> purple, remembered for the fade-out
        self.circle_alpha_start = 1.0    # alpha the circle had when it started fading out
        self.pulse_t0 = -1.0             # when the ring's last pulse began (a stage completed), -1 = never
        self.clap_t0 = -1.0              # when the clap state began, -1 = not yet

    # -- slots
    def slot_at(self, now: float) -> float:
        t = (now - self.slot_t0) / SLOT_SLIDE
        return draw.lerp(self.slot_from, self.slot_to, draw.ease_in_out(t))

    def move_slot(self, target: float, now: float) -> None:
        if target != self.slot_to:
            self.slot_from = self.slot_at(now)
            self.slot_to = target
            self.slot_t0 = now

    def has(self, name: str) -> bool:
        return name in self.flags

    def enter_dur(self) -> float:
        return draw.duration_ms("enter", self.style.enter) / 1000.0

    def exit_dur(self) -> float:
        return draw.duration_ms("exit", self.style.exit) / 1000.0


TERMINAL_FLAGS = ("done", "overlay_done", "overlay_fail", "fail")


class Model:
    """All live captures, grouped into one stack per (screen, anchor) surface."""

    def __init__(self, max_visible: int = MAX_VISIBLE, watchdog: float = WATCHDOG):
        self.max_visible = max_visible
        self.watchdog = watchdog
        self._inds: "dict[str, Indicator]" = {}
        self._stacks: "dict[tuple[str, str], list[Indicator]]" = {}
        self._rolled: "set[str]" = set()          # ids already finished (late events are ignored)

    # ------------------------------------------------------------ queries

    def __len__(self) -> int:
        return len(self._inds)

    def has(self, id: str) -> bool:
        return id in self._inds

    def stack_keys(self) -> "list[tuple[str, str]]":
        return [k for k, v in self._stacks.items() if v]

    def style_of(self, key: "tuple[str, str]") -> "Style | None":
        stack = self._stacks.get(key)
        return stack[-1].style if stack else None

    def visible(self, key: "tuple[str, str]") -> "list[Indicator]":
        return [i for i in self._stacks.get(key, []) if i.state != "queued"]

    def overflow(self, key: "tuple[str, str]") -> int:
        return sum(1 for i in self._stacks.get(key, []) if i.state == "queued")

    def stack_alive(self, key: "tuple[str, str]") -> bool:
        return bool(self.visible(key))

    # ------------------------------------------------------------ events

    def event(self, id: str, name: str, now: float, style: "Style | dict | None" = None,
              screen_key: str = "") -> None:
        if name == "start":
            if id in self._inds or id in self._rolled:
                return
            st = style if isinstance(style, Style) else Style.from_dict(style)
            ind = Indicator(id, st, screen_key, now)
            self._inds[id] = ind
            stack = self._stacks.setdefault((screen_key, st.anchor), [])
            stack.append(ind)
            self._refresh_stack((screen_key, st.anchor), now)
            return
        ind = self._inds.get(id)
        if ind is None:
            return
        if name not in ("clap", "processing", "done", "overlay", "overlay_done", "overlay_fail", "fail"):
            return
        ind.last_event = now
        ind.flags.setdefault("clap" if name == "processing" else name, now)
        if ind.state == "queued" and name in TERMINAL_FLAGS:
            self._remove(ind, now)       # never shown, nothing left to show
            return
        self._advance(ind, now)
        self._sweep(now)

    def tick(self, now: float) -> None:
        for ind in list(self._inds.values()):
            if ind.state != "queued" and now - ind.last_event > self.watchdog:
                for f in ("clap", "done", "overlay_done"):   # nothing has been heard for far too long: wind down
                    ind.flags.setdefault(f, now)
                ind.last_event = now
            self._advance(ind, now)
        self._sweep(now)

    # ------------------------------------------------------------ the machine

    def _go(self, ind: Indicator, state: str, t0: float) -> None:
        ind.state = state
        ind.t0 = t0
        if state == "clap":
            ind.clap_t0 = t0
        # the ring pulses as each stage completes: it arrives (the clip is saved), turns purple (the
        # clip is in the library, the overlay renders), and bursts as it leaves (everything done)
        if ind.style.pulse and state in ("circle_in", "cross", "circle_out"):
            ind.pulse_t0 = t0

    def _advance(self, ind: Indicator, now: float) -> None:
        for _ in range(32):                        # a long gap can chain several transitions
            if not self._step(ind, now):
                return

    def _step(self, ind: Indicator, now: float) -> bool:
        s, el = ind.state, now - ind.t0
        f = ind.flags
        st = ind.style
        if s == "enter":
            d = ind.enter_dur()
            if el >= d:
                self._go(ind, "hold", ind.t0 + d)
                return True
        elif s == "hold":
            if "fail" in f:
                self._go(ind, "fail", max(ind.t0, f["fail"]))
                return True
            for name in ("clap", "done", "overlay"):       # `done` without a clap implies it
                if name in f:
                    f.setdefault("clap", f[name])
                    self._go(ind, "clap", max(ind.t0, f[name]))
                    return True
        elif s == "clap":
            if "fail" in f:
                self._go(ind, "fail", max(ind.t0, f["fail"]))
                return True
            total = draw.clap_total_ms(st.style)
            if el * 1000.0 >= total:
                end = ind.t0 + total / 1000.0
                self._go(ind, "leave" if st.mode == "circle" else "stay", end)
                return True
        elif s == "stay":
            if "fail" in f:
                self._go(ind, "fail", max(ind.t0, f["fail"]))
                return True
            if "done" in f or "overlay" in f:
                self._go(ind, "leave", max(ind.t0, f.get("done", f.get("overlay", ind.t0))))
                return True
        elif s == "leave":
            d = ind.exit_dur()
            if el >= d:
                end = ind.t0 + d
                if "fail" in f:
                    ind.circle_alpha_start = 0.0         # no circle showing: just the clapper popping back in
                    self._go(ind, "popin", end)
                elif "overlay" in f:
                    if "overlay_done" in f or "overlay_fail" in f:
                        self._go(ind, "gone", end)
                    else:
                        ind.mix = 1.0
                        self._go(ind, "circle_in", end)
                elif "done" in f:
                    self._go(ind, "gone", end)
                else:
                    ind.mix = 0.0
                    self._go(ind, "circle_in", end)
                return True
        elif s in ("circle_in", "circle", "cross", "overlay"):
            alpha_now = self._circle_alpha(ind, now)
            if "fail" in f:                              # the circle is replaced by the clapper popping back in
                ind.circle_alpha_start = alpha_now
                self._go(ind, "popin", max(ind.t0, f["fail"]))
                return True
            if "overlay_fail" in f and s != "circle" and "overlay" in f:
                self._go(ind, "circle_fail", max(ind.t0, f["overlay_fail"]))
                return True
            if "overlay_done" in f and "overlay" in f:
                ind.circle_alpha_start = alpha_now
                self._go(ind, "circle_out", max(ind.t0, f["overlay_done"]))
                return True
            if s in ("circle_in", "circle") and "done" in f and "overlay" not in f:
                ind.circle_alpha_start = alpha_now
                self._go(ind, "circle_out", max(ind.t0, f["done"]))
                return True
            if s in ("circle_in", "circle") and "overlay" in f and ind.mix < 1.0:
                ind.mix = 0.0                            # a gray circle becomes purple: the cross-fade
                self._go(ind, "cross", max(ind.t0, f["overlay"]))
                return True
            if s == "circle_in" and el >= CIRCLE_FADE_IN:
                # a circle that came in purple (the overlay was already rendering) stays purple
                self._go(ind, "overlay" if ind.mix >= 1.0 else "circle", ind.t0 + CIRCLE_FADE_IN)
                return True
            if s == "cross" and el >= CIRCLE_CROSS:
                ind.mix = 1.0
                self._go(ind, "overlay", ind.t0 + CIRCLE_CROSS)
                return True
        elif s == "circle_out":
            if el >= CIRCLE_FADE_OUT:
                self._go(ind, "gone", ind.t0 + CIRCLE_FADE_OUT)
                return True
        elif s == "circle_fail":
            if el >= CIRCLE_FAIL:
                self._go(ind, "gone", ind.t0 + CIRCLE_FAIL)
                return True
        elif s == "popin":
            if el >= POP_IN:
                self._go(ind, "fail", ind.t0 + POP_IN)
                return True
        elif s == "fail":
            if el >= FAIL_TIME:
                self._go(ind, "gone", ind.t0 + FAIL_TIME)
                return True
        return False

    # ------------------------------------------------------------ stacks

    def _sweep(self, now: float) -> None:
        for ind in [i for i in self._inds.values() if i.state == "gone"]:
            self._remove(ind, now)

    def _remove(self, ind: Indicator, now: float) -> None:
        self._inds.pop(ind.id, None)
        self._rolled.add(ind.id)
        if len(self._rolled) > 512:
            self._rolled = set(list(self._rolled)[-256:])
        key = (ind.screen_key, ind.style.anchor)
        stack = self._stacks.get(key, [])
        if ind in stack:
            stack.remove(ind)
        self._refresh_stack(key, now)

    def _refresh_stack(self, key: "tuple[str, str]", now: float) -> None:
        """Re-number the slots (closing any gap with the 200 ms slide) and start whichever
        queued capture now has room."""
        stack = self._stacks.get(key, [])
        shown = 0
        for ind in list(stack):
            if ind.state == "queued":
                if shown >= self.max_visible:
                    continue
                self._go(ind, "enter", now)
                ind.slot_from = ind.slot_to = float(shown)       # a new one enters straight into its slot
                ind.slot_t0 = now
            else:
                ind.move_slot(float(shown), now)
            shown += 1
            if ind.state == "enter":
                self._advance(ind, now)
        if not stack:
            self._stacks.pop(key, None)

    # ------------------------------------------------------------ drawing

    def _circle_alpha(self, ind: Indicator, now: float) -> float:
        el = now - ind.t0
        s = ind.state
        if s == "circle_in":
            return draw.clamp01(el / CIRCLE_FADE_IN)
        if s in ("circle", "cross", "overlay"):
            return 1.0
        if s == "circle_out":
            return getattr(ind, "circle_alpha_start", 1.0) * (1.0 - draw.ease_in_quad(el / CIRCLE_FADE_OUT))
        if s == "circle_fail":
            return draw.circle_fail(el / CIRCLE_FAIL)[2]
        if s == "popin":
            return getattr(ind, "circle_alpha_start", 0.0) * draw.clamp01(1.0 - el / 0.15)
        return 0.0

    def frame(self, ind: Indicator, now: float) -> Frame:
        s, el = ind.state, now - ind.t0
        st = ind.style
        fr = Frame(id=ind.id, state=s, slot=ind.slot_at(now))
        fr.age = max(0.0, now - ind.born)
        if getattr(ind, "clap_t0", -1.0) >= 0:
            fr.since_clap = max(0.0, now - ind.clap_t0)
        fr.clapped = s in ("leave", "stay", "popin", "fail", "clap") and "clap" in ind.flags
        if s == "enter":
            fr.anim = ("enter", st.enter, el / ind.enter_dur())
        elif s == "hold":
            fr.anim = ("enter", st.enter, 1.0)
        elif s == "clap":
            fr.anim = ("enter", st.enter, 1.0)
            fr.clap_ms = el * 1000.0
        elif s == "stay":
            fr.anim = ("enter", st.enter, 1.0)
            fr.idle_t = el
        elif s == "leave":
            fr.anim = ("exit", st.exit, el / ind.exit_dur())
        elif s == "popin":
            fr.anim = ("enter", "pop", el / POP_IN)
        elif s == "fail":
            fr.anim = ("fail", "fail", el / FAIL_TIME)
        if s in CIRCLE_STATES or s == "popin":
            fr.circle_alpha = self._circle_alpha(ind, now)
            fr.circle_angle = draw.circle_angle(now - ind.born)
            if s == "cross":
                fr.circle_mix = draw.clamp01(el / CIRCLE_CROSS)
            elif s == "circle_in":
                fr.circle_mix = ind.mix
            elif s in ("overlay",):
                fr.circle_mix = 1.0
            else:
                fr.circle_mix = ind.mix
            if s == "circle_fail":
                fr.circle_dx, fr.circle_red, _ = draw.circle_fail(el / CIRCLE_FAIL)
            elif ind.pulse_t0 >= 0 and 0 <= now - ind.pulse_t0 < draw.PULSE_SECONDS:
                fr.circle_pulse = (now - ind.pulse_t0) / draw.PULSE_SECONDS
        return fr

    def frames(self, key: "tuple[str, str]", now: float) -> "list[Frame]":
        return [self.frame(i, now) for i in self.visible(key)]
