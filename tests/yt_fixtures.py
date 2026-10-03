"""
Shared fixtures for the YouTube upload tests: a local HTTP server that plays YouTube Studio
(tests/fake_studio/*.html, the same selectors as afterglow/youtube/studio_steps.json), YouTube's
public oEmbed endpoint and i.ytimg.com thumbnails -- plus a studio_steps.json override pointing
the driver at it (AFTERGLOW_STUDIO_STEPS).  Import this BEFORE anything from afterglow.

    import yt_fixtures as F
    F.READY.add("FAKEvid1234")      # oEmbed / thumbnail answer for this id from now on
"""
from __future__ import annotations

import http.server
import io
import json
import os
import socketserver
import tempfile
import threading
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake_studio"
READY: "set[str]" = set()          # video ids whose oEmbed / thumbnail exist
HITS: "list[str]" = []


def _jpeg_bytes() -> bytes:
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    img = QImage(480, 360, QImage.Format_RGB32)
    for y in range(360):
        for x in range(0, 480, 4):
            img.setPixelColor(x, y, QColor((x * 7) % 255, (y * 5) % 255, (x + y) % 255))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "JPG", 95)
    return bytes(ba)


_JPEG = None


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(FAKE), **kw)

    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        global _JPEG
        u = urllib.parse.urlparse(self.path)
        HITS.append(u.path)
        if u.path == "/ua":
            body = json.dumps({"ua": self.headers.get("User-Agent", ""),
                               "ch": self.headers.get("Sec-CH-UA", None)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if u.path == "/oembed":
            q = urllib.parse.parse_qs(u.query)
            watch = q.get("url", [""])[0]
            vid = urllib.parse.parse_qs(urllib.parse.urlparse(watch).query).get("v", [""])[0]
            if vid in READY:
                body = json.dumps({"title": f"video {vid}", "type": "video"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(401 if vid else 404)
            return
        if u.path.startswith("/thumb/"):
            vid = u.path.split("/")[2].split(".")[0]
            if vid in READY:
                if _JPEG is None:
                    _JPEG = _jpeg_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(_JPEG)))
                self.end_headers()
                self.wfile.write(_JPEG)
            else:
                self.send_error(404)
            return
        return super().do_GET()


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def start(upload_query: str = "") -> dict:
    """Start the fake Studio + a second server playing the Google sign-in host; write the steps
    override.  Returns {"port", "signin_port", "base", "steps_path"}."""
    srv = _Server(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv2 = _Server(("localhost", 0), _Handler)
    port2 = srv2.server_address[1]
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    os.environ["AFTERGLOW_YT_OEMBED"] = base + "/oembed?url={url}"
    os.environ["AFTERGLOW_YT_THUMB"] = base + "/thumb/{id}.jpg"
    info = {"port": port, "signin_port": port2, "base": base, "signin_base": f"http://localhost:{port2}"}
    info["steps_path"] = write_steps(info, upload_query)
    return info


def write_steps(info: dict, upload_query: str = "", **overrides) -> str:
    data = json.loads((ROOT / "afterglow" / "youtube" / "studio_steps.json").read_text())
    data["upload_url"] = f"{info['base']}/upload.html?{upload_query}"
    data["studio_url"] = f"{info['base']}/home.html"
    data["edit_url"] = f"{info['base']}/edit.html?vid={{video_id}}"
    data["studio_hosts"] = [f"127.0.0.1:{info['port']}"]
    data["signin_hosts"] = [f"localhost:{info['signin_port']}"]
    for k, v in overrides.items():
        data[k] = v
    path = info.get("steps_path") or os.path.join(tempfile.mkdtemp(prefix="yt_steps_"), "studio_steps.json")
    Path(path).write_text(json.dumps(data))
    os.environ["AFTERGLOW_STUDIO_STEPS"] = path
    return path


def set_flow_timeouts(info: dict, flow: str, upload_query: str = "", **step_timeouts) -> None:
    data = json.loads(Path(info["steps_path"]).read_text())
    for st in data["flows"][flow]:
        if st["id"] in step_timeouts:
            st["timeout"] = step_timeouts[st["id"]]
    data["upload_url"] = f"{info['base']}/upload.html?{upload_query}"
    Path(info["steps_path"]).write_text(json.dumps(data))
