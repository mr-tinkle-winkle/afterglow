"""
Fire-and-forget client for the clip indicator helper (``python -m afterglow.indicator``).

The capture pipeline (``clips.trigger_clip``, ``clips._OverlayCapture``, the daemon) calls
``begin()`` / ``emit()``; those NEVER raise and NEVER block the capture -- same rule as
``clips.play_sound``.  Everything that can be slow (reading the config / DB, asking the
compositor which window is focused, connecting, spawning the helper) happens on one worker
thread, in order, so events reach the helper in the order they were emitted.

    cid = begin(clip_config_id)            # right at the hotkey: the clapper slides on
    emit(cid, "clap")                      # OBS confirmed the save
    emit(cid, "done")                      # clip is in the library  (or "overlay", see clips.py)
    emit(cid, "fail")                      # any exception in the pipeline

The helper owns the screen; it is started with the daemon (``ensure_started(daemon=True)``) so
the first clap is instant, and respawned on demand if it died.  It needs a display
(WAYLAND_DISPLAY / DISPLAY); a daemon started before the session won't have them, so they are
read from ``systemctl --user show-environment`` when missing.
"""
from __future__ import annotations

import atexit
import itertools
import json
import logging
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config as config_module
from .indicator import EVENTS, OVERLAY_TIMEOUT

logger = logging.getLogger("afterglow.indicator_client")

CONNECT_TIMEOUT = 0.25            # s, one connect attempt
SPAWN_WAIT = 4.0                  # s, how long to keep retrying after spawning the helper
SPAWN_COOLDOWN = 5.0              # s, at most one spawn per this
NODISPLAY_COOLDOWN = 30.0         # s, don't re-probe for a display more often than this
WARN_COOLDOWN = 60.0
ON_DEMAND_IDLE_EXIT = 900         # s, a helper started on demand (CLI trigger) quits when idle

_counter = itertools.count(1)
_queue: "queue.Queue | None" = None
_worker: "threading.Thread | None" = None
_lock = threading.Lock()
_last_spawn = -1e9
_last_nodisplay = -1e9
_last_warn = -1e9
_test_sink = None                 # tests: a callable(dict) that receives every message instead of a socket


# ------------------------------------------------------------------ pure helpers

def socket_path() -> str:
    env = os.environ.get("AFTERGLOW_INDICATOR_SOCKET")
    if env:
        return env
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/afterglow-{os.getuid()}"
    return str(Path(base) / "afterglow-indicator.sock")


def new_id() -> str:
    """A capture id unique across the daemon, CLI and GUI processes."""
    return f"{os.getpid()}-{next(_counter)}-{time.time_ns() % 1_000_000}"


def enabled(settings=None) -> bool:
    try:
        s = settings or config_module.load_readonly()
        return bool(s.clip_indicator.enabled)
    except Exception:  # noqa: BLE001 -- never break a capture over a config problem
        return False


def build_style(settings, clip_cfg=None, screen_hint: "dict | None" = None) -> dict:
    """The ``style`` payload of a ``start`` event: global settings + this clip type's colours / icon."""
    ci = settings.clip_indicator
    # the afterglow theme's colours, with this clip type's own choices on top
    try:
        from .indicator import draw          # lazily: draw pulls in QtGui, which the headless daemon should not load up front
        colors = draw.afterglow_defaults(getattr(settings, "appearance", None))
    except Exception:  # noqa: BLE001 -- the helper falls back to its own afterglow defaults
        colors = {}
    colors.update({k: v for k, v in (getattr(clip_cfg, "indicator_colors", None) or {}).items() if v})
    return {
        "style": ci.style,
        "colors": colors,
        "icon": getattr(clip_cfg, "indicator_icon_path", "") or "",
        "anchor": ci.anchor,
        "padding_x": ci.padding_x,
        "padding_y": ci.padding_y,
        "size": ci.size,
        "enter": ci.enter_animation,
        "exit": ci.exit_animation,
        "mode": ci.processing,
        "circle_color": ci.circle_color,
        "overlay_circle_color": ci.overlay_circle_color,
        "circle_opacity": ci.circle_opacity,
        "opacity": ci.clapper_opacity,
        "pulse": ci.ring_pulse,
        "screen": ci.screen,
        "screen_hint": screen_hint if ci.screen == "focused" else None,
    }


def display_environment() -> "dict[str, str]":
    """The environment variables the helper needs to reach the display.  Taken from this process;
    anything missing is read from the systemd user manager (Plasma imports WAYLAND_DISPLAY /
    DISPLAY there; a daemon started before the session won't have inherited them)."""
    keys = ("WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR", "XAUTHORITY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
            "DBUS_SESSION_BUS_ADDRESS")
    env = {k: os.environ[k] for k in keys if os.environ.get(k)}
    if not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")) and shutil.which("systemctl"):
        try:
            out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True,
                                 timeout=2).stdout
            for line in out.splitlines():
                k, _, v = line.partition("=")
                if k in keys and v and k not in env:
                    env[k] = v
        except (subprocess.SubprocessError, OSError):
            pass
    if "XDG_RUNTIME_DIR" not in env and Path(f"/run/user/{os.getuid()}").is_dir():
        env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
    return env


def helper_command() -> "list[str]":
    """How to launch the helper: the wrapped ``afterglow-indicator`` script next to the running
    one (the Nix wrapper sets up Qt's plugin paths and the layer-shell library), else on PATH,
    else ``python -m afterglow.indicator``."""
    sibling = Path(sys.argv[0]).resolve().parent / "afterglow-indicator" if sys.argv and sys.argv[0] else None
    if sibling and sibling.is_file() and os.access(sibling, os.X_OK):
        return [str(sibling)]
    which = shutil.which("afterglow-indicator")
    if which:
        return [which]
    return [sys.executable, "-m", "afterglow.indicator"]


# ------------------------------------------------------------------ transport (worker thread)

def _warn(msg: str) -> None:
    global _last_warn
    now = time.monotonic()
    if now - _last_warn > WARN_COOLDOWN:
        _last_warn = now
        logger.warning(msg)
        print(f"Warning: clip indicator: {msg}")


def _send_once(data: bytes) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(CONNECT_TIMEOUT)
        s.connect(socket_path())
        s.sendall(data)
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def spawn_helper(daemon: bool = False) -> bool:
    """Start the helper detached from this process.  False if it can't be (no display, cooldown)."""
    global _last_spawn, _last_nodisplay
    now = time.monotonic()
    if now - _last_spawn < SPAWN_COOLDOWN or now - _last_nodisplay < NODISPLAY_COOLDOWN:
        return False
    if os.environ.get("QT_QPA_PLATFORM") in ("offscreen", "minimal") and not os.environ.get("AFTERGLOW_INDICATOR_ALLOW_OFFSCREEN"):
        return False        # test runs (offscreen Qt) must never leave helper processes behind
    env = dict(os.environ)
    env.update(display_environment())
    if not (env.get("WAYLAND_DISPLAY") or env.get("DISPLAY")) and env.get("QT_QPA_PLATFORM") not in ("offscreen", "minimal"):
        _last_nodisplay = now
        _warn("no display found (WAYLAND_DISPLAY / DISPLAY) -- the indicator can't be shown from this session.")
        return False
    _last_spawn = now
    cmd = helper_command() + ["--socket", socket_path(), "--idle-exit", "0" if daemon else str(ON_DEMAND_IDLE_EXIT)]
    # `python -m afterglow.indicator` must find this package wherever it is installed
    pkg_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        from .config import CONFIG_DIR
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        log = CONFIG_DIR / "indicator.log"
        if log.exists() and log.stat().st_size > 1_000_000:
            log.write_text("")
        logf = open(log, "ab")
    except OSError:
        logf = subprocess.DEVNULL
    try:
        subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf, start_new_session=True)
        return True
    except OSError as e:
        _warn(f"couldn't start the helper ({cmd[0]}): {e}")
        return False


def _deliver(msg: dict) -> bool:
    if _test_sink is not None:
        _test_sink(msg)
        return True
    data = (json.dumps(msg) + "\n").encode()
    if _send_once(data):
        return True
    spawned = spawn_helper()
    if not (spawned or time.monotonic() - _last_spawn < SPAWN_WAIT):
        return False                                    # no display / nothing to wait for: drop quickly
    deadline = time.monotonic() + SPAWN_WAIT
    while time.monotonic() < deadline:
        time.sleep(0.05)
        if _send_once(data):
            return True
    return False


def _resolve_start(cid: str, clip_config_id) -> "dict | None":
    """The full ``start`` message (worker thread): settings + clip type + the focused window's centre."""
    settings = config_module.load()
    if not settings.clip_indicator.enabled:
        return None
    clip_cfg = None
    if clip_config_id is not None:
        try:
            from . import clips
            clip_cfg = clips.get_clip_config(clip_config_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("indicator: no clip config %s: %s", clip_config_id, e)
    hint = None
    if settings.clip_indicator.screen == "focused":
        from .indicator.focus import focused_window_center
        hint = focused_window_center()
    return {"id": cid, "event": "start", "style": build_style(settings, clip_cfg, hint)}


def _run() -> None:
    assert _queue is not None
    while True:
        item = _queue.get()
        try:
            kind = item[0]
            if kind == "stop":
                return
            if kind == "start":
                _, cid, clip_config_id = item
                msg = _resolve_start(cid, clip_config_id)
            elif kind == "raw":
                msg = item[1]
            else:
                msg = None
            if msg is not None and not _deliver(msg):
                _warn(f"the helper isn't reachable at {socket_path()} -- dropped '{msg.get('event')}'.")
        except Exception as e:  # noqa: BLE001 -- never let the worker die
            logger.debug("indicator worker: %s", e)
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _queue, _worker
    with _lock:
        if _worker is None or not _worker.is_alive():
            _queue = queue.Queue(maxsize=256)
            _worker = threading.Thread(target=_run, name="afterglow-indicator-client", daemon=True)
            _worker.start()


def _put(item) -> None:
    try:
        _ensure_worker()
        _queue.put_nowait(item)
    except Exception:  # noqa: BLE001 -- full queue etc.: drop, never block
        pass


# ------------------------------------------------------------------ public API

def begin(clip_config_id=None, settings=None) -> "str | None":
    """Right at the hotkey: allocate a capture id and queue the ``start`` event (the clapper
    slides on).  Returns None when the indicator is off -- every later ``emit(None, ...)`` is a
    no-op, so callers never need to check."""
    try:
        if not enabled(settings):
            return None
        cid = new_id()
        _put(("start", cid, clip_config_id))
        return cid
    except Exception:  # noqa: BLE001
        return None


def emit(cid: "str | None", event: str) -> None:
    """Send ``clap`` / ``done`` / ``overlay`` / ``overlay_done`` / ``overlay_fail`` / ``fail`` for a
    capture.  Never raises, never blocks."""
    if not cid or event not in EVENTS or event == "start":
        return
    _put(("raw", {"id": cid, "event": event}))


def arm_overlay_timeout(cid: "str | None", seconds: float = OVERLAY_TIMEOUT):
    """The overlay render's own timeout: if it hasn't finished in ``seconds`` the purple circle gets
    ``overlay_fail``.  Returns a cancel() callable (a no-op if there is nothing to time)."""
    if not cid:
        return lambda: None
    t = threading.Timer(seconds, lambda: emit(cid, "overlay_fail"))
    t.daemon = True
    t.start()
    return t.cancel


def ensure_started(daemon: bool = False) -> None:
    """Start the helper if it isn't running (the daemon calls this at startup so the first clap is
    instant).  Non-blocking: the connect / spawn happens on the worker thread."""
    try:
        if not enabled():
            return

        def go():
            if not _send_once(b"\n"):
                spawn_helper(daemon=daemon)
        threading.Thread(target=go, name="afterglow-indicator-start", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def send_raw(msg: dict) -> None:
    """Queue an arbitrary protocol message (Settings' Test button, tests)."""
    _put(("raw", msg))


def flush(timeout: float = 10.0) -> bool:
    """Wait until everything queued so far has been delivered (tests, CLI exit)."""
    if _queue is None:
        return True
    done = threading.Event()
    threading.Thread(target=lambda: (_queue.join(), done.set()), daemon=True).start()
    return done.wait(timeout)


def set_test_sink(fn) -> None:
    global _test_sink
    _test_sink = fn


# A short-lived process (the CLI's `trigger`) must not exit with events still queued: Python joins
# non-daemon threads (the input-overlay render) first, THEN runs atexit, so a late overlay_done
# emitted by that thread is still delivered here.
atexit.register(lambda: flush(5.0))
