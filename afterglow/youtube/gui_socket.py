"""
The YouTube host process's socket (youtube/host.py serves it): the GUI's RemoteUploadQueue talks
to the host here, and the clip indicator helper sends ``{"event": "show_upload", "id": <circle
id>}`` here when the red upload circle is clicked, so the host pops that upload's Studio window out.

``send`` is plain Python (the indicator side): fire-and-forget, never raises, never blocks long.
``make_server`` is a minimal listener kept for tests.
"""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path


def socket_path() -> str:
    env = os.environ.get("AFTERGLOW_GUI_SOCKET")
    if env:
        return env
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/afterglow-{os.getuid()}"
    return str(Path(base) / "afterglow-youtube.sock")


def send(msg: dict, timeout: float = 0.3) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.connect(socket_path())
        s.sendall((json.dumps(msg) + "\n").encode())
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass


def make_server(parent=None):
    """The GUI side: a QLocalServer emitting ``message(dict)``.  None if another afterglow GUI
    already owns the socket (that one gets the clicks)."""
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    class GuiSocketServer(QObject):
        message = Signal(dict)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.server = QLocalServer(self)
            self._bufs: dict = {}

        def listen(self) -> bool:
            path = socket_path()
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            probe = QLocalSocket()
            probe.connectToServer(path)
            if probe.waitForConnected(150):
                probe.disconnectFromServer()
                return False
            QLocalServer.removeServer(path)
            self.server.setSocketOptions(QLocalServer.UserAccessOption)
            self.server.newConnection.connect(self._on_conn)
            return self.server.listen(path)

        def _on_conn(self) -> None:
            while self.server.hasPendingConnections():
                sock = self.server.nextPendingConnection()
                self._bufs[sock] = b""
                sock.readyRead.connect(lambda s=sock: self._read(s))
                sock.disconnected.connect(lambda s=sock: self._done(s))
                if sock.bytesAvailable():
                    self._read(sock)

        def _read(self, sock) -> None:
            self._bufs[sock] = self._bufs.get(sock, b"") + bytes(sock.readAll())
            while b"\n" in self._bufs[sock]:
                line, self._bufs[sock] = self._bufs[sock].split(b"\n", 1)
                self._emit(line)

        def _done(self, sock) -> None:
            rest = self._bufs.pop(sock, b"")
            if rest.strip():
                self._emit(rest)
            sock.deleteLater()

        def _emit(self, line: bytes) -> None:
            try:
                msg = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return
            if isinstance(msg, dict):
                self.message.emit(msg)

        def close(self) -> None:
            self.server.close()
            QLocalServer.removeServer(socket_path())

    srv = GuiSocketServer(parent)
    return srv if srv.listen() else None
