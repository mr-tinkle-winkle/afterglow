"""
The local video library: adding clips, tagging, filtering, renaming, and
wiring the editor's trim/undo into the DB.

This module owns the DB<->filesystem relationship. editor.py knows nothing
about the database; library.py is the glue.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import db
from . import config
from .editor import (
    TrimRequest, commit_trim, undo_trim, clear_backup, probe_duration,
    EditorError,
)

# Video file extensions recognized by scan_and_ingest_new_videos(). Matches
# what OBS itself can produce for the replay buffer (mp4/mkv are the two
# realistic cases; the others are included for anyone who's changed their
# OBS output format or drags in clips from elsewhere).
KNOWN_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi"}


class LibraryError(RuntimeError):
    pass


@dataclass
class Video:
    id: int
    filename: str
    path: str
    title: str
    description: str
    duration_sec: float | None
    created_at: str
    clip_config_id: int | None
    has_edit: bool
    backup_path: str | None
    youtube_video_id: str | None
    youtube_privacy: str | None
    tags: list[str]

    @property
    def path_obj(self) -> Path:
        return Path(self.path)


def _row_to_video(row: sqlite3.Row, tags: list[str]) -> Video:
    return Video(
        id=row["id"], filename=row["filename"], path=row["path"],
        title=row["title"], description=row["description"],
        duration_sec=row["duration_sec"], created_at=row["created_at"],
        clip_config_id=row["clip_config_id"], has_edit=bool(row["has_edit"]),
        backup_path=row["backup_path"], youtube_video_id=row["youtube_video_id"],
        youtube_privacy=row["youtube_privacy"], tags=tags,
    )


def _tags_for_video(conn: sqlite3.Connection, video_id: int) -> list[str]:
    rows = conn.execute(
        """SELECT t.name FROM tags t
           JOIN video_tags vt ON vt.tag_id = t.id
           WHERE vt.video_id = ? ORDER BY t.name""",
        (video_id,),
    ).fetchall()
    return [r["name"] for r in rows]


# ---------------------------------------------------------------- add/import

def add_video(path: Path, title: str, description: str = "",
              clip_config_id: int | None = None) -> Video:
    if not path.exists():
        raise LibraryError(f"File does not exist: {path}")
    try:
        duration = probe_duration(path)
    except EditorError:
        duration = None

    now = datetime.now(timezone.utc).isoformat()
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO videos (filename, path, title, description, duration_sec,
                                    created_at, clip_config_id, has_edit)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (path.name, str(path), title, description, duration, now, clip_config_id),
        )
        video_id = cur.lastrowid
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        return _row_to_video(row, [])


# ---------------------------------------------------------------- read/list

def get_video(video_id: int) -> Video:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row is None:
            raise LibraryError(f"No video with id {video_id}")
        return _row_to_video(row, _tags_for_video(conn, video_id))


def list_videos(tag_filter: list[str] | None = None, uploaded_only: bool = False,
                 local_only: bool = False, search: str | None = None) -> list[Video]:
    """
    tag_filter: list of tag names, ANDed together (a video must have ALL of
    them) -- this matches "multiple filters can be applied at once" as an
    intersection, which is the more useful reading for finding a specific clip.
    search: case-insensitive substring match against title OR description.
    """
    with db.get_conn() as conn:
        query = "SELECT DISTINCT v.* FROM videos v"
        params: list = []
        conditions = []

        if tag_filter:
            # Require the video to have ALL requested tags via a count check.
            placeholders = ",".join("?" for _ in tag_filter)
            query += f"""
                JOIN video_tags vt ON vt.video_id = v.id
                JOIN tags t ON t.id = vt.tag_id AND t.name IN ({placeholders})
            """
            params.extend(tag_filter)

        if uploaded_only:
            conditions.append("v.youtube_video_id IS NOT NULL")
        if local_only:
            conditions.append("v.youtube_video_id IS NULL")
        if search:
            conditions.append("(v.title LIKE ? COLLATE NOCASE OR v.description LIKE ? COLLATE NOCASE)")
            like_term = f"%{search}%"
            params.extend([like_term, like_term])

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        if tag_filter:
            query += " GROUP BY v.id HAVING COUNT(DISTINCT t.id) = ?"
            params.append(len(tag_filter))

        query += " ORDER BY v.created_at DESC"

        rows = conn.execute(query, params).fetchall()
        return [_row_to_video(r, _tags_for_video(conn, r["id"])) for r in rows]


def all_known_tags() -> list[str]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
        return [r["name"] for r in rows]


# ---------------------------------------------------------------- edit metadata

def rename_video(video_id: int, title: str | None = None, description: str | None = None) -> Video:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row is None:
            raise LibraryError(f"No video with id {video_id}")
        new_title = title if title is not None else row["title"]
        new_desc = description if description is not None else row["description"]
        conn.execute("UPDATE videos SET title = ?, description = ? WHERE id = ?",
                     (new_title, new_desc, video_id))
        # Build the return value from THIS SAME connection/transaction,
        # not via get_video() (which opens a separate connection) -- a
        # separate connection can't see this transaction's write until it
        # commits, which only happens when this `with` block exits. Doing
        # that unearthed a real bug: rename_video() was silently returning
        # a Video with the OLD title even though the DB itself was
        # correctly updated, because get_video() was called before the
        # commit that its own fresh connection needed to see the change.
        updated_row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        tags = _tags_for_video(conn, video_id)
        return _row_to_video(updated_row, tags)


def delete_video(video_id: int, delete_file: bool = True) -> None:
    video = get_video(video_id)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    if delete_file:
        p = Path(video.path)
        if p.exists():
            p.unlink()
        backup = Path(video.backup_path) if video.backup_path else None
        if backup and backup.exists():
            backup.unlink()


def prune_missing_videos() -> list[int]:
    """
    Remove library entries whose underlying file no longer exists on disk
    (deleted outside the app, moved, drive unmounted, etc.) -- the
    library only ever gains entries otherwise (add_video on capture,
    manual import later), so without this, a video removed externally
    stays listed forever, unclickable/broken.

    Returns the ids of removed entries. Doesn't touch anything else on
    disk (a missing file has nothing to clean up) -- this only prunes the
    DB row and its tag associations (via ON DELETE CASCADE).
    """
    removed_ids = []
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM videos").fetchall()
        for row in rows:
            if not Path(row["path"]).exists():
                conn.execute("DELETE FROM videos WHERE id = ?", (row["id"],))
                removed_ids.append(row["id"])
    return removed_ids


def scan_and_ingest_new_videos() -> list[Video]:
    """
    Pick up video files that exist in the clips folder but aren't tracked
    in the DB yet -- e.g. dragged/dropped in manually, or copied over from
    elsewhere. Previously the library only ever gained entries through
    add_video() being called from the capture pipeline, so anything placed
    into the clips folder by other means was invisible to it forever.

    Skips the Edit Backups folder (those are .orig copies of tracked
    videos, not videos in their own right) and any file already present
    at that exact path in the DB. Ingested videos get their filename stem
    as a default title and no tags/clip_config -- same starting state a
    freshly-captured clip would have before the user names it.

    Returns the newly-added Video rows (empty if nothing new was found).
    """
    settings = config.load()
    clips_dir = settings.clips_path()
    if not clips_dir.is_dir():
        return []

    with db.get_conn() as conn:
        known_paths = {row["path"] for row in conn.execute("SELECT path FROM videos")}

    newly_added = []
    for entry in sorted(clips_dir.iterdir()):
        if entry.is_dir():
            continue  # implicitly skips the "Edit Backups" folder itself
        if entry.suffix.lower() not in KNOWN_VIDEO_EXTENSIONS:
            continue
        if entry.name.endswith(".trim_tmp" + entry.suffix):
            continue  # ffmpeg's in-progress trim output, not a real clip
        if str(entry) in known_paths:
            continue
        newly_added.append(add_video(entry, title=entry.stem))
    return newly_added


# ---------------------------------------------------------------- tags

def add_tag_to_video(video_id: int, tag_name: str) -> None:
    tag_name = tag_name.strip()
    if not tag_name:
        raise LibraryError("Tag name can't be empty.")
    with db.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO video_tags (video_id, tag_id) VALUES (?, ?)",
                     (video_id, tag_id))


def remove_tag_from_video(video_id: int, tag_name: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            """DELETE FROM video_tags WHERE video_id = ? AND tag_id =
               (SELECT id FROM tags WHERE name = ?)""",
            (video_id, tag_name),
        )


# ---------------------------------------------------------------- editor integration

def apply_trim(video_id: int, start_sec: float, end_sec: float, frame_perfect: bool = False) -> Video:
    video = get_video(video_id)
    request = TrimRequest(
        video_path=video.path_obj, start_sec=start_sec, end_sec=end_sec,
        frame_perfect=frame_perfect,
    )
    existing_backup = Path(video.backup_path) if video.backup_path else None
    backup_path = commit_trim(request, has_prior_edit=video.has_edit, existing_backup=existing_backup)
    new_duration = probe_duration(video.path_obj)

    with db.get_conn() as conn:
        conn.execute(
            "UPDATE videos SET has_edit = 1, backup_path = ?, duration_sec = ? WHERE id = ?",
            (str(backup_path), new_duration, video_id),
        )
    return get_video(video_id)


def undo_edit(video_id: int) -> Video:
    video = get_video(video_id)
    if not video.has_edit or not video.backup_path:
        raise LibraryError("This video has no pending edit to undo.")
    undo_trim(video.path_obj, Path(video.backup_path))
    new_duration = probe_duration(video.path_obj)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE videos SET has_edit = 0, backup_path = NULL, duration_sec = ? WHERE id = ?",
            (new_duration, video_id),
        )
    return get_video(video_id)


def clear_edit_backup(video_id: int) -> Video:
    """Discard a video's edit backup, giving up the ability to Undo while
    leaving the edit itself in place. Distinct from undo_edit(), which
    restores from the backup instead of deleting it."""
    video = get_video(video_id)
    if not video.has_edit or not video.backup_path:
        raise LibraryError("This video has no edit backup to clear.")
    clear_backup(Path(video.backup_path))
    with db.get_conn() as conn:
        conn.execute("UPDATE videos SET backup_path = NULL WHERE id = ?", (video_id,))
    return get_video(video_id)


if __name__ == "__main__":
    import subprocess, tempfile, shutil

    db.init_db()
    tmpdir = Path(tempfile.mkdtemp())
    src = tmpdir / "clip1.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=8:size=320x240:rate=30",
         "-c:v", "libx264", "-g", "30", str(src)],
        capture_output=True, check=True,
    )

    v = add_video(src, title="Nice clutch", description="1v3 ace")
    print("Added:", v)

    add_tag_to_video(v.id, "clutch")
    add_tag_to_video(v.id, "1v3")
    print("Tags:", get_video(v.id).tags)
    print("All known tags:", all_known_tags())

    v = apply_trim(v.id, 1.0, 5.0, frame_perfect=True)
    print("After trim:", v.duration_sec, "has_edit:", v.has_edit)

    v = undo_edit(v.id)
    print("After undo:", v.duration_sec, "has_edit:", v.has_edit)

    print("Filtered by ['clutch']:", [x.title for x in list_videos(tag_filter=["clutch"])])
    print("Filtered by ['clutch','1v3']:", [x.title for x in list_videos(tag_filter=["clutch", "1v3"])])
    print("Filtered by ['nonexistent']:", [x.title for x in list_videos(tag_filter=["nonexistent"])])

    delete_video(v.id)
    print("Deleted OK, remaining videos:", len(list_videos()))
