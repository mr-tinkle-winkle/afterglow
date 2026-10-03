"""
Processing throttle: slows afterglow's own background work (trimming, thumbnails, the input
overlay render) so it doesn't cost frames in the game being played.

What it throttles: this process's child processes that do the heavy lifting -- ``ffmpeg`` /
``ffprobe`` / ``puppetry-overlay`` -- and everything those start.  Nothing else (sound players,
the indicator helper, mpv) is touched.

How: a duty cycle.  Every ``PERIOD`` seconds the throttled processes run for ``duty`` of the time
and are paused (SIGSTOP / SIGCONT) for the rest, and their IO priority is set to idle.  Both are
fully reversible at any moment (a pause lasts at most one period), unlike ``nice``, which an
unprivileged process can raise but never lower again.

Modes (``AppSettings.processing_throttle``):
  off      no throttling (default)
  light    70 % duty         medium   45 % duty         heavy   20 % duty
  auto     follows the machine's load: the CPU busy time of everything else (afterglow's own
           throttled work excluded) and the GPU busy %, whichever is higher.  Below ~30 % pressure
           the work runs at full speed; toward ~85 % it is held to 15 % duty.  (A game's frame rate
           itself isn't readable from here, so the load is the stand-in: a game that saturates
           the GPU or the CPU gets the machine back.)

Bypass: clicking the clip indicator's processing element toggles a temporary bypass (the
indicator helper writes ``bypass_path()``; it removes it when its last capture is gone or when
clicked again).  While the file exists nothing is throttled.

GPU busy: AMD from ``/sys/class/drm/card*/device/gpu_busy_percent``; NVIDIA from ``nvidia-smi``
(polled at most once a second, only in auto mode while there is work to throttle); otherwise
unknown (CPU only).
"""
from __future__ import annotations

import ctypes
import glob
import logging
import os
import platform
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

logger = logging.getLogger("afterglow.throttle")

MODES = ("off", "light", "medium", "heavy", "auto")
MODE_LABELS = {
    "off": "Off",
    "light": "Light (runs 70% of the time)",
    "medium": "Medium (45%)",
    "heavy": "Heavy (20%)",
    "auto": "Auto (follows CPU / GPU load)",
}
DUTY = {"off": 1.0, "light": 0.70, "medium": 0.45, "heavy": 0.20}
AUTO_LOW, AUTO_HIGH, AUTO_MIN_DUTY = 30.0, 85.0, 0.15
PERIOD = 0.10            # s, one run + pause cycle
SAMPLE_EVERY = 0.5       # s, how often the process list, the load and the settings are refreshed
THROTTLED_NAMES = ("ffmpeg", "ffprobe", "puppetry-overlay")


def bypass_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/afterglow-{os.getuid()}"
    return Path(base) / "afterglow-throttle-bypass"


def bypassed() -> bool:
    return bypass_path().exists()


def set_bypass(on: bool) -> None:
    p = bypass_path()
    try:
        if on:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(str(time.time()))
        else:
            p.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("throttle bypass file %s: %s", p, e)


def auto_duty(pressure: float) -> float:
    """Duty for a load pressure (0..100): full speed below AUTO_LOW, AUTO_MIN_DUTY from AUTO_HIGH."""
    if pressure <= AUTO_LOW:
        return 1.0
    if pressure >= AUTO_HIGH:
        return AUTO_MIN_DUTY
    k = (pressure - AUTO_LOW) / (AUTO_HIGH - AUTO_LOW)
    return 1.0 + (AUTO_MIN_DUTY - 1.0) * k


# ------------------------------------------------------------------ /proc helpers

def _stat(pid: int) -> "tuple[int, int] | None":
    """(ppid, utime + stime ticks) of a process, or None if it is gone."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    rest = raw[raw.rfind(")") + 2:].split()
    try:
        return int(rest[1]), int(rest[11]) + int(rest[12])
    except (IndexError, ValueError):
        return None


def _is_heavy(pid: int) -> bool:
    try:
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    for a in argv[:2]:                      # argv[1] too: a script run through its interpreter
        name = os.path.basename(a.decode("utf-8", "replace"))
        if name in THROTTLED_NAMES:
            return True
    return False


def throttle_targets(root: int) -> "dict[int, int]":
    """{pid: cpu ticks} of the processes to throttle: heavy descendants of ``root`` and everything
    under them."""
    parent: dict[int, int] = {}
    ticks: dict[int, int] = {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            s = _stat(int(d))
            if s:
                parent[int(d)], ticks[int(d)] = s
    children: dict[int, list] = {}
    for pid, pp in parent.items():
        children.setdefault(pp, []).append(pid)
    out: dict[int, int] = {}
    stack = [(c, False) for c in children.get(root, [])]
    while stack:
        pid, under_heavy = stack.pop()
        heavy = under_heavy or _is_heavy(pid)
        if heavy:
            out[pid] = ticks.get(pid, 0)
        stack.extend((c, heavy) for c in children.get(pid, []))
    return out


def _cpu_times() -> "tuple[int, int] | None":
    """(busy, total) jiffies over all CPUs."""
    try:
        with open("/proc/stat") as f:
            parts = f.readline().split()[1:]
    except OSError:
        return None
    vals = [int(x) for x in parts[:8]]
    idle = vals[3] + vals[4]                 # idle + iowait
    total = sum(vals)
    return total - idle, total


class _Gpu:
    def __init__(self):
        self.amd = glob.glob("/sys/class/drm/card*/device/gpu_busy_percent")
        self.nvidia = shutil.which("nvidia-smi")
        self._last = (0.0, None)

    def busy(self) -> "float | None":
        vals = []
        for p in self.amd:
            try:
                vals.append(float(Path(p).read_text().strip()))
            except (OSError, ValueError):
                pass
        if self.nvidia:
            t, v = self._last
            if time.monotonic() - t >= 1.0:
                try:
                    out = subprocess.run([self.nvidia, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                         capture_output=True, text=True, timeout=1.5).stdout
                    v = max(float(x) for x in out.split() if x.strip())
                except (OSError, ValueError, subprocess.SubprocessError):
                    v = None
                self._last = (time.monotonic(), v)
            if v is not None:
                vals.append(v)
        return max(vals) if vals else None


# ------------------------------------------------------------------ IO priority (ioprio_set)

_IOPRIO_SYS = {"x86_64": 251, "aarch64": 30, "i686": 289, "armv7l": 315}.get(platform.machine())
_libc = None


def _ioprio(pid: int, idle: bool) -> None:
    """Idle IO class while throttled; back to best-effort (normal) otherwise."""
    global _libc
    if _IOPRIO_SYS is None:
        return
    try:
        if _libc is None:
            _libc = ctypes.CDLL(None, use_errno=True)
        value = (3 << 13) if idle else ((2 << 13) | 4)       # IOPRIO_CLASS_IDLE / IOPRIO_CLASS_BE, level 4
        _libc.syscall(_IOPRIO_SYS, 1, pid, value)            # IOPRIO_WHO_PROCESS
    except Exception:  # noqa: BLE001 -- best effort
        pass


# ------------------------------------------------------------------ the controller

class Throttler:
    """Runs on its own thread; ``mode_source()`` returns the current mode (re-read every sample)."""

    def __init__(self, mode_source, root: "int | None" = None, clock=time.monotonic, sleep=time.sleep):
        self.mode_source = mode_source
        self.root = root or os.getpid()
        self.clock, self.sleep = clock, sleep
        self.targets: dict[int, int] = {}
        self.stopped: set[int] = set()
        self.idle_io: set[int] = set()
        self.mode = "off"
        self.duty = 1.0
        self.pressure = 0.0
        self._prev_cpu = None
        self._gpu = None
        self._running = False
        self._thread: "threading.Thread | None" = None
        self._lock = threading.Lock()

    # -- one sample: process list, mode, load -> duty
    def sample(self) -> float:
        try:
            self.mode = self.mode_source()
        except Exception:  # noqa: BLE001
            self.mode = "off"
        if self.mode not in MODES:
            self.mode = "off"
        prev_targets = self.targets
        self.targets = throttle_targets(self.root)
        cpu = _cpu_times()
        if self.mode == "auto":
            if self._gpu is None:
                self._gpu = _Gpu()
            other = 0.0
            if cpu and self._prev_cpu:
                busy_d = cpu[0] - self._prev_cpu[0]
                total_d = max(1, cpu[1] - self._prev_cpu[1])
                ours = sum(max(0, t - prev_targets.get(pid, t)) for pid, t in self.targets.items())
                other = max(0.0, min(100.0, (busy_d - ours) / total_d * 100.0))
            gpu = self._gpu.busy() if self.targets else None
            raw = max(other, gpu or 0.0)
            self.pressure = raw if self.pressure == 0.0 else self.pressure + 0.4 * (raw - self.pressure)
            duty = auto_duty(self.pressure)
        else:
            duty = DUTY.get(self.mode, 1.0)
        self._prev_cpu = cpu
        if bypassed() or not self.targets:
            duty = 1.0
        self.duty = duty
        self._apply_io(duty < 0.999)
        return duty

    def _apply_io(self, throttling: bool) -> None:
        for pid in list(self.targets):
            if throttling and pid not in self.idle_io:
                _ioprio(pid, True)
                self.idle_io.add(pid)
            elif not throttling and pid in self.idle_io:
                _ioprio(pid, False)
                self.idle_io.discard(pid)
        self.idle_io &= set(self.targets)

    def _signal(self, sig) -> None:
        for pid in list(self.targets):
            try:
                os.kill(pid, sig)
                if sig == signal.SIGSTOP:
                    self.stopped.add(pid)
                else:
                    self.stopped.discard(pid)
            except (ProcessLookupError, PermissionError):
                self.stopped.discard(pid)

    def resume_all(self) -> None:
        for pid in list(self.stopped):
            try:
                os.kill(pid, signal.SIGCONT)
            except (ProcessLookupError, PermissionError):
                pass
        self.stopped.clear()

    def run(self) -> None:
        next_sample = 0.0
        try:
            while self._running:
                now = self.clock()
                if now >= next_sample:
                    self.sample()
                    next_sample = now + SAMPLE_EVERY
                if self.duty >= 0.999 or not self.targets:
                    self.resume_all()
                    self.sleep(SAMPLE_EVERY if not self.targets else PERIOD)
                    continue
                self.resume_all()
                self.sleep(PERIOD * self.duty)
                self._signal(signal.SIGSTOP)
                self.sleep(PERIOD * (1.0 - self.duty))
        finally:
            self.resume_all()

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self.run, name="afterglow-throttle", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.resume_all()


_THROTTLER: "Throttler | None" = None


def _configured_mode() -> str:
    from . import config as config_module
    return getattr(config_module.load_readonly(), "processing_throttle", "off")


def ensure_running() -> Throttler:
    """Start this process's throttler (idempotent).  Called by the daemon at startup and by
    trigger_clip, so every process that captures clips throttles its own work."""
    global _THROTTLER
    if _THROTTLER is None:
        _THROTTLER = Throttler(_configured_mode)
        import atexit
        atexit.register(_THROTTLER.resume_all)
    _THROTTLER.start()
    return _THROTTLER
