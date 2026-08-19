"""
Trim engine.

Two modes:
- fast (default): ffmpeg stream-copy, seeking with -ss before -i. This snaps
  to the nearest keyframe at or before the requested start, so it's near-
  instant but not sample-accurate. Good enough for "cut the dead air".
- frame_perfect: ffmpeg re-encodes, seeking with -ss after -i so every frame
  is decoded and the cut lands exactly where asked. Slow (real-time-ish or
  worse depending on codec/hardware), but exact. This is the opt-in path
  from the editor's "Frame Perfect Accuracy" toggle.

Undo model (per the spec: one committed edit, one level of undo):
- Before the FIRST edit on a video, we copy the current file to a backup
  path and record it in videos.backup_path.
- Every subsequent edit on that video overwrites the working file directly
  (the backup stays pointed at the ORIGINAL, pre-any-edit file) -- so undo
  always means "go back to how it was before I started editing", not a
  multi-step undo stack.
- Calling undo() restores from backup, deletes the backup, and clears
  has_edit/backup_path.
"""
from __future__ import annotations

import shutil
import subprocess
import json
from dataclasses import dataclass
from pathlib import Path


class EditorError(RuntimeError):
    pass


def probe_duration(path: Path) -> float:
    """Return duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise EditorError(f"ffprobe failed on {path}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


@dataclass
class TrimRequest:
    video_path: Path
    start_sec: float
    end_sec: float
    frame_perfect: bool = False

    def validate(self, duration: float) -> None:
        if self.start_sec < 0:
            raise EditorError("Start time can't be negative.")
        if self.end_sec <= self.start_sec:
            raise EditorError("End time must be after start time.")
        if self.end_sec > duration + 0.05:  # small tolerance for float/probe drift
            raise EditorError(f"End time {self.end_sec}s exceeds video duration {duration:.2f}s.")


def _backup_path_for(video_path: Path) -> Path:
    return video_path.with_name(video_path.stem + ".orig" + video_path.suffix)


def commit_trim(request: TrimRequest, has_prior_edit: bool, existing_backup: Path | None) -> Path:
    """
    Perform the trim in place (working file at request.video_path is
    replaced by the trimmed result). Returns the backup path that should be
    stored in the DB (created fresh on first edit, unchanged on subsequent
    edits).

    Caller (library.py) is responsible for updating the DB row.
    """
    video_path = request.video_path
    duration = probe_duration(video_path)
    request.validate(duration)

    # Only back up on the FIRST edit -- subsequent edits should not
    # overwrite the backup with an already-edited version.
    if has_prior_edit and existing_backup and existing_backup.exists():
        backup_path = existing_backup
    else:
        backup_path = _backup_path_for(video_path)
        shutil.copy2(video_path, backup_path)

    tmp_output = video_path.with_name(video_path.stem + ".trim_tmp" + video_path.suffix)
    duration_arg = request.end_sec - request.start_sec

    if request.frame_perfect:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-ss", f"{request.start_sec}",
            "-t", f"{duration_arg}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            str(tmp_output),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{request.start_sec}",
            "-i", str(video_path),
            "-t", f"{duration_arg}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            str(tmp_output),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not tmp_output.exists():
        if tmp_output.exists():
            tmp_output.unlink()
        raise EditorError(f"ffmpeg trim failed:\n{result.stderr[-2000:]}")

    tmp_output.replace(video_path)
    return backup_path


def undo_trim(video_path: Path, backup_path: Path) -> None:
    if not backup_path.exists():
        raise EditorError(f"No backup found at {backup_path} -- can't undo.")
    shutil.move(str(backup_path), str(video_path))


if __name__ == "__main__":
    # Smoke test: generate a 10s test clip with ffmpeg's testsrc, trim it
    # both ways, and confirm the durations come out right.
    import tempfile

    tmpdir = Path(tempfile.mkdtemp())
    src = tmpdir / "test.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=10:size=320x240:rate=30",
         "-c:v", "libx264", "-g", "30", str(src)],
        capture_output=True, check=True,
    )
    print(f"Source duration: {probe_duration(src):.3f}s")

    # Fast trim
    fast_copy = tmpdir / "fast.mp4"
    shutil.copy2(src, fast_copy)
    req = TrimRequest(video_path=fast_copy, start_sec=2.0, end_sec=7.0, frame_perfect=False)
    commit_trim(req, has_prior_edit=False, existing_backup=None)
    print(f"Fast trim result duration: {probe_duration(fast_copy):.3f}s (requested 5.0s, may drift to keyframe)")

    # Frame-perfect trim
    fp_copy = tmpdir / "fp.mp4"
    shutil.copy2(src, fp_copy)
    req2 = TrimRequest(video_path=fp_copy, start_sec=2.0, end_sec=7.0, frame_perfect=True)
    commit_trim(req2, has_prior_edit=False, existing_backup=None)
    print(f"Frame-perfect trim result duration: {probe_duration(fp_copy):.3f}s (requested 5.0s, should be exact)")

    print(f"\nTest files in {tmpdir}")
