"""
YouTube upload, without the YouTube Data API.

Why no API: ``videos.insert`` from a Google Cloud project that has not passed YouTube's compliance
audit locks every upload to *private*, permanently (it can't be switched to unlisted later), and
unlisted uploads are the whole point.  So afterglow drives **YouTube Studio's own upload page** in
an embedded QtWebEngine view, signed in once from Settings, exactly as a person would click
through it, and keeps its own local record of the result.

Modules
  template.py      the description template (``{filters}`` -> ``#hashtags`` etc.); pure
  trash.py         freedesktop trash (``gio trash``, or the spec directly); pure
  studio_steps.json  every Studio selector / step definition -- a Studio redesign is a one-file fix
  steps.py         loads studio_steps.json; pure
  profile.py       the persistent web profile + the sign-in user-agent fix (QtWebEngine)
  studio.py        the step machine that drives a Studio page (QtWebEngine, no widgets)
  remote.py        oEmbed check + thumbnail fetch (urllib, run off the GUI thread)
  queue.py         the upload queue: one upload at a time, sounds, the red indicator circle
  gui_socket.py    the GUI's tiny socket the indicator helper uses to pop a Studio window back out

Nothing here imports Qt at package import time.
"""
from __future__ import annotations

PRIVACY_CHOICES = (("unlisted", "Unlisted"), ("public", "Public"), ("private", "Private"))
TITLE_MAX = 100            # YouTube's limit, characters
DESCRIPTION_MAX = 5000     # YouTube's limit, bytes (UTF-8)
STUDIO_URL = "https://studio.youtube.com/"
UPLOAD_URL = "https://www.youtube.com/upload"


def watch_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def embed_url(video_id: str) -> str:
    return f"https://www.youtube.com/embed/{video_id}?autoplay=1&rel=0"


def studio_edit_url(video_id: str) -> str:
    return f"https://studio.youtube.com/video/{video_id}/edit"


def thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def parse_video_id(text: str) -> "str | None":
    """A YouTube video id out of anything a user might paste (youtu.be / watch?v= / shorts / embed /
    studio links, or a bare 11-character id)."""
    import re
    text = (text or "").strip()
    pats = (
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"/(?:shorts|embed|live|video)/([A-Za-z0-9_-]{11})",
    )
    for pat in pats:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    return None
