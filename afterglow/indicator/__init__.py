"""
Clip indicator ("the clapper"): a small overlay that slides onto the screen when a
clip hotkey fires, claps when OBS confirms the save, then shows processing until the
clip (and its input overlay) are done.  See HANDOFF.md, "Clip indicator".

Layout of this package
  draw.py        pure QPainter drawing + every enter/exit/fail animation (no windows)
  layout.py      pure geometry: surface size, stack slots, per-anchor space
  model.py       pure state machine per capture + stacking, driven by a clock
  focus.py       which screen holds the focused window (kdotool / hyprctl / swaymsg)
  layershell.py  ctypes binding for the LayerShellQt shim (native/)
  paint.py       paints a stack of captures (shared by the overlay and the Settings preview)
  surface.py     the Qt overlay window
  __main__.py    the helper process (python -m afterglow.indicator)

Nothing here imports Qt at package import time except the modules that draw, so
the daemon (headless) can import `afterglow.indicator` for the constants only.
"""
from __future__ import annotations

# --- protocol (JSON lines over the helper's socket) -------------------------------
EVENTS = ("start", "clap", "processing", "done", "overlay", "overlay_done", "overlay_fail", "fail")

# --- positions ------------------------------------------------------------------
ANCHORS = ("top_left", "top", "top_right", "left", "right", "bottom_left", "bottom", "bottom_right", "center")
DEFAULT_ANCHOR = "bottom_right"

# --- animations (enter and exit are chosen separately; fail is fixed) -----------------
ENTER_ANIMATIONS = ("slide", "drop", "pop", "swing", "spin", "toss", "flip", "peek", "fade")
EXIT_ANIMATIONS = ("slide", "zip", "fall", "shrink", "spin", "toss", "flip", "fade", "bow")
ANIMATION_LABELS = {
    "slide": "Slide", "drop": "Drop", "pop": "Pop", "swing": "Swing", "spin": "Spin", "toss": "Toss",
    "flip": "Flip", "peek": "Peek", "fade": "Fade", "zip": "Zip", "fall": "Fall", "shrink": "Shrink",
    "bow": "Bow",
}

STYLES = ("clapper", "hands")
HANDS_FRONT = ("right", "left")      # which glove ends up in front in the clasp (Hands style)
HANDS_LOOKS = ("retro", "cel")       # how the Hands are drawn: retro (cream, black ink, red cuffs) / cel-shaded
DEFAULT_HANDS_LOOK = "retro"
# The Hands clap: a wind-up (the hands reel back), then a fast swing in; the hands meet this long
# after the `clap` event, so the clap sound is delayed by the same amount to land on the impact.
# These match the traced frames (indicator/resources/hands_frames.json.gz, "timing").
HANDS_WINDUP_MS = 320.0
HANDS_SWING_MS = 90.0
HANDS_CONTACT_MS = HANDS_WINDUP_MS + HANDS_SWING_MS
PROCESSING_MODES = ("circle", "stay")
SCREEN_MODES = ("focused", "primary")

# Rapid repeats stack: one slot per capture, this many visible, the rest in a "+N" badge.
MAX_VISIBLE = 5
STACK_GAP = 12          # px between stacked indicators

# Watchdogs (seconds).  The daemon's own capture timeout sends `fail`; the overlay
# render gets OVERLAY_TIMEOUT before its thread reports `overlay_fail`; the helper drops
# any indicator that hears nothing for WATCHDOG so nothing can hang on screen.
OVERLAY_TIMEOUT = 600.0
WATCHDOG = OVERLAY_TIMEOUT + 120.0
