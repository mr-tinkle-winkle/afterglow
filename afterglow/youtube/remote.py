"""
Public, signed-out checks of an uploaded video (no API, no account): run off the GUI thread.

``oembed(video_id)``  YouTube's oEmbed endpoint answers for public AND unlisted videos once they are
                      watchable, never for private ones -- the "it really plays" check made before
                      the local file is moved to the trash.
``fetch_thumbnail``   https://i.ytimg.com/vi/<id>/hqdefault.jpg, cached for the Uploaded tab (public
                      for unlisted videos too).  Until YouTube has processed the video this is a
                      404 (or a tiny gray placeholder), so it doubles as a processing check.

Base URLs can be overridden for tests: AFTERGLOW_YT_OEMBED (a format string with {url}) and
AFTERGLOW_YT_THUMB (with {id}).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "afterglow" / "youtube_thumbs"
TIMEOUT = 15
MIN_REAL_THUMB_BYTES = 2500      # YouTube's "not ready" placeholder is ~1 KB of gray
_UA = "Mozilla/5.0 (X11; Linux x86_64) afterglow"


def _oembed_url(video_id: str) -> str:
    watch = f"https://www.youtube.com/watch?v={video_id}"
    tpl = os.environ.get("AFTERGLOW_YT_OEMBED") or "https://www.youtube.com/oembed?url={url}&format=json"
    return tpl.format(url=urllib.parse.quote(watch, safe=""))


def _thumb_url(video_id: str) -> str:
    tpl = os.environ.get("AFTERGLOW_YT_THUMB") or "https://i.ytimg.com/vi/{id}/hqdefault.jpg"
    return tpl.format(id=video_id)


def _get(url: str) -> "tuple[int, bytes]":
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except (urllib.error.URLError, OSError, ValueError):
        return 0, b""


def oembed(video_id: str) -> "dict | None":
    """The oEmbed record (title, thumbnail_url, ...) or None (not watchable yet / private / offline)."""
    status, body = _get(_oembed_url(video_id))
    if status != 200:
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def fetch_thumbnail(video_id: str) -> "Path | None":
    """Download (or reuse) the cached thumbnail; None until YouTube has a real one."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / f"{video_id}.jpg"
    status, body = _get(_thumb_url(video_id))
    if status == 200 and len(body) >= MIN_REAL_THUMB_BYTES:
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(body)
        tmp.replace(dest)
        return dest
    return dest if dest.exists() else None
