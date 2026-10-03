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


class _FullScreenWindow:
    """The player's fullscreen: accepting the page's fullscreen request only changes what the page
    THINKS; a window has to actually go fullscreen.  Same pattern as QtWebEngine's own example:
    a frameless window with its own view takes the page over and shows full screen on the player's
    screen; leaving fullscreen (the player's button, Esc, F11, or closing it) hands the page back."""

    def __init__(self, player: "YouTubePlayerDialog"):
        from PySide6.QtGui import QKeySequence, QShortcut
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        self.player = player
        self.window = QWidget(None, Qt.Window | Qt.FramelessWindowHint)
        self.window.setWindowTitle(player.windowTitle())
        self.window.setStyleSheet("QWidget { background-color: black; }")
        lay = QVBoxLayout(self.window)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self.window)
        lay.addWidget(self.view)
        # held directly: once this view owns the page, player.view.page() is no longer it
        self.page = player.page
        self.view.setPage(self.page)
        # application-wide while fullscreen is up: a window-scoped shortcut needs this window to be
        # the active one, which focus inside the web content (or the window manager) doesn't guarantee
        self._shortcuts = []
        for key in ("Esc", "F11"):
            sc = QShortcut(QKeySequence(key), self.window, activated=self.exit)
            sc.setContext(Qt.ApplicationShortcut)
            self._shortcuts.append(sc)
        screen = player.screen() or player.window().screen()
        if screen is not None:
            self.window.setScreen(screen)
            self.window.setGeometry(screen.geometry())
        self.window.showFullScreen()
        self.window.raise_()
        self.window.activateWindow()
        self.view.setFocus()

    def exit(self) -> None:
        from PySide6.QtWebEngineCore import QWebEnginePage
        self.page.triggerAction(QWebEnginePage.ExitFullScreen)

    def close(self) -> None:
        self.player.view.setPage(self.page)     # the page goes back to the player
        self.window.hide()
        self.window.deleteLater()


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
        self.page = QWebEnginePage(profile(), self)     # owned by the dialog: it moves between views
        self.view.setPage(self.page)
        self.view.setMinimumSize(960, 540)
        self._fullscreen: "_FullScreenWindow | None" = None
        self.page.fullScreenRequested.connect(self._on_fullscreen_requested)
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
        src = embed_url(self.video_id)
        # an https base so the embed gets a referrer; same scheme as the embed (no mixed-content block)
        base = QUrl("https://localhost/" if src.startswith("https:") else "http://localhost/")
        self.view.setHtml(_PAGE.format(src=src), base)

    def _on_fullscreen_requested(self, request) -> None:
        if request.toggleOn():
            if self._fullscreen is not None:
                request.reject()
                return
            request.accept()
            self._fullscreen = _FullScreenWindow(self)
        else:
            if self._fullscreen is None:
                request.reject()
                return
            request.accept()
            fs, self._fullscreen = self._fullscreen, None
            fs.close()
            self.raise_()
            self.activateWindow()

    def _loaded(self, ok: bool) -> None:
        if not ok and not self.fell_back:
            self.fell_back = True
            open_in_browser(self.video_id)
            self.accept()

    def done(self, r) -> None:
        if self._fullscreen is not None:            # closing while fullscreen: drop that window too
            fs, self._fullscreen = self._fullscreen, None
            fs.close()
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
