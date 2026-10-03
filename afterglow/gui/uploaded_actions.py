"""
What the Uploaded tab's cards can do (right-click menu and the card's own buttons): Copy link,
Open on YouTube, Edit in Studio, Delete from YouTube (confirmed here first, then automated in
Studio through the upload queue), Remove from afterglow (the local record only; YouTube is not
touched).  Plus the upload badge text a card shows over its thumbnail.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication

from .. import library
from ..youtube import studio_edit_url, watch_url


def copy_links(videos) -> None:
    links = [watch_url(v.youtube_video_id) for v in videos if v.youtube_video_id]
    if links:
        QGuiApplication.clipboard().setText("\n".join(links))


def open_on_youtube(video) -> None:
    if video.youtube_video_id:
        QDesktopServices.openUrl(QUrl(watch_url(video.youtube_video_id)))


_edit_windows: list = []


def edit_in_studio(video, parent=None) -> None:
    """That video's Studio edit page (opened by the YouTube host process)."""
    if not video.youtube_video_id:
        return
    from .upload_queue import upload_queue
    upload_queue().edit_in_studio(video.id)


def open_studio_editor(video) -> None:
    """(YouTube host process) the edit page in its own window -- the queue's hidden window may be
    busy uploading.  Changes made there are YouTube's; afterglow's record isn't rewritten."""
    from .studio_window import StudioWindow
    w = StudioWindow()
    w.setAttribute(Qt.WA_DeleteOnClose, True)
    w.set_banner(f"Editing \u201c{video.youtube_title or video.title}\u201d in YouTube Studio. Changes made here "
                 "stay on YouTube (afterglow's own title and filters are not changed).", ("hide",))
    w.action.connect(lambda _a: w.close())
    w.load(studio_edit_url(video.youtube_video_id))
    w.pop_out()
    _edit_windows.append(w)
    w.destroyed.connect(lambda *_: _edit_windows.remove(w) if w in _edit_windows else None)


def delete_from_youtube(videos, parent=None) -> bool:
    from .custom_message_dialog import ask_confirm
    from .upload_queue import upload_queue
    videos = [v for v in videos if v.youtube_video_id]
    if not videos:
        return False
    if len(videos) == 1:
        text = (f"Delete “{videos[0].youtube_title or videos[0].title}” from YouTube? It is deleted "
                "forever on YouTube (afterglow does it through YouTube Studio).")
    else:
        text = f"Delete {len(videos)} videos from YouTube? They are deleted forever on YouTube."
    if videos and any(v.has_local_file for v in videos):
        text += " Clips that still have their local file stay in Local."
    if not ask_confirm(parent, "Delete from YouTube", text, "Delete from YouTube", danger=True):
        return False
    for v in videos:
        upload_queue().enqueue_delete(v.id)
    return True


def remove_from_afterglow(videos, parent=None) -> bool:
    from .custom_message_dialog import ask_confirm
    videos = list(videos)
    if not videos:
        return False
    n = len(videos)
    text = ("Remove " + (f"“{videos[0].youtube_title or videos[0].title}”" if n == 1 else f"{n} videos")
            + " from afterglow's Uploaded list? Nothing changes on YouTube; a clip that still has its local "
              "file stays in Local.")
    if not ask_confirm(parent, "Remove from afterglow", text, "Remove"):
        return False
    for v in videos:
        library.forget_youtube(v.id)
    return True


def badge_for(video, uploaded_view: bool, job=None) -> "tuple[str, str]":
    """(text, kind) of the small badge over a card's thumbnail; kind is "" | "info" | "busy" | "error"."""
    state = video.upload_state
    if job is not None and not job.finished:
        if job.kind == "delete":
            return "Deleting from YouTube…", "busy"
        if job.kind == "edit":
            return "Updating on YouTube…", "busy"
        if job.state == "queued":
            return "Queued for YouTube", "busy"
        if job.state == "waiting_user":
            return "Upload needs you", "error"
        if job.state == "verifying":
            return "Processing on YouTube…", "busy"
        if job.percent >= 0:
            return f"Uploading {job.percent}%", "busy"
        return "Uploading…", "busy"
    if state == "failed":
        return "Upload failed", "error"
    if state in ("queued", "uploading", "processing"):
        return "Uploading…", "busy"
    if video.is_uploaded:
        if uploaded_view:
            label = {"unlisted": "Unlisted", "public": "Public", "private": "Private"}.get(video.youtube_privacy or "", "")
            return label, "info" if label else ""
        return "On YouTube", "info"
    return "", ""
