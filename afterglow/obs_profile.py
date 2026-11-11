"""
OBS's keyframe interval, read and written straight from OBS's own profile
files.

Why files and not obs-websocket: the websocket API (v5) has no request for
encoder settings -- SetProfileParameter only reaches basic.ini, and the
encoder's settings (keyint_sec among them) live in a JSON file next to it:

  <obs config>/basic/profiles/<profile>/streamEncoder.json   (Advanced output,
                                                              recording "uses the stream encoder")
  <obs config>/basic/profiles/<profile>/recordEncoder.json   (Advanced output,
                                                              own recording encoder)

The replay buffer uses the recording encoder, so that's the file that
matters. Simple output mode has no keyframe setting at all (OBS fixes it),
so there's nothing to change there.

Why it matters for afterglow: a frame-exact trim re-encodes only the frames
from the cut point up to the next keyframe (smartcut.py). Keyframes every
1 s instead of every 2 s halve that, on average. With CBR (what the replay
buffer uses) the file size is unchanged -- the bitrate is fixed -- and at
replay-buffer bitrates the extra keyframes cost no visible quality.

OBS reads the file when it starts; writing it while OBS is running is safe
(OBS only writes this file when you press OK/Apply in its own Settings) but
only takes effect after OBS restarts.
"""
from __future__ import annotations

import configparser
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

CONFIG_ROOTS = (
    Path.home() / ".var/app/com.obsproject.Studio/config/obs-studio",   # Flatpak
    Path.home() / ".config/obs-studio",                                   # native
)


@dataclass
class KeyframeInfo:
    """Where OBS's replay-buffer encoder settings are and what they say."""
    ok: bool                         # False: can't be changed (reason says why)
    reason: str = ""
    profile: str = ""
    encoder_file: "Path | None" = None
    keyint_sec: int = 0              # 0 = OBS's "auto"
    encoder: str = ""


def _ini(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read_string(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, configparser.Error):
        pass
    return cp


def _root() -> "Path | None":
    found = [r for r in CONFIG_ROOTS if (r / "basic/profiles").is_dir()]
    if not found:
        return None
    # both installed: the one used most recently
    return max(found, key=lambda r: (r / "basic/profiles").stat().st_mtime)


def _profile_dir(root: Path) -> "Path | None":
    profiles = root / "basic/profiles"
    for name in ("user.ini", "global.ini"):          # OBS 31+ keeps it in user.ini
        cp = _ini(root / name)
        d = cp.get("Basic", "ProfileDir", fallback="")
        if d and (profiles / d).is_dir():
            return profiles / d
    dirs = [p for p in profiles.iterdir() if p.is_dir()]
    return dirs[0] if len(dirs) == 1 else None


def read_keyframe_info() -> KeyframeInfo:
    root = _root()
    if root is None:
        return KeyframeInfo(False, "OBS's settings folder wasn't found.")
    prof = _profile_dir(root)
    if prof is None:
        return KeyframeInfo(False, "Couldn't tell which OBS profile is in use.")
    basic = _ini(prof / "basic.ini")
    name = basic.get("General", "Name", fallback=prof.name)
    mode = basic.get("Output", "Mode", fallback="Simple")
    if mode != "Advanced":
        return KeyframeInfo(False, "OBS is in Simple output mode, which has no keyframe setting. "
                                   "Switch OBS to Settings > Output > Output Mode: Advanced to change it.",
                            profile=name)
    rec_enc = basic.get("AdvOut", "RecEncoder", fallback="none")
    if rec_enc in ("", "none"):
        f = prof / "streamEncoder.json"
        enc = basic.get("AdvOut", "Encoder", fallback="")
    else:
        f = prof / "recordEncoder.json"
        enc = rec_enc
    data = {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        return KeyframeInfo(False, f"Couldn't read {f.name}: {e}", profile=name)
    try:
        keyint = int(data.get("keyint_sec", 0) or 0)
    except (TypeError, ValueError):
        keyint = 0
    return KeyframeInfo(True, profile=name, encoder_file=f, keyint_sec=keyint, encoder=enc)


def obs_running() -> bool:
    """An OBS process is running (Flatpak or native: the process is named "obs")."""
    try:
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
                        if fh.read().strip() == "obs":
                            return True
                except OSError:
                    continue
    except OSError:
        pass
    return False


def set_keyint_sec(seconds: int) -> KeyframeInfo:
    """Write keyint_sec into the replay buffer's encoder settings (keeping a
    one-time backup of the original next to it). Returns the new info;
    raises RuntimeError when it can't."""
    info = read_keyframe_info()
    if not info.ok:
        raise RuntimeError(info.reason)
    f = info.encoder_file
    data = {}
    if f.exists():
        data = json.loads(f.read_text(encoding="utf-8"))
        backup = f.with_name(f.name + ".afterglow-backup")
        if not backup.exists():
            shutil.copy2(f, backup)
    data["keyint_sec"] = int(seconds)
    tmp = f.with_name(f.name + ".afterglow-tmp")
    tmp.write_text(json.dumps(data, indent=4), encoding="utf-8")
    os.replace(tmp, f)
    return read_keyframe_info()


# ---------------------------------------------------------------- automatic

AUTO_KEYINT_SEC = 1


def notify(text: str) -> None:
    """One desktop notification (notify-send), printed as well. Used from the
    daemon and the CLI, which have no window to show it in."""
    print(text)
    import subprocess
    if shutil.which("notify-send"):
        try:
            subprocess.Popen(["notify-send", "-a", "afterglow", "-i", "afterglow", "afterglow", text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass


def auto_apply(settings=None, on_changed=None) -> "str | None":
    """Set OBS's keyframe interval to 1 s if the "automatic" setting is on
    and it is anything else (OBS's own 0 = "auto" included, which resolves to
    a longer interval). Called when the GUI and the daemon start and after
    each capture, so a new or switched OBS profile is picked up too.

    Returns the message to show the user when something was changed (and
    passes it to `on_changed`, default: a desktop notification) -- which only
    happens once per profile, since afterwards the value already is 1 -- else
    None. Never raises: Simple output mode, no OBS, an unreadable profile all
    just mean "nothing to do"."""
    try:
        if settings is None:
            from . import config
            settings = config.load()
        if not getattr(settings.obs, "auto_keyframe_interval", True):
            return None
        info = read_keyframe_info()
        if not info.ok or info.keyint_sec == AUTO_KEYINT_SEC:
            return None
        before = info.keyint_sec
        set_keyint_sec(AUTO_KEYINT_SEC)
        msg = (f"OBS's keyframe interval (profile “{info.profile}”) was set to {AUTO_KEYINT_SEC} s "
               f"(it was {'Auto' if not before else f'{before} s'}) so clips trim faster. "
               + ("Restart OBS for it to take effect." if obs_running() else "It applies the next time OBS starts.")
               + " Turn this off in Settings > OBS Connection.")
        (on_changed or notify)(msg)
        return msg
    except Exception as e:  # noqa: BLE001 -- never in the way of a capture or of starting up
        print(f"Automatic keyframe interval: skipped ({e})")
        return None
