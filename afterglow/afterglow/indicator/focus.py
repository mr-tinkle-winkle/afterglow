"""
Which screen holds the focused window -- the screen the indicator should appear on.

Wayland clients can't read the cursor position or the focused window, so this reuses the
compositor-specific routes `autofilter.py` already uses for Auto Add Filter:

  KDE Plasma  `kdotool getactivewindow getwindowgeometry`
  Hyprland    `hyprctl activewindow -j`
  Sway        `swaymsg -t get_tree` (the focused node's rect)

The result is a *hint* -- the centre of the focused window in the compositor's global
logical coordinates -- sent with `start`; the helper matches it to a QScreen by geometry
(`choose_screen`, pure, below).  No hint (no backend, nothing focused) = the primary screen.

The kdotool output format (``Position: x,y`` / ``Geometry: WxH``) follows its README and is
NOT yet verified against a live KWin session -- same status as autofilter's kdotool path.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess

logger = logging.getLogger("afterglow.indicator.focus")


def parse_kdotool_geometry(text: str) -> "tuple[float, float, float, float] | None":
    """`kdotool getwindowgeometry` -> (x, y, w, h)."""
    pos = re.search(r"Position:\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", text or "")
    geo = re.search(r"Geometry:\s*(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)", text or "")
    if not pos or not geo:
        return None
    return float(pos.group(1)), float(pos.group(2)), float(geo.group(1)), float(geo.group(2))


def parse_hyprctl_activewindow(text: str) -> "tuple[float, float, float, float] | None":
    try:
        d = json.loads(text)
        (x, y), (w, h) = d["at"], d["size"]
        return float(x), float(y), float(w), float(h)
    except (ValueError, KeyError, TypeError):
        return None


def _find_focused(node: dict) -> "dict | None":
    if node.get("focused"):
        return node
    for child in (node.get("nodes") or []) + (node.get("floating_nodes") or []):
        hit = _find_focused(child)
        if hit is not None:
            return hit
    return None


def parse_sway_tree(text: str) -> "tuple[float, float, float, float] | None":
    try:
        node = _find_focused(json.loads(text))
        if node is None:
            return None
        r = node["rect"]
        return float(r["x"]), float(r["y"]), float(r["width"]), float(r["height"])
    except (ValueError, KeyError, TypeError):
        return None


def _run(cmd: "list[str]") -> "str | None":
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=1.5, check=True)
        return out.stdout
    except (subprocess.SubprocessError, OSError):
        return None


def focused_window_center() -> "dict | None":
    """{"x": .., "y": ..}: the centre of the focused window, or None if unknown."""
    geo = None
    if shutil.which("kdotool"):
        out = _run(["kdotool", "getactivewindow", "getwindowgeometry"])
        geo = parse_kdotool_geometry(out) if out else None
    if geo is None and shutil.which("hyprctl"):
        out = _run(["hyprctl", "activewindow", "-j"])
        geo = parse_hyprctl_activewindow(out) if out else None
    if geo is None and shutil.which("swaymsg"):
        out = _run(["swaymsg", "-t", "get_tree"])
        geo = parse_sway_tree(out) if out else None
    if geo is None:
        return None
    x, y, w, h = geo
    return {"x": x + w / 2.0, "y": y + h / 2.0}


def choose_screen(screens: "list[tuple[str, tuple[float, float, float, float]]]", hint: "dict | None",
                  primary: str = "", mode: str = "focused") -> str:
    """Pick a screen name.  ``screens``: [(name, (x, y, w, h))] in the compositor's logical space.
    mode "primary", or no hint -> the primary screen; otherwise the screen containing the
    hint point (the nearest one if it falls in a gap between screens)."""
    if not screens:
        return ""
    names = [n for n, _ in screens]
    fallback = primary if primary in names else names[0]
    if mode != "focused" or not hint or "x" not in hint or "y" not in hint:
        return fallback
    px, py = float(hint["x"]), float(hint["y"])
    best, best_d = fallback, None
    for name, (x, y, w, h) in screens:
        if x <= px < x + w and y <= py < y + h:
            return name
        dx = max(x - px, 0.0, px - (x + w))
        dy = max(y - py, 0.0, py - (y + h))
        d = dx * dx + dy * dy
        if best_d is None or d < best_d:
            best, best_d = name, d
    return best
