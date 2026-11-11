"""
The setup guide's checks: what a working afterglow setup needs, in the order
a new user sets it up, each looked at LIVE (is OBS reachable right now, is
the replay buffer on and long enough, is the hotkey daemon running...) and
reported with what to do when it isn't.

No Qt here (gui/setup_guide.py shows the result; `afterglow-cli` could
print it). run_checks() talks to OBS, so call it off the GUI thread.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

OK = "ok"          # set up and working
TODO = "todo"      # needs the user to do something
INFO = "info"      # nothing to check: how a part of the app is used


@dataclass
class Check:
    key: str
    title: str
    state: str
    summary: str                 # one line: what was found
    detail: str = ""             # what it is for / what to do (plain text, may have several lines)
    settings_tab: str = ""       # Settings tab that holds the relevant options ("" = none)
    page: str = ""               # "library" / "editor": a page to show instead


# ---------------------------------------------------------------- pieces

def daemon_running() -> bool:
    """The hotkey daemon is running: the systemd user service is active, or
    (started by hand) a process whose command line is afterglow-daemon."""
    if shutil.which("systemctl"):
        try:
            r = subprocess.run(["systemctl", "--user", "is-active", "--quiet", "afterglow-daemon"],
                               capture_output=True, timeout=3)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit() or int(pid) == os.getpid():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    cmd = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            if "afterglow-daemon" in cmd or "afterglow.daemon" in cmd:
                return True
    except OSError:
        pass
    return False


def input_devices_readable() -> "bool | None":
    """Whether this user can read the keyboard devices the hotkeys come
    from. None when there are none to look at (a container, a test)."""
    try:
        devices = [p for p in Path("/dev/input").iterdir() if p.name.startswith("event")]
    except OSError:
        return None
    if not devices:
        return None
    return any(os.access(p, os.R_OK) for p in devices)


def _fmt_len(seconds: int) -> str:
    seconds = int(seconds)
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


# ---------------------------------------------------------------- the checks

def check_obs(settings) -> "tuple[Check, dict]":
    """The OBS connection, plus what was learned over it for the next checks:
    {"connected", "buffer_seconds", "buffer_available", "buffer_active"}."""
    from .obs_client import OBSClient, OBSError
    facts = {"connected": False, "buffer_seconds": None, "buffer_available": None, "buffer_active": None}
    where = f"{settings.obs.host}:{settings.obs.port}"
    try:
        with OBSClient(settings.obs) as c:
            facts["connected"] = True
            facts["buffer_seconds"] = c.get_replay_buffer_max_seconds()
            try:
                status = c._require_client().get_replay_buffer_status()
                facts["buffer_available"] = True
                facts["buffer_active"] = bool(status.output_active)
            except Exception:  # noqa: BLE001 -- OBS answers with an error when the replay buffer is disabled
                facts["buffer_available"] = False
    except OBSError as e:
        return Check("obs", "Connect to OBS", TODO, f"OBS isn't reachable at {where}.",
                     "afterglow saves clips by asking OBS for its replay buffer, over OBS's WebSocket server.\n"
                     "1. Start OBS.\n"
                     "2. In OBS: Tools > WebSocket Server Settings > turn on \"Enable WebSocket server\".\n"
                     "3. Copy the port and (with authentication on) the password from there into "
                     "Settings > Clipping > OBS Connection, then press Test Connection.\n"
                     f"What OBS answered: {e}", settings_tab="Clipping"), facts
    except Exception as e:  # noqa: BLE001
        return Check("obs", "Connect to OBS", TODO, f"OBS isn't reachable at {where}.",
                     f"Unexpected error while connecting: {e}", settings_tab="Clipping"), facts
    return Check("obs", "Connect to OBS", OK, f"Connected to OBS at {where}.",
                 "afterglow saves clips by asking OBS for its replay buffer, over OBS's WebSocket server.",
                 settings_tab="Clipping"), facts


def check_replay_buffer(settings, facts: dict, clip_configs: list) -> Check:
    title = "Turn on OBS's replay buffer"
    what = ("The replay buffer is OBS continuously keeping the last minutes of video in memory; a clip is cut "
            "from it. Its length is the longest clip that can be saved.")
    if not facts.get("connected"):
        return Check("replay_buffer", title, TODO, "Checked once OBS is connected.", what)
    if facts.get("buffer_available") is False:
        return Check("replay_buffer", title, TODO, "The replay buffer is turned off in OBS.",
                     what + "\nIn OBS: Settings > Output > Replay Buffer > Enable Replay Buffer, set its Maximum "
                            "Replay Time, press OK. afterglow starts the buffer itself whenever a clip is asked for.")
    longest = max((c.length_seconds for c in clip_configs), default=0)
    have = facts.get("buffer_seconds")
    if have and longest > have:
        return Check("replay_buffer", title, TODO,
                     f"The replay buffer keeps {_fmt_len(have)}, but the longest clip option is {_fmt_len(longest)}.",
                     what + f"\nClips longer than {_fmt_len(have)} come out shorter than asked. In OBS: Settings > "
                            "Output > Replay Buffer > Maximum Replay Time: raise it to at least the longest clip "
                            "option (or shorten that clip option in Settings > Clipping).", settings_tab="Clipping")
    length = f" ({_fmt_len(have)} long)" if have else ""
    running = "" if facts.get("buffer_active") else " It isn't running right now; afterglow starts it on the first clip."
    return Check("replay_buffer", title, OK, f"The replay buffer is enabled{length}.{running}", what)


def check_keyframes(settings) -> Check:
    from . import obs_profile
    title = "OBS keyframe interval"
    what = ("A clip is trimmed to its exact length by re-encoding only the frames before the first keyframe. "
            "A keyframe every 1 s (OBS's default is 2 s or more) makes that about twice as fast, at the same file size.")
    info = obs_profile.read_keyframe_info()
    auto = bool(getattr(settings.obs, "auto_keyframe_interval", True))
    if not info.ok:
        return Check("keyframes", title, TODO if "Simple" in info.reason else INFO, info.reason, what,
                     settings_tab="Clipping")
    if info.keyint_sec == 1:
        return Check("keyframes", title, OK, f"OBS (profile “{info.profile}”) puts a keyframe every 1 s."
                     + (" Kept that way automatically." if auto else ""), what, settings_tab="Clipping")
    cur = f"{info.keyint_sec} s" if info.keyint_sec else "Auto"
    if auto:
        return Check("keyframes", title, TODO, f"OBS's keyframe interval is {cur}; it is set to 1 s automatically.",
                     what + "\nafterglow sets it the next time it starts or a clip is saved. OBS reads the setting "
                            "when it starts: restart OBS once afterwards.", settings_tab="Clipping")
    return Check("keyframes", title, TODO, f"OBS's keyframe interval is {cur}.",
                 what + "\nSettings > Clipping > OBS Connection: choose 1 s and press Apply to OBS (or turn on "
                        "\"Keep OBS's keyframe interval at 1 s automatically\"), then restart OBS.",
                 settings_tab="Clipping")


def check_daemon() -> Check:
    title = "Hotkey daemon"
    what = ("The daemon is afterglow's background service: it listens for the clip hotkeys and saves the clips, "
            "whether or not this window is open.")
    readable = input_devices_readable()
    if readable is False:
        return Check("daemon", title, TODO, "This user can't read the keyboard devices (/dev/input).",
                     what + "\nHotkeys are read from /dev/input, which needs the \"input\" group. With the NixOS "
                            "module: set services.afterglow.user to this user, rebuild, then log out and back in "
                            "(group membership only applies to a new login). Elsewhere: add the user to the "
                            "\"input\" group and log in again.")
    if daemon_running():
        return Check("daemon", title, OK, "The daemon is running.", what)
    return Check("daemon", title, TODO, "The daemon isn't running.",
                 what + "\nStart it: systemctl --user start afterglow-daemon\n"
                        "With the NixOS module (services.afterglow.enable = true) it starts with the session. "
                        "To see why it stopped: journalctl --user -u afterglow-daemon -e")


def check_clip_options(clip_configs: list) -> Check:
    title = "Clip options and hotkeys"
    what = ("A clip option is one kind of clip: its length, its hotkey and its sound. Pressing the hotkey saves the "
            "last that-many seconds. `afterglow-cli clip <name>` does the same from a script or a Stream Deck.")
    with_key = [c for c in clip_configs if c.hotkey]
    if not clip_configs:
        return Check("clips", title, TODO, "There are no clip options yet.",
                     what + "\nSettings > Clipping > Clip Options: add one, give it a length and record a hotkey.",
                     settings_tab="Clipping")
    listing = "; ".join(f"{c.name}: {_fmt_len(c.length_seconds)}" + (f" on {c.hotkey}" if c.hotkey else " (no hotkey)")
                        for c in clip_configs)
    if not with_key:
        return Check("clips", title, TODO, f"No clip option has a hotkey yet ({listing}).",
                     what + "\nSettings > Clipping > Clip Options: press Record... next to a clip option and "
                            "press the keys to use.", settings_tab="Clipping")
    return Check("clips", title, OK, listing + ".", what, settings_tab="Clipping")


def check_clips_folder(settings) -> Check:
    title = "Clips folder"
    path = settings.clips_path()
    what = "Where finished clips are kept. Video files dropped into it by hand show up in the Library too."
    try:
        path.mkdir(parents=True, exist_ok=True)
        writable = os.access(path, os.W_OK)
    except OSError:
        writable = False
    if not writable:
        return Check("folder", title, TODO, f"{path} can't be written to.",
                     what + "\nChoose another folder in Settings > Clipping, or fix the folder's permissions.",
                     settings_tab="Clipping")
    return Check("folder", title, OK, f"Clips are saved to {path}.", what, settings_tab="Clipping")


def info_steps(settings) -> list:
    """How the rest of the app is used (nothing to check)."""
    from . import overlay_support
    steps = [
        Check("filters", "Filters", INFO, "Labels for clips: by game, by kind of moment, anything.",
              "Add filters to a clip from its Filters button; filter or block by them from the Library's Sort menu; "
              "star a clip to make it a favorite.\nSettings > Filters: give a filter an icon and colour, and set up "
              "Auto Add Filter rules that tag new clips by the application that is open or focused when they are "
              "saved. `afterglow-cli clip <name> --filter=(\"a\" \"b\")` tags a clip as it is saved.",
              settings_tab="Filters"),
        Check("library", "Library and previewer", INFO, "Every clip, newest first. Click one to play it.",
              "The previewer plays the clip and is where it is trimmed: drag the handles on the timeline and save. "
              "The original is kept as a backup the first time, and Undo brings it back.\nRight-click a clip (or "
              "several: ctrl / shift click) to favorite, copy, upload or delete. The Sort menu searches, sorts, "
              "filters and chooses what each card shows.", page="library"),
        Check("editor", "Advanced Editor", INFO, "A multi-track editor for more than a trim.",
              "Open a clip in it from the previewer or a card's Edit button. Cut and rearrange clips on tracks, add "
              "text, images and sounds, transitions, zooms and fades, then export. Unchanged stretches are copied "
              "rather than re-encoded, so exporting a lightly edited clip is fast.", page="editor"),
    ]
    try:
        have_puppetry = bool(overlay_support.available()[0])
    except Exception:  # noqa: BLE001
        have_puppetry = False
    steps.append(Check(
        "overlay", "Input overlay (optional)", INFO,
        "Puppetry is installed: clips can record the keys and mouse buttons pressed." if have_puppetry
        else "Needs Puppetry (not found): shows the keys and mouse buttons pressed during a clip.",
        "Turn it on per clip option (Settings > Clipping > Clip Options > Input Overlay). The overlay is kept next "
        "to the clip, never burned into it: it can be shown, hidden, moved and resized later.\nSettings > Input "
        "Overlay: default positions, and the overlay's frame rate (lower renders faster).",
        settings_tab="Input Overlay"))
    steps.append(Check(
        "youtube", "YouTube (optional)", INFO, "Upload clips to YouTube from the Library.",
        "Settings > YouTube: sign in and set the upload defaults (privacy, description template). Upload from a "
        "clip's right-click menu; uploaded clips are listed on the Library's Uploaded tab.", settings_tab="YouTube"))
    return steps


def run_checks(settings=None) -> list:
    """Every step of the guide, in order. Talks to OBS (blocks for up to a
    few seconds when OBS isn't there)."""
    from . import config
    from .clips import list_clip_configs
    settings = settings or config.load()
    try:
        clip_configs = list_clip_configs()
    except Exception:  # noqa: BLE001
        clip_configs = []
    obs_check, facts = check_obs(settings)
    checks = [obs_check]
    for fn in (lambda: check_replay_buffer(settings, facts, clip_configs),
               lambda: check_keyframes(settings),
               check_daemon,
               lambda: check_clip_options(clip_configs),
               lambda: check_clips_folder(settings)):
        try:
            checks.append(fn())
        except Exception as e:  # noqa: BLE001 -- one broken check must not hide the others
            checks.append(Check("error", "A check failed", INFO, str(e)))
    try:
        checks.extend(info_steps(settings))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("error", "A check failed", INFO, str(e)))
    return checks


def summary(checks: list) -> "tuple[int, int]":
    """(steps that are set up, steps that are checked at all)."""
    checked = [c for c in checks if c.state in (OK, TODO)]
    return sum(1 for c in checked if c.state == OK), len(checked)
