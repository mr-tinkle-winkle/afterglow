"""
The quick Upload dialog: everything prefilled, everything editable, then the upload runs in the
background (gui/upload_queue.py) and this dialog closes.

One clip: title (= the clip's title), description (from Settings > YouTube's template), playlist
(from the clip type), visibility (default from Settings).  Several (multi-select): ONE dialog
listing every clip, each row editable, uploaded one after another.

"Upload in browser instead" is the fallback for when the embedded sign-in is refused or Studio
can't be automated: Studio's upload page opens in the system browser, the file is shown in the
file manager, title / description are one click from the clipboard, and the finished video's
link is pasted back here to register it (``FallbackUploadDialog``).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import (QDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                               QVBoxLayout, QWidget)

from .. import config as config_module, library, clips, thumbnails
from ..youtube import PRIVACY_CHOICES, TITLE_MAX, DESCRIPTION_MAX, UPLOAD_URL, parse_video_id
from ..youtube import template as template_module
from .custom_button import CustomButton
from .custom_combo_box import CustomComboBox
from .custom_line_edit import CustomLineEdit
from .custom_message_dialog import show_message
from .themed_dialogs import ThemedDialog
from .youtube_settings import themed_text_edit

THUMB = QSize(160, 90)


def _thumb_pixmap(video) -> QPixmap:
    path = None
    if video.has_local_file:
        try:
            path = thumbnails.get_thumbnail(video.id, Path(video.path))
        except Exception:  # noqa: BLE001
            path = None
    pm = QPixmap(str(path)) if path else QPixmap()
    if pm.isNull():
        pm = QPixmap(THUMB)
        pm.fill(Qt.black)
    return pm.scaled(THUMB, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation).copy(0, 0, THUMB.width(), THUMB.height())


def known_playlists() -> "list[str]":
    names = []
    for cfg in clips.list_clip_configs():
        if cfg.youtube_playlist and cfg.youtube_playlist not in names:
            names.append(cfg.youtube_playlist)
    return names


def defaults_for(video, settings=None) -> dict:
    """The prefilled upload values for one clip."""
    s = settings or config_module.load_readonly()
    playlist = ""
    if video.clip_config_id is not None:
        try:
            playlist = clips.get_clip_config(video.clip_config_id).youtube_playlist
        except Exception:  # noqa: BLE001
            playlist = ""
    return {
        "video_id": video.id,
        "title": template_module.sanitize_for_youtube(video.title, max_chars=TITLE_MAX),
        "description": template_module.sanitize_for_youtube(
            template_module.describe(video, s.youtube.description_template), max_bytes=DESCRIPTION_MAX),
        "playlist": playlist,
        "privacy": s.youtube.default_privacy or "unlisted",
    }


class _ClipRow(QFrame):
    """One clip's editable upload fields."""

    def __init__(self, video, values: dict, appearance, compact: bool, parent=None):
        super().__init__(parent)
        self.video = video
        self.video_id = video.id
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 6, 0, 6)
        grid.setHorizontalSpacing(12)
        thumb = QLabel()
        thumb.setPixmap(_thumb_pixmap(video))
        thumb.setFixedSize(THUMB)
        grid.addWidget(thumb, 0, 0, 3 if compact else 4, 1, Qt.AlignTop)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.title_edit = CustomLineEdit(values["title"])
        self.title_edit.setMinimumWidth(360)
        self.title_edit.setMaxLength(TITLE_MAX)
        form.addRow("Title:", self.title_edit)
        self.desc_edit = themed_text_edit(appearance, values["description"], rows=3 if compact else 6)
        form.addRow("Description:", self.desc_edit)
        sub = QHBoxLayout()
        self.playlist_edit = CustomLineEdit(values["playlist"])
        self.playlist_edit.setPlaceholderText("(no playlist)")
        self.playlist_edit.setToolTip("The playlist's name exactly as YouTube Studio lists it")
        lists = known_playlists()
        if lists:
            from PySide6.QtWidgets import QCompleter
            comp = QCompleter(lists, self.playlist_edit)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            self.playlist_edit.setCompleter(comp)
        sub.addWidget(QLabel("Playlist:"))
        sub.addWidget(self.playlist_edit, 1)
        self.privacy_combo = CustomComboBox()
        for key, label in PRIVACY_CHOICES:
            self.privacy_combo.addItem(label, key)
        self.privacy_combo.setCurrentIndex(max(0, self.privacy_combo.findData(values["privacy"])))
        sub.addWidget(QLabel("Visibility:"))
        sub.addWidget(self.privacy_combo)
        form.addRow("", sub)
        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        self.problem_label.setTextFormat(Qt.PlainText)
        self.problem_label.setStyleSheet("QLabel { color: #ff8a8a; }")
        form.addRow("", self.problem_label)
        grid.addLayout(form, 0, 1, 4, 1)
        grid.setColumnStretch(1, 1)
        self.title_edit.textChanged.connect(self.check)
        self.desc_edit.textChanged.connect(self.check)
        if not video.has_local_file:
            self.problem_label.setText("This clip's file isn't in the library any more.")
        else:
            self.check()

    def values(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": template_module.sanitize_for_youtube(self.title_edit.text().strip(), max_chars=TITLE_MAX),
            "description": template_module.sanitize_for_youtube(self.desc_edit.toPlainText(), max_bytes=DESCRIPTION_MAX),
            "playlist": self.playlist_edit.text().strip(),
            "privacy": self.privacy_combo.currentData() or "unlisted",
        }

    def check(self) -> "list[str]":
        probs = template_module.problems(self.title_edit.text(), self.desc_edit.toPlainText())
        if not self.video.has_local_file:
            probs.insert(0, "This clip's file isn't in the library any more.")
        self.problem_label.setText(" ".join(probs))
        return [p for p in probs if "will be replaced" not in p]


class UploadDialog(ThemedDialog):
    """``exec()`` -> Accepted with ``requests()`` (for the queue), or Rejected.  ``fallback_chosen``
    is True when the user picked "Upload in browser instead"."""

    def __init__(self, videos: list, parent=None):
        n = len(videos)
        super().__init__("Upload to YouTube" if n == 1 else f"Upload {n} clips to YouTube", parent)
        settings = config_module.load_readonly()
        a = settings.appearance
        self.fallback_chosen = False
        self.videos = videos
        acct = settings.youtube.account_name
        self.account_label = self.label(
            f"Uploads as: {acct}" if acct else
            "Not signed in to YouTube yet -- the first upload will ask you to sign in (or sign in under "
            "Settings > YouTube).", dim=True)
        self.lay.addWidget(self.account_label)

        self.rows: "list[_ClipRow]" = []
        compact = n > 1
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        for v in videos:
            row = _ClipRow(v, defaults_for(v, settings), a, compact)
            self.rows.append(row)
            hl.addWidget(row)
        hl.addStretch(1)
        if compact:
            all_row = QHBoxLayout()
            all_row.addWidget(self.label("Visibility for all:"))
            self.all_privacy = CustomComboBox()
            self.all_privacy.addItem("(per clip)", "")
            for key, label in PRIVACY_CHOICES:
                self.all_privacy.addItem(label, key)
            self.all_privacy.currentIndexChanged.connect(self._apply_all_privacy)
            all_row.addWidget(self.all_privacy)
            all_row.addStretch(1)
            self.lay.addLayout(all_row)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(holder)
            holder.setAutoFillBackground(False)
            scroll.viewport().setAutoFillBackground(False)
            scroll.setMinimumHeight(min(560, 190 * n))
            self.lay.addWidget(scroll, 1)
        else:
            self.lay.addWidget(holder)

        row = QHBoxLayout()
        browser = CustomButton("Upload in browser instead...")
        browser.setToolTip("Open Studio's upload page in your own browser and register the link afterwards")
        browser.clicked.connect(self._choose_fallback)
        row.addWidget(browser)
        row.addStretch(1)
        cancel = CustomButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = CustomButton("Upload" if n == 1 else f"Upload {n}")
        ok.clicked.connect(self._accept)
        for b in (browser, cancel, ok):
            b.setMinimumHeight(30)
        cancel.setMinimumWidth(90)
        ok.setMinimumWidth(110)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.ok_button = ok
        self.lay.addLayout(row)
        self.setMinimumWidth(820)
        if self.rows:
            self.rows[0].title_edit.setFocus()

    def _apply_all_privacy(self) -> None:
        key = self.all_privacy.currentData()
        if key:
            for r in self.rows:
                r.privacy_combo.setCurrentIndex(max(0, r.privacy_combo.findData(key)))

    def _accept(self) -> None:
        bad = [(r, r.check()) for r in self.rows]
        bad = [(r, p) for r, p in bad if p]
        if bad:
            r, probs = bad[0]
            show_message(self, "Can't Upload Yet", f"“{r.title_edit.text() or r.video.title}”: {' '.join(probs)}")
            return
        self.accept()

    def _choose_fallback(self) -> None:
        self.fallback_chosen = True
        self.accept()

    def requests(self) -> "list[dict]":
        return [r.values() for r in self.rows]


class FallbackUploadDialog(ThemedDialog):
    """Upload by hand in the system browser, then register the link: the video is then verified
    and recorded exactly like an automated upload (and the local file trashed if that's on)."""

    def __init__(self, requests: "list[dict]", parent=None):
        super().__init__("Upload in Your Browser", parent)
        self.requests_ = requests
        self.lay.addWidget(self.label(
            "1. Open YouTube Studio's upload page.  2. Drop the file in (“Show file” opens its folder).  "
            "3. Paste the title and description with the Copy buttons.  4. When Studio shows the video's "
            "link, paste it here and press Register."))
        top = QHBoxLayout()
        open_btn = CustomButton("Open Studio's upload page")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(UPLOAD_URL)))
        top.addWidget(open_btn)
        top.addStretch(1)
        self.lay.addLayout(top)
        self.link_edits: "dict[int, CustomLineEdit]" = {}
        grid = QGridLayout()
        for i, req in enumerate(requests):
            v = library.get_video(req["video_id"])
            grid.addWidget(self.label(req["title"]), i, 0)
            for col, (text, fn) in enumerate((
                ("Show file", lambda _=False, p=v.path: self._show_file(p)),
                ("Copy title", lambda _=False, t=req["title"]: QGuiApplication.clipboard().setText(t)),
                ("Copy description", lambda _=False, d=req["description"]: QGuiApplication.clipboard().setText(d)),
            ), start=1):
                b = CustomButton(text)
                b.clicked.connect(fn)
                grid.addWidget(b, i, col)
            edit = CustomLineEdit("")
            edit.setPlaceholderText("https://youtu.be/...")
            edit.setMinimumWidth(240)
            grid.addWidget(edit, i, 4)
            self.link_edits[req["video_id"]] = edit
        self.lay.addLayout(grid)
        row, ok = self.button_row("Register", "Close")
        ok.clicked.disconnect()
        ok.clicked.connect(self._register)
        self.lay.addLayout(row)
        self.setMinimumWidth(900)
        self.registered: "list[dict]" = []

    @staticmethod
    def _show_file(path: str) -> None:
        p = Path(path)
        try:
            import subprocess, shutil
            if shutil.which("dbus-send"):
                uri = QUrl.fromLocalFile(str(p)).toString()
                r = subprocess.run(["dbus-send", "--session", "--print-reply", "--dest=org.freedesktop.FileManager1",
                                    "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1.ShowItems",
                                    f"array:string:{uri}", "string:"], capture_output=True, timeout=3)
                if r.returncode == 0:
                    return
        except Exception:  # noqa: BLE001
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent)))

    def _register(self) -> None:
        out, bad = [], []
        for req in self.requests_:
            text = self.link_edits[req["video_id"]].text().strip()
            if not text:
                continue
            yt = parse_video_id(text)
            if not yt:
                bad.append(text)
                continue
            out.append(dict(req, yt_id=yt))
        if bad:
            show_message(self, "Not a YouTube Link", "These don't look like YouTube video links:\n" + "\n".join(bad))
            return
        if not out:
            show_message(self, "Nothing to Register", "Paste at least one video's link first.")
            return
        self.registered = out
        self.accept()


def start_upload(videos: list, parent=None) -> bool:
    """The whole entry point the Library / previewer / Editor call: the quick dialog, then the queue
    (or the browser fallback).  Returns True if anything was queued / registered."""
    from .upload_queue import upload_queue
    videos = [v for v in videos if v is not None]
    if not videos:
        return False
    already = [v for v in videos if v.is_uploaded]
    if already and len(already) == len(videos):
        show_message(parent, "Already Uploaded",
                     "Already on YouTube." if len(videos) == 1 else "All of these are already on YouTube.")
        return False
    videos = [v for v in videos if not v.is_uploaded and v.has_local_file]
    if not videos:
        return False
    dlg = UploadDialog(videos, parent)
    if dlg.exec() != QDialog.Accepted:
        return False
    reqs = dlg.requests()
    if dlg.fallback_chosen:
        fb = FallbackUploadDialog(reqs, parent)
        if fb.exec() != QDialog.Accepted:
            return False
        for r in fb.registered:
            upload_queue().register_manual(r)
        return bool(fb.registered)
    upload_queue().enqueue_uploads(reqs)
    return True
