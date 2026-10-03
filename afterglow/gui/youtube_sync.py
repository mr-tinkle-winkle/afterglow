"""
Renaming / changing the filters of an uploaded clip in afterglow asks "Also update it on
YouTube?" -- yes updates the title and the description (re-expanded from the template, so the
hashtags follow the filters) through Studio's edit page, hidden, via the upload queue.

Hooked into library.metadata_listeners (GUI process only).  Changes are collected and asked about
once, a moment after the last one and never while a menu / popup is open: ticking four filters in
the card's Filters menu is one question, not four.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from .. import config as config_module, library
from ..youtube import TITLE_MAX, DESCRIPTION_MAX
from ..youtube import template as template_module

DEBOUNCE_MS = 1500


class YouTubeSync(QObject):
    def __init__(self, parent_widget=None):
        super().__init__(parent_widget)
        self._parent_widget = parent_widget
        self._pending: "dict[int, set[str]]" = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._ask)
        self.enabled = True
        library.metadata_listeners.append(self._on_change)

    def detach(self) -> None:
        try:
            library.metadata_listeners.remove(self._on_change)
        except ValueError:
            pass

    def _on_change(self, video_id: int, kind: str) -> None:
        if not self.enabled:
            return
        try:
            v = library.get_video(video_id)
        except library.LibraryError:
            return
        if not v.is_uploaded:
            return
        self._pending.setdefault(video_id, set()).add(kind)
        self._timer.start()

    @staticmethod
    def new_metadata(v, kinds: "set[str]") -> "tuple[str, str]":
        tpl = config_module.load_readonly().youtube.description_template
        title = v.title if "title" in kinds else (v.youtube_title or v.title)
        title = template_module.sanitize_for_youtube(title, max_chars=TITLE_MAX)
        desc = template_module.sanitize_for_youtube(template_module.describe(v, tpl), max_bytes=DESCRIPTION_MAX)
        return title, desc

    def _ask(self) -> None:
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            self._timer.start()                  # a menu / dialog is still open: ask once it closes
            return
        pending, self._pending = self._pending, {}
        items = []
        for vid, kinds in pending.items():
            try:
                v = library.get_video(vid)
            except library.LibraryError:
                continue
            if v.is_uploaded:
                items.append((v, kinds))
        if not items:
            return
        from .custom_message_dialog import ask_confirm
        if len(items) == 1:
            v, kinds = items[0]
            what = " and ".join(sorted({"title": "the title", "tags": "the filters"}[k] for k in kinds))
            text = (f"You changed {what} of “{v.title}”, which is on YouTube. Also update it on YouTube? "
                    "(The description is rebuilt from your template, so its hashtags follow the filters.)")
        else:
            text = (f"You changed {len(items)} clips that are on YouTube. Also update their titles and "
                    "descriptions on YouTube?")
        if not ask_confirm(self._parent_widget, "Update on YouTube?", text, "Update on YouTube"):
            return
        from .upload_queue import upload_queue
        for v, kinds in items:
            title, desc = self.new_metadata(v, kinds)
            upload_queue().enqueue_edit(v.id, title, desc)
