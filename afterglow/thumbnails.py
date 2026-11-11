"""
Video thumbnails for the Library grid: first frame, extracted via ffmpeg,
cached to disk so we're not re-decoding on every page visit.

Cache key includes the source file's mtime, not just the video id -- so a
trim (which replaces the file in place and changes its mtime) automatically
invalidates the old thumbnail without needing an explicit "clear cache"
call anywhere in library.py's edit/undo paths. Stale thumbnails from
before an edit are left on disk (harmless, just unused) rather than hunted
down and deleted -- simpler, and cache directories are cheap.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "afterglow" / "thumbnails"


def get_thumbnail(video_id: int, video_path: Path) -> Path | None:
    """
    Returns a path to a cached (or freshly generated) JPEG thumbnail for
    the given video, or None if ffmpeg couldn't produce one (e.g. a
    corrupt or zero-frame file) -- callers should fall back to a generic
    placeholder icon in that case rather than treating it as fatal.
    """
    if not video_path.exists():
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    mtime = int(video_path.stat().st_mtime)
    cache_path = CACHE_DIR / f"{video_id}_{mtime}.jpg"
    if cache_path.exists():
        return cache_path

    # -ss before -i for a fast keyframe-ish seek near the start; 0.1s in
    # rather than exactly 0 since frame 0 is occasionally solid black for
    # some encoders/scenes.
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-ss", "0.1", "-i", str(video_path),
            "-frames:v", "1", "-q:v", "4", str(cache_path),
        ],
        capture_output=True,
    )
    if result.returncode != 0 or not cache_path.exists():
        # Retry at 0.0s -- some very short clips don't have a frame at 0.1s.
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video_path),
                "-frames:v", "1", "-q:v", "4", str(cache_path),
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not cache_path.exists():
            return None

    return cache_path


# ---------------------------------------------------------------- card thumbnails
#
# The Library grid shows every thumbnail at 400x224. Decoding the full-size
# JPEG above for that (2560x1440 for a 1440p clip) cost ~15 ms per card on
# every app start -- measured, the biggest part of filling a warm Library.
# The grid reads a small copy instead; everything that needs the full frame
# (the upload dialog, the YouTube thumbnail, the Editor's browser) keeps
# using get_thumbnail().

CARD_THUMB_WIDTH = 640


def _mtime_key(video_path: Path) -> "int | None":
    try:
        return int(video_path.stat().st_mtime)
    except OSError:
        return None


def card_thumbnail_path(video_id: int, video_path: Path) -> "Path | None":
    """Where this video's small card thumbnail lives (whether or not it has
    been made yet). None when the video file is gone."""
    mtime = _mtime_key(video_path)
    if mtime is None:
        return None
    return CACHE_DIR / f"{video_id}_{mtime}_card.jpg"


def full_thumbnail_path_if_cached(video_id: int, video_path: Path) -> "Path | None":
    """The full-size thumbnail if get_thumbnail() made one earlier (a small
    one can be scaled from it without running ffmpeg)."""
    mtime = _mtime_key(video_path)
    if mtime is None:
        return None
    path = CACHE_DIR / f"{video_id}_{mtime}.jpg"
    return path if path.exists() else None


def get_card_thumbnail(video_id: int, video_path: Path) -> "Path | None":
    """The small (CARD_THUMB_WIDTH wide) thumbnail, made with ffmpeg when it
    isn't cached. Blocks for one ffmpeg run on a miss: call it off the GUI
    thread (gui/thumb_loader.py does) or from the daemon at capture time."""
    cache_path = card_thumbnail_path(video_id, video_path)
    if cache_path is None:
        return None
    if cache_path.exists():
        return cache_path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # written under a temporary name and renamed: the GUI and the daemon can
    # both be making the same one, and a reader must never see half a file
    tmp = cache_path.with_name(f".{cache_path.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
    scale = f"scale={CARD_THUMB_WIDTH}:-2:flags=fast_bilinear"
    try:
        for seek in (["-ss", "0.1"], []):   # 2nd try at 0: very short clips have no frame at 0.1 s
            result = subprocess.run(
                ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *seek, "-i", str(video_path),
                 "-an", "-sn", "-frames:v", "1", "-vf", scale, "-q:v", "4", str(tmp)],
                capture_output=True,
            )
            if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                os.replace(tmp, cache_path)
                return cache_path
        return None
    except OSError:
        return None
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    import tempfile

    tmpdir = Path(tempfile.mkdtemp())
    src = tmpdir / "test.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=30",
         "-c:v", "libx264", str(src)],
        capture_output=True, check=True,
    )
    thumb = get_thumbnail(999, src)
    print("Thumbnail generated at:", thumb, "exists:", thumb.exists() if thumb else None)

    # Second call should hit the cache (same mtime) -- verify it's instant
    # and returns the same path rather than regenerating.
    thumb2 = get_thumbnail(999, src)
    print("Second call same path (cache hit):", thumb == thumb2)
