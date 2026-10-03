"""
The GUI's handle on the YouTube host process (afterglow/youtube/host.py): the same methods and
signals as gui/upload_queue.UploadQueue, carried as JSON lines over a local socket.

Why a separate process: QtWebEngine's views are Qt Quick items inside, and PySide6 crashes when a
Python app-wide event filter (afterglow has two: wheel_guard, app_chrome) is the first to touch
one of those items (see host.py).  Keeping every web view in its own process avoids that for good,
and an upload no longer depends on the main window staying open.

The host is started on demand (the first command) and connected to without starting it when it
is already running (uploads from an earlier GUI session show up live).  Job states arrive as
snapshots; the library itself is shared through the DB as usual.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from .. import config as config_module
from ..youtube import gui_socket

CONNECT_RETRY_MS = 150
SPAWN_WAIT_MS = 15000


@dataclass
class JobView:
    """A job as the host last reported it (read-only)."""
    kind: str = "upload"
    video_id: int = 0
    key: int = 0
    state: str = "queued"
    percent: int = -1
    status: str = ""
    error: str = ""
    step: str = ""
    title: str = ""
    yt_id: str = ""
    cid: "str | None" = None

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    def label(self) -> str:
        return self.title or f"clip {self.video_id}"

    @classmethod
    def from_dict(cls, d: dict) -> "JobView":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def host_command() -> "list[str]":
    """The wrapped ``afterglow-youtube`` next to the running script (the Nix wrapper sets up Qt
    and QtWebEngine), else on PATH, else ``python -m afterglow.youtube.host``."""
    sibling = Path(sys.argv[0]).resolve().parent / "afterglow-youtube" if sys.argv and sys.argv[0] else None
    if sibling and sibling.is_file() and os.access(sibling, os.X_OK):
        return [str(sibling)]
    which = shutil.which("afterglow-youtube")
    if which:
        return [which]
    return [sys.executable, "-m", "afterglow.youtube.host"]


class RemoteUploadQueue(QObject):
    changed = Signal()
    job_finished = Signal(object)          # JobView
    idle = Signal()
    signed_in = Signal(str)
    opened = Signal(str)                   # the host opened a window for a command (play, sign_in, ...)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.jobs: "list[JobView]" = []
        self._sock = QLocalSocket(self)
        self._sock.readyRead.connect(self._read)
        self._sock.connected.connect(self._on_connected)
        self._sock.disconnected.connect(self._on_disconnected)
        self._sock.errorOccurred.connect(lambda _e: None)
        self._buf = b""
        self._outbox: "list[dict]" = []
        self._spawned_at = None
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._try_connect)
        self._connect_quietly()

    # ------------------------------------------------------------------ connection

    def _connect_quietly(self) -> None:
        """Pick up a host that's already running (uploads from an earlier session) without starting one."""
        if Path(gui_socket.socket_path()).exists():
            self._sock.connectToServer(gui_socket.socket_path())

    def connected(self) -> bool:
        return self._sock.state() == QLocalSocket.ConnectedState

    def _send(self, msg: dict) -> None:
        if self.connected():
            self._sock.write((json.dumps(msg) + "\n").encode())
            self._sock.flush()
            return
        self._outbox.append(msg)
        if not self._retry.isActive() and self._sock.state() == QLocalSocket.UnconnectedState:
            self._try_connect()

    def _try_connect(self) -> None:
        if self.connected() or self._sock.state() == QLocalSocket.ConnectingState:
            return
        self._sock.connectToServer(gui_socket.socket_path())
        if self._sock.waitForConnected(100):
            return
        self._sock.abort()
        import time
        now = time.monotonic()
        if self._spawned_at is None or now - self._spawned_at > SPAWN_WAIT_MS / 1000.0:
            self._spawn()
            self._spawned_at = now
        if now - self._spawned_at <= SPAWN_WAIT_MS / 1000.0:
            self._retry.start(CONNECT_RETRY_MS)
        else:
            print("Warning: the YouTube helper (afterglow-youtube) didn't start; dropped:", self._outbox)
            self._outbox.clear()

    def _spawn(self) -> None:
        env = dict(os.environ)
        pkg_root = str(Path(__file__).resolve().parent.parent.parent)
        env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            config_module.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            log = config_module.CONFIG_DIR / "youtube.log"
            if log.exists() and log.stat().st_size > 2_000_000:
                log.write_text("")
            logf = open(log, "ab")
        except OSError:
            logf = subprocess.DEVNULL
        try:
            subprocess.Popen(host_command(), env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
                             start_new_session=True)
        except OSError as e:
            print(f"Warning: couldn't start the YouTube helper: {e}")

    def _on_connected(self) -> None:
        self._spawned_at = None
        self._sock.write(b'{"cmd": "hello"}\n')         # subscribe to job snapshots / events
        pending, self._outbox = self._outbox, []
        for m in pending:
            self._send(m)

    def _on_disconnected(self) -> None:
        # the host exits when idle: whatever it was doing is over
        was_busy = self.busy()
        self.jobs = [j for j in self.jobs if j.finished]
        self.changed.emit()
        if was_busy:
            self.idle.emit()

    def _read(self) -> None:
        self._buf += bytes(self._sock.readAll())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            self._handle(msg)

    def _handle(self, msg: dict) -> None:
        ev = msg.get("event")
        if ev == "jobs":
            self.jobs = [JobView.from_dict(d) for d in msg.get("jobs", [])]
            self.changed.emit()
        elif ev == "job_finished":
            self.job_finished.emit(JobView.from_dict(msg.get("job", {})))
        elif ev == "idle":
            self.idle.emit()
        elif ev == "signed_in":
            self.signed_in.emit(str(msg.get("name", "")))
        elif ev == "opened":
            self.opened.emit(str(msg.get("what", "")))

    # ------------------------------------------------------------------ the UploadQueue API

    def enqueue_uploads(self, requests: "list[dict]") -> None:
        # the host records "queued" itself (it also fails stale states from a crash at start-up,
        # which must not race a record written here first)
        self._send({"cmd": "enqueue_uploads", "requests": requests})

    def enqueue_edit(self, video_id: int, title: str, description: str) -> None:
        self._send({"cmd": "enqueue_edit", "video_id": video_id, "title": title, "description": description})

    def enqueue_delete(self, video_id: int) -> None:
        self._send({"cmd": "enqueue_delete", "video_id": video_id})

    def register_manual(self, r: dict) -> None:
        self._send({"cmd": "register_manual", "request": r})

    def cancel(self, video_id: int) -> None:
        self._send({"cmd": "cancel", "video_id": video_id})

    def busy(self) -> bool:
        return any(not j.finished for j in self.jobs)

    def active_jobs(self) -> "list[JobView]":
        return [j for j in self.jobs if not j.finished]

    def job_for_video(self, video_id: int, kind: str = "upload") -> "JobView | None":
        for j in reversed(self.jobs):
            if j.video_id == video_id and j.kind == kind:
                return j
        return None

    def show_job(self, job) -> None:
        self._send({"cmd": "show_video", "video_id": job.video_id, "kind": job.kind})

    def show_for_cid(self, cid: str) -> bool:
        self._send({"cmd": "show_for_cid", "id": cid})
        return True

    def show_current(self) -> None:
        self._send({"cmd": "show_current"})

    def play(self, video_id: int) -> None:
        self._send({"cmd": "play", "video_id": video_id})

    def edit_in_studio(self, video_id: int) -> None:
        self._send({"cmd": "edit_in_studio", "video_id": video_id})

    def sign_in(self) -> None:
        self._send({"cmd": "sign_in"})

    def sign_out(self) -> bool:
        if self.busy():
            return False
        self._send({"cmd": "sign_out"})
        s = config_module.load()
        s.youtube.account_name = ""
        config_module.save(s)
        return True
