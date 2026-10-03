"""
The one YouTube Studio window: a web view in the shared YouTube profile with a slim banner on top.

It runs **hidden** while an upload / edit / delete is automated: the widget is shown with
Qt.WA_DontShowOnScreen, so Qt -- and therefore Chromium -- treat it as visible (the page lays out,
isn't throttled as a background tab, and takes the one real click the file picker needs) while
nothing is mapped on screen.  ``pop_out()`` puts it on screen (review before Save, a failure to
finish by hand, a click on the red indicator circle, sign-in); ``tuck_away()`` hides it again
without stopping anything.  Closing it with the window's X only tucks it away while work is
running -- the upload lives in this page.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import config as config_module
from .custom_button import CustomButton
from .theme import Theme


class StudioWindow(QWidget):
    action = Signal(str)          # banner button pressed: "hide" | "retry" | "finished" | "cancel"
    closed_by_user = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from ..youtube.studio import StudioPage
        self.setWindowTitle("YouTube Studio -- afterglow")
        self.resize(1280, 900)
        appearance = config_module.load_readonly().appearance
        theme = Theme(appearance)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.banner = QWidget()
        self.banner.setAutoFillBackground(True)
        pal = self.banner.palette()
        pal.setColor(QPalette.Window, theme.library_background())
        pal.setColor(QPalette.WindowText, QColor(appearance.card_text_color))
        self.banner.setPalette(pal)
        brow = QHBoxLayout(self.banner)
        brow.setContentsMargins(12, 8, 12, 8)
        brow.setSpacing(8)
        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        self.message.setPalette(pal)
        brow.addWidget(self.message, 1)
        self.buttons: "dict[str, CustomButton]" = {}
        for key, text in (("retry", "Retry"), ("finished", "I finished it here"), ("cancel", "Cancel"),
                          ("hide", "Hide")):
            b = CustomButton(text)
            b.setMinimumHeight(30)
            b.setMinimumWidth(90)
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            brow.addWidget(b)
            self.buttons[key] = b
        lay.addWidget(self.banner)

        self.view = QWebEngineView(self)
        self.page = StudioPage(self.view)
        self.view.setPage(self.page)
        lay.addWidget(self.view, 1)
        self._mode = "idle"
        self.set_banner("", ())
        self._busy = False

    # ------------------------------------------------------------------ banner

    def set_banner(self, text: str, buttons=("hide",)) -> None:
        self.message.setText(text)
        for k, b in self.buttons.items():
            b.setVisible(k in buttons)

    def set_busy(self, busy: bool) -> None:
        """Busy = an automated flow (or a job waiting on the user) lives in this page."""
        self._busy = busy

    # ------------------------------------------------------------------ showing

    def is_on_screen(self) -> bool:
        return self.isVisible() and not self.testAttribute(Qt.WA_DontShowOnScreen)

    def run_hidden(self) -> None:
        if self.isVisible() and self.testAttribute(Qt.WA_DontShowOnScreen):
            return
        if self.isVisible():
            self.hide()
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.show()

    def pop_out(self) -> None:
        if self.is_on_screen():
            self.raise_()
            self.activateWindow()
            return
        if self.isVisible():
            self.hide()
        self.setAttribute(Qt.WA_DontShowOnScreen, False)
        self.show()
        self.raise_()
        self.activateWindow()

    def tuck_away(self) -> None:
        if self._busy:
            self.run_hidden()
        else:
            self.hide()
            self.setAttribute(Qt.WA_DontShowOnScreen, False)

    def closeEvent(self, event) -> None:
        if self._busy:
            event.ignore()
            self.tuck_away()
            self.closed_by_user.emit()
            return
        super().closeEvent(event)

    def load(self, url: str) -> None:
        self.page.load(QUrl(url))
