"""
Signing in to YouTube, once, from Settings: a window on YouTube Studio in the shared profile
(youtube/profile.py).  Google's own sign-in page does the work; the window closes by itself as
soon as Studio opens signed in, after reading the channel's name off the page.

Already signed in?  Studio opens straight away and the window closes within a second or two, so
"Sign in" doubles as "check the sign-in".
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import config as config_module
from .custom_button import CustomButton
from .theme import Theme


class SignInWindow(QWidget):
    signed_in = Signal(str)        # the channel's name ("" when it couldn't be read)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from ..youtube.studio import StudioPage
        from ..youtube import steps as steps_module
        self.setWindowTitle("Sign in to YouTube -- afterglow")
        self.resize(1000, 820)
        self._data = steps_module.load()
        a = config_module.load_readonly().appearance
        theme = Theme(a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        banner = QWidget()
        banner.setAutoFillBackground(True)
        pal = banner.palette()
        pal.setColor(QPalette.Window, theme.library_background())
        pal.setColor(QPalette.WindowText, QColor(a.card_text_color))
        banner.setPalette(pal)
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 12, 8)
        self.message = QLabel("Sign in with the Google account of the YouTube channel to upload to. "
                              "This window closes by itself once YouTube Studio opens.")
        self.message.setWordWrap(True)
        self.message.setPalette(pal)
        row.addWidget(self.message, 1)
        cancel = CustomButton("Cancel")
        cancel.setMinimumHeight(30)
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        lay.addWidget(banner)
        self.view = QWebEngineView(self)
        self.page = StudioPage(self.view)
        self.view.setPage(self.page)
        lay.addWidget(self.view, 1)
        self._done = False
        self._runner = None
        self.page.urlChanged.connect(self._on_url)
        self.page.loadFinished.connect(lambda _ok: self._on_url(self.page.url()))

    def start(self) -> None:
        self.show()
        self.page.load(QUrl(self._data.get("studio_url", "https://studio.youtube.com/")))

    def _on_url(self, url: QUrl) -> None:
        if self._done or self._runner is not None:
            return
        hosts = self._data.get("studio_hosts", ["studio.youtube.com"])
        hostport = f"{url.host()}:{url.port()}" if url.port() > 0 else url.host()
        if url.host() in hosts or hostport in hosts:
            self.message.setText("Signed in -- reading the channel name...")
            QTimer.singleShot(1200, self._read_account)

    def _read_account(self) -> None:
        if self._done or self._runner is not None:
            return
        from ..youtube.studio import FlowRunner
        # the "account" flow re-opens Studio's home (harmless) and reads the channel name
        self._runner = FlowRunner(self.page, "account", {}, view=self.view, pace_ms=0, parent=self)
        self._runner.finished.connect(lambda res: self._finish(res.get("account", "")))
        self._runner.failed.connect(self._on_failed)
        self._runner.start()

    def _on_failed(self, step: str, reason: str, kind: str) -> None:
        if kind == "cancelled":
            return
        if kind == "signed_out":
            self._runner = None                   # Studio bounced to the sign-in page: keep waiting
            return
        self._finish("")                          # in Studio, the name just couldn't be read

    def _finish(self, name: str) -> None:
        if self._done:
            return
        self._done = True
        s = config_module.load()
        s.youtube.account_name = name or s.youtube.account_name or "Signed in"
        config_module.save(s)
        self.signed_in.emit(s.youtube.account_name)
        QTimer.singleShot(300, self.close)

    def closeEvent(self, event) -> None:
        if self._runner is not None and self._runner.is_running():
            self._runner.cancel()
        if not self._done:
            self.cancelled.emit()
        super().closeEvent(event)
