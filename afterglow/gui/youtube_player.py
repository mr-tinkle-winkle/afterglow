"""
Double-click in the Uploaded tab: the video plays in-app through YouTube's embed player
(``https://www.youtube.com/embed/<id>``) in a web view of the shared YouTube profile -- nothing is
read from the local cache for playback.  "Open on YouTube" (and any load failure) falls back to the
system browser.

The embed is wrapped in a tiny page with an https base URL: YouTube's player refuses to start
without a referrer ("Error 153"), which a bare setUrl() of the embed would not send.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout

from ..youtube import embed_url, watch_url
from .custom_button import CustomButton
from .themed_dialogs import ThemedDialog

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="referrer" content="strict-origin-when-cross-origin">
<style>html,body{{margin:0;height:100%;background:#000}}iframe{{border:0;width:100%;height:100%}}</style></head>
<body><iframe src="{src}" allow="autoplay; encrypted-media; fullscreen; picture-in-picture"
 referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe></body></html>"""


def open_in_browser(video_id: str) -> None:
    QDesktopServices.openUrl(QUrl(watch_url(video_id)))


class YouTubePlayerDialog(ThemedDialog):
    def __init__(self, video, parent=None):
        super().__init__(video.youtube_title or video.title, parent, margins=14)
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWebEngineCore import QWebEnginePage
        from ..youtube.profile import profile
        self.video_id = video.youtube_video_id
        self.view = QWebEngineView(self)
        self.view.setPage(QWebEnginePage(profile(), self.view))
        self.view.setMinimumSize(960, 540)
        self.view.page().fullScreenRequested.connect(lambda req: req.accept())
        self.view.loadFinished.connect(self._loaded)
        self.lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        browser = CustomButton("Open on YouTube")
        browser.clicked.connect(lambda: (open_in_browser(self.video_id), self.accept()))
        close = CustomButton("Close")
        close.clicked.connect(self.accept)
        for b in (browser, close):
            b.setMinimumHeight(30)
            b.setMinimumWidth(110)
            row.addWidget(b)
        self.lay.addLayout(row)
        self.fell_back = False
        self.view.setHtml(_PAGE.format(src=embed_url(self.video_id)), QUrl("https://localhost/"))

    def _loaded(self, ok: bool) -> None:
        if not ok and not self.fell_back:
            self.fell_back = True
            open_in_browser(self.video_id)
            self.accept()

    def done(self, r) -> None:
        # stop playback (the page would otherwise keep playing audio until it is garbage-collected)
        try:
            self.view.setHtml("")
        except RuntimeError:
            pass
        super().done(r)


def play(video, parent=None) -> None:
    if not video.youtube_video_id:
        return
    try:
        dlg = YouTubePlayerDialog(video, parent)
    except Exception:  # noqa: BLE001 -- no web engine available: the browser it is
        open_in_browser(video.youtube_video_id)
        return
    dlg.setAttribute(Qt.WA_DeleteOnClose, True)
    dlg.open()
