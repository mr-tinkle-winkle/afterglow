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

import subprocess
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
