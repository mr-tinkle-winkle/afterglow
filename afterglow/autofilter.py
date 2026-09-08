"""
Auto Add Filter detection: figures out which configured auto-filter
rules currently match, so newly captured clips can be auto-tagged
based on which application was open or focused at capture time.

Two independent modes per rule (multiple rules, in any mix of modes,
can be active at once -- they aren't mutually exclusive):
  - "open": matches if a running process's name or full command line
    contains the rule's app_match text. Compositor-agnostic -- plain
    /proc scanning, no extra dependency.
  - "focused": matches if the currently-focused window's title OR its
    window class/app-id contains the rule's app_match text. This is
    KDE Plasma on Wayland, which (like every Wayland compositor) has no
    X11-style global "get the focused window" API -- detection goes
    through `kdotool` (https://github.com/jinliu/kdotool, packaged in
    nixpkgs as `kdotool`), which drives KWin's own scripting/DBus
    interface to answer exactly this. Hyprland (`hyprctl activewindow
    -j`) and Sway (`swaymsg -t get_tree`) backends are also included as
    a fallback for a different compositor, tried in that order after
    kdotool; if none of the three is present, "focused" rules log one
    warning and never match.

A rule's app_match is checked against BOTH the relevant fields for its
mode (process name AND full cmdline for "open"; window title AND
window class for "focused") -- a hit on either counts as a match.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from . import config as config_module

logger = logging.getLogger("clipping-daemon.autofilter")

_warned_no_focused_backend = False


def _running_process_names() -> set[str]:
    """Lowercase set of every running process's comm name and full
    cmdline, scanned directly from /proc."""
    names: set[str] = set()
    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return names
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
            if comm:
                names.add(comm.lower())
            cmdline = (entry / "cmdline").read_bytes().decode("utf-8", "ignore")
            if cmdline:
                names.add(cmdline.replace("\x00", " ").strip().lower())
        except (OSError, PermissionError):
            continue  # process exited mid-scan, or not readable -- skip it
    return names


def list_running_process_display_names() -> list[str]:
    """Sorted, deduped list of just the short comm name (e.g. "steam",
    "firefox") of every running process -- for the Settings > Auto Add
    Filter app-name dropdown, which needs something a person can
    actually scan/pick from, not the full /proc dump _running_process_names()
    returns (that includes full command lines, used for actual matching,
    not for display)."""
    proc_dir = Path("/proc")
    names: set[str] = set()
    if not proc_dir.is_dir():
        return []
    for entry in proc_dir.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
            if comm:
                names.add(comm)
        except (OSError, PermissionError):
            continue
    return sorted(names, key=str.lower)


def _find_focused_sway_node(node: dict) -> dict | None:
    if node.get("focused"):
        return node
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        found = _find_focused_sway_node(child)
        if found is not None:
            return found
    return None


def _focused_window_info() -> tuple[str, str] | None:
    """(window_title, window_class) of the currently focused window, or
    None if no supported compositor backend is available. Tries
    kdotool (KDE Plasma / KWin) first, then Hyprland, then Sway as
    fallbacks for a different compositor."""
    global _warned_no_focused_backend

    if shutil.which("kdotool"):
        try:
            title = subprocess.run(
                ["kdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout.strip()
            window_class = subprocess.run(
                ["kdotool", "getactivewindow", "getwindowclassname"],
                capture_output=True, text=True, timeout=2, check=True,
            ).stdout.strip()
            return title, window_class
        except (subprocess.SubprocessError, OSError):
            pass

    if shutil.which("hyprctl"):
        try:
            out = subprocess.run(
                ["hyprctl", "activewindow", "-j"],
                capture_output=True, text=True, timeout=2, check=True,
            )
            data = json.loads(out.stdout)
            return data.get("title", ""), data.get("class", "")
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            pass

    if shutil.which("swaymsg"):
        try:
            out = subprocess.run(
                ["swaymsg", "-t", "get_tree"],
                capture_output=True, text=True, timeout=2, check=True,
            )
            tree = json.loads(out.stdout)
            focused = _find_focused_sway_node(tree)
            if focused is not None:
                return focused.get("name", "") or "", focused.get("app_id", "") or ""
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            pass

    if not _warned_no_focused_backend:
        logger.warning(
            "No supported compositor backend found for 'focused' Auto Add Filter "
            "rules (tried kdotool, hyprctl, swaymsg) -- 'focused' rules will never "
            "match until one of these is installed (kdotool is the one that "
            "applies to KDE Plasma; it's packaged in nixpkgs as `kdotool`)."
        )
        _warned_no_focused_backend = True
    return None


def _matches(app_match: str, haystacks: list[str]) -> bool:
    needle = app_match.strip().lower()
    if not needle:
        return False
    return any(needle in h.lower() for h in haystacks if h)


def compute_active_auto_tags(settings: "config_module.AppSettings | None" = None) -> list[str]:
    """Every tag name whose Auto Add Filter rule currently matches --
    called at clip-capture time (see clips.trigger_clip) to auto-apply
    filters to a newly captured clip. Multiple rules can match; all
    matching tag names are returned."""
    settings = settings or config_module.load()
    if not settings.auto_filters:
        return []

    open_names: set[str] | None = None
    focused_info: tuple[str, str] | None = None
    matched_tags: list[str] = []

    for rule in settings.auto_filters:
        if not rule.tag_name or not rule.app_match:
            continue
        if rule.mode == "focused":
            if focused_info is None:
                focused_info = _focused_window_info() or ("", "")
            if _matches(rule.app_match, list(focused_info)):
                matched_tags.append(rule.tag_name)
        else:  # "open"
            if open_names is None:
                open_names = _running_process_names()
            if _matches(rule.app_match, list(open_names)):
                matched_tags.append(rule.tag_name)

    return matched_tags
