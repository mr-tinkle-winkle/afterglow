"""
The indicator helper: owns the model, the overlay surfaces and the socket the daemon / GUI /
CLI talk to.  Run as ``python -m afterglow.indicator`` (``afterglow-indicator``).

Protocol: JSON lines over a Unix socket (``$XDG_RUNTIME_DIR/afterglow-indicator.sock``, or
``AFTERGLOW_INDICATOR_SOCKET``):

    {"id": "<capture id>", "event": "start", "style": {...}}
    {"id": "<capture id>", "event": "clap" | "processing" | "done" | "overlay" |
                                    "overlay_done" | "overlay_fail" | "fail"}
    {"event": "quit"}

A socket (rather than the helper's stdin) because three different processes send events --
the daemon, the CLI's ``trigger`` and Settings' Test button -- and the helper has to outlive
any one of them and be found again after a crash.  Fire-and-forget: nothing is sent back.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import EVENTS, layershell
from .focus import choose_screen
from .model import Model, Style
from .surface import IndicatorSurface

logger = logging.getLogger("afterglow.indicator")

FRAME_MS = 16          # ~60 fps while anything is on screen


def socket_path() -> str:
    env = os.environ.get("AFTERGLOW_INDICATOR_SOCKET")
    if env:
        return env
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/afterglow-{os.getuid()}"
    return str(Path(base) / "afterglow-indicator.sock")


class IndicatorHelper(QObject):
    def __init__(self, clock=time.monotonic, log_path: "str | None" = None, idle_exit: float = 0.0,
                 force_layer_shell: "bool | None" = None, on_quit=None):
        super().__init__()
        self.clock = clock
        self.model = Model()
        self.surfaces: "dict[tuple[str, str], IndicatorSurface]" = {}
        self.log_path = log_path
        self.idle_exit = idle_exit
        self.on_quit = on_quit
        self.last_activity = clock()
        platform = QGuiApplication.platformName() or ""
        self.is_wayland = platform.startswith("wayland")
        if force_layer_shell is None:
            self.layer = self.is_wayland and layershell.available()
        else:
            self.layer = force_layer_shell
        # Wayland without layer-shell can't place or raise the window: skip the visual (sounds still play)
        self.can_show = (not self.is_wayland) or self.layer
        if not self.can_show:
            logger.warning("Wayland session but no layer-shell shim (AFTERGLOW_LAYERSHELL_LIB): the indicator "
                           "will not be drawn (sounds still play).")
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.setInterval(FRAME_MS)
        self.timer.timeout.connect(self.step)
        self.server: "QLocalServer | None" = None
        self._buffers: "dict[QLocalSocket, bytes]" = {}

    # ------------------------------------------------------------ socket

    def listen(self, path: "str | None" = None) -> bool:
        """Start the server.  False if another helper already owns the socket."""
        path = path or socket_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        probe = QLocalSocket()
        probe.connectToServer(path)
        if probe.waitForConnected(150):
            probe.disconnectFromServer()
            return False
        QLocalServer.removeServer(path)                    # a stale socket file from a crashed helper
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._on_connection)
        ok = self.server.listen(path)
        if not ok:
            logger.error("could not listen on %s: %s", path, self.server.errorString())
        return ok

    def _on_connection(self) -> None:
        while self.server and self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))
            if sock.bytesAvailable():
                self._on_ready(sock)

    def _on_ready(self, sock: QLocalSocket) -> None:
        self._buffers[sock] = self._buffers.get(sock, b"") + bytes(sock.readAll())
        while b"\n" in self._buffers[sock]:
            line, self._buffers[sock] = self._buffers[sock].split(b"\n", 1)
            self.handle_line(line)

    def _on_disconnected(self, sock: QLocalSocket) -> None:
        rest = self._buffers.pop(sock, b"")
        if rest.strip():                                    # a last message with no trailing newline
            self.handle_line(rest)
        try:
            sock.deleteLater()
        except RuntimeError:                                # already gone: the server is shutting down
            pass

    # ------------------------------------------------------------ events

    def handle_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.warning("ignoring a malformed line: %r", line[:80])
            return
        if isinstance(msg, dict):
            self.handle(msg)

    def handle(self, msg: dict) -> None:
        now = self.clock()
        self.last_activity = now
        event = msg.get("event")
        if event == "quit":
            self._log(msg)
            if self.on_quit:
                self.on_quit()
            return
        cid = msg.get("id")
        if event not in EVENTS or not isinstance(cid, str) or not cid:
            logger.warning("ignoring a message with an unknown event or no id: %r", msg)
            return
        if event == "start":
            style = Style.from_dict(msg.get("style"))
            self.model.event(cid, "start", now, style, screen_key=self._screen_name(style))
        else:
            self.model.event(cid, event, now)
        self._log(msg)
        self.step()

    def _screens(self):
        return QGuiApplication.screens()

    def _screen_name(self, style: Style) -> str:
        screens = self._screens()
        spec = [(s.name(), (s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height())) for s in screens]
        primary = QGuiApplication.primaryScreen()
        return choose_screen(spec, style.screen_hint, primary.name() if primary else "", style.screen)

    def _screen_obj(self, name: str):
        for s in self._screens():
            if s.name() == name:
                return s
        return QGuiApplication.primaryScreen()

    # ------------------------------------------------------------ frame loop

    def step(self) -> None:
        now = self.clock()
        self.model.tick(now)
        self._sync(now)
        for s in self.surfaces.values():
            s.update()
        if not self.surfaces and len(self.model) == 0:
            self.timer.stop()
            if self.idle_exit > 0 and now - self.last_activity > self.idle_exit and self.on_quit:
                self.on_quit()
        elif not self.timer.isActive():
            self.timer.start()

    def _sync(self, now: float) -> None:
        alive = {k for k in self.model.stack_keys() if self.model.stack_alive(k)}
        if self.can_show:
            for key in alive:
                if key not in self.surfaces:
                    st = self.model.style_of(key)
                    if st is None:
                        continue
                    surf = IndicatorSurface(self.model, key, key[1], st.size, st.padding_x, st.padding_y,
                                            self._screen_obj(key[0]), self.clock, self.layer)
                    surf.present()
                    self.surfaces[key] = surf
        for key in list(self.surfaces):
            if key not in alive:
                surf = self.surfaces.pop(key)
                surf.hide()
                surf.deleteLater()
        if self.idle_exit > 0 and not alive:
            pass

    # ------------------------------------------------------------ introspection

    def snapshot(self) -> dict:
        now = self.clock()
        return {
            "stacks": {f"{k[0]}|{k[1]}": [{"id": i.id, "state": i.state, "slot": round(i.slot_at(now), 3)}
                                         for i in self.model._stacks.get(k, [])] for k in self.model.stack_keys()},
            "surfaces": sorted(f"{k[0]}|{k[1]}" for k in self.surfaces),
        }

    def _log(self, msg: dict) -> None:
        if not self.log_path:
            return
        try:
            rec = {"t": round(self.clock(), 3), "msg": {k: v for k, v in msg.items() if k != "style"},
                   "style": bool(msg.get("style")), "snapshot": self.snapshot()}
            with open(self.log_path, "a") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
