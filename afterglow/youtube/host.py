"""
The YouTube host process (``afterglow-youtube`` / ``python -m afterglow.youtube.host``): owns every
web view -- the hidden Studio window and the upload queue (gui/upload_queue.py), the sign-in
window, the embed player, Studio edit windows -- and serves the GUI's RemoteUploadQueue
(gui/youtube_client.py) plus the clip indicator's red-circle clicks on one local socket
(youtube/gui_socket.socket_path(), JSON lines).

Why its own process: QtWebEngine's views are built from Qt Quick items, and with PySide6 a
Python *application-wide* event filter crashes the process the first time it sees one of those
items (PySide wraps the unknown item on the fly, wrapping sets a dynamic property, the property
change is itself an event sent through the same filter, and the half-built wrapper is re-entered
-- reproduced with a filter that only returns False).  The main GUI depends on two such filters
(wheel_guard, app_chrome), so the web views live here, where no such filter is ever installed.
It also means an upload keeps running when the main window is closed.

Protocol.  GUI -> host: {"cmd": ...} (enqueue_uploads, enqueue_edit, enqueue_delete,
register_manual, cancel, show_video, show_for_cid, show_current, play, edit_in_studio, sign_in,
sign_out); the indicator helper sends {"event": "show_upload", "id": <circle id>}.  Host -> every
client: {"event": "jobs", "jobs": [...]}, {"event": "job_finished", "job": {...}}, {"event": "idle"},
{"event": "signed_in", "name": ...}.

Lifetime: one host per user session (a second one exits at once); it quits after IDLE_EXIT
seconds with nothing queued / running / verifying and no YouTube window open.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

IDLE_EXIT = float(os.environ.get("AFTERGLOW_YT_HOST_IDLE_EXIT", "120"))


def _job_dict(job) -> dict:
    return {"kind": job.kind, "video_id": job.video_id, "key": job.key, "state": job.state,
            "percent": job.percent, "status": job.status, "error": job.error, "step": job.step,
            "title": job.title, "yt_id": job.yt_id, "cid": job.cid}


class Host:
    def __init__(self, app):
        from PySide6.QtCore import QTimer
        from PySide6.QtNetwork import QLocalServer
        from ..gui import upload_queue as UQ
        from . import gui_socket
        self.app = app
        self.queue = UQ.UploadQueue()
        UQ.set_upload_queue(self.queue)
        self.server = QLocalServer()
        self.clients: list = []
        self._listeners: set = set()           # clients that said hello (the GUI): they get events
        self._bufs: dict = {}
        self._push = QTimer()
        self._push.setSingleShot(True)
        self._push.setInterval(100)
        self._push.timeout.connect(self._push_jobs)
        self.queue.changed.connect(self._push.start)
        self.queue.job_finished.connect(lambda j: self._broadcast({"event": "job_finished", "job": _job_dict(j)}))
        self.queue.idle.connect(lambda: self._broadcast({"event": "idle"}))
        self.queue.signed_in.connect(lambda name: self._broadcast({"event": "signed_in", "name": name}))
        self._idle = QTimer()
        self._idle.setInterval(5000)
        self._idle.timeout.connect(self._check_idle)
        self._idle_since = None
        self.path = gui_socket.socket_path()

    def listen(self) -> bool:
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        probe = QLocalSocket()
        probe.connectToServer(self.path)
        if probe.waitForConnected(300):
            probe.disconnectFromServer()
            return False                                # another host already serves this session
        QLocalServer.removeServer(self.path)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._on_conn)
        ok = self.server.listen(self.path)
        if ok:
            self._idle.start()
        return ok

    # ------------------------------------------------------------------ clients

    def _on_conn(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self.clients.append(sock)
            self._bufs[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(lambda s=sock: self._gone(s))
            # nothing is written to a connection before it says "hello": a fire-and-forget sender
            # (the indicator) may already have closed, and writing to it would abort the socket
            # together with the message still unread in it
            if sock.bytesAvailable():
                self._read(sock)

    def _gone(self, sock) -> None:
        try:
            tail = bytes(sock.readAll())                 # a fire-and-forget sender may close before readyRead
        except RuntimeError:
            tail = b""
        rest = self._bufs.pop(sock, b"") + tail
        if sock in self.clients:
            self.clients.remove(sock)
        self._listeners.discard(sock)
        for line in rest.split(b"\n"):
            if line.strip():
                self._line(line)
        try:
            sock.deleteLater()
        except RuntimeError:
            pass

    def _read(self, sock) -> None:
        if sock not in self._bufs:
            return                                          # already handled by _gone
        self._bufs[sock] = self._bufs.get(sock, b"") + bytes(sock.readAll())
        while b"\n" in self._bufs.get(sock, b""):
            line, self._bufs[sock] = self._bufs[sock].split(b"\n", 1)
            if line.strip() == b'{"cmd": "hello"}':
                self._listeners.add(sock)
                self._send(sock, {"event": "jobs", "jobs": [_job_dict(j) for j in self.queue.jobs]})
                continue
            self._line(line)

    def _send(self, sock, msg: dict) -> None:
        try:
            sock.write((json.dumps(msg) + "\n").encode())
            sock.flush()
        except RuntimeError:
            pass

    def _broadcast(self, msg: dict) -> None:
        for s in list(self.clients):
            if s in self._listeners:
                self._send(s, msg)

    def _push_jobs(self) -> None:
        self._broadcast({"event": "jobs", "jobs": [_job_dict(j) for j in self.queue.jobs]})

    # ------------------------------------------------------------------ commands

    def _line(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return
        if isinstance(msg, dict):
            try:
                self.handle(msg)
            except Exception:  # noqa: BLE001 -- one bad command never takes the uploads down
                import traceback
                traceback.print_exc()

    def handle(self, msg: dict) -> None:
        if os.environ.get("AFTERGLOW_YT_HOST_DEBUG"):
            print("host <-", msg, flush=True)
        q = self.queue
        if msg.get("event") == "show_upload":                 # the red indicator circle was clicked
            if not q.show_for_cid(str(msg.get("id", ""))):
                q.show_current()
            self._broadcast({"event": "opened", "what": "show_upload"})
            return
        cmd = msg.get("cmd")
        if cmd == "enqueue_uploads":
            q.enqueue_uploads(list(msg.get("requests", [])))
        elif cmd == "enqueue_edit":
            q.enqueue_edit(int(msg["video_id"]), str(msg.get("title", "")), str(msg.get("description", "")))
        elif cmd == "enqueue_delete":
            q.enqueue_delete(int(msg["video_id"]))
        elif cmd == "register_manual":
            q.register_manual(dict(msg["request"]))
        elif cmd == "cancel":
            q.cancel(int(msg["video_id"]))
        elif cmd == "show_video":
            job = q.job_for_video(int(msg["video_id"]), str(msg.get("kind", "upload")))
            if job is not None:
                q.show_job(job)
        elif cmd == "show_for_cid":
            if not q.show_for_cid(str(msg.get("id", ""))):
                q.show_current()
        elif cmd == "show_current":
            q.show_current()
        elif cmd == "play":
            q.play(int(msg["video_id"]))
        elif cmd == "edit_in_studio":
            q.edit_in_studio(int(msg["video_id"]))
        elif cmd == "sign_in":
            q.sign_in()
        if cmd in ("play", "edit_in_studio", "sign_in", "show_video", "show_for_cid", "show_current"):
            self._broadcast({"event": "opened", "what": cmd})
        elif cmd == "sign_out":
            q.sign_out()
        elif cmd == "quit":
            self.app.quit()

    # ------------------------------------------------------------------ lifetime

    def _check_idle(self) -> None:
        import time
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        windows_open = any(w.isVisible() and w.isWindow() and not w.testAttribute(Qt.WA_DontShowOnScreen)
                           for w in QApplication.topLevelWidgets())
        if self.queue.busy() or windows_open:
            self._idle_since = None
            return
        self._idle_since = self._idle_since or time.monotonic()
        if time.monotonic() - self._idle_since >= IDLE_EXIT:
            self.app.quit()


def main(argv=None) -> int:
    if os.geteuid() == 0:                 # Chromium refuses to run its sandbox as root
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    from PySide6.QtCore import QCoreApplication, Qt
    QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 -- initialise the engine first
    from PySide6.QtWidgets import QApplication
    from .. import db
    db.init_db()
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("afterglow-youtube")
    app.setDesktopFileName("afterglow")
    app.setQuitOnLastWindowClosed(False)
    host = Host(app)
    if not host.listen():
        print("afterglow-youtube: another instance is already running.")
        return 0
    print(f"afterglow-youtube: listening on {host.path}", flush=True)
    rc = app.exec()
    host.server.close()
    from PySide6.QtNetwork import QLocalServer
    QLocalServer.removeServer(host.path)
    return rc


if __name__ == "__main__":
    sys.exit(main())
