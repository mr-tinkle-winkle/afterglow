"""
The local video library: adding clips, tagging, filtering, renaming, and
wiring the editor's trim/undo into the DB.

This module owns the DB<->filesystem relationship. editor.py knows nothing
about the database; library.py is the glue.
"""
from __future__ import annotations

import re
import sqlite3
import json
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

_UNSAFE_FILENAME_CHARS = re.compile(r'[\/\\:\*\?"<>\|\x00-\x1f]')


def _sanitize_filename_stem(title: str) -> str:
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "clip"


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = directory / f"{stem} ({n}){suffix}"
        n += 1
    return candidate


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
    favorite: bool = False

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
        favorite=bool(row["favorite"]),
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
              clip_config_id: int | None = None, created_at: str | None = None) -> Video:
    if not path.exists():
        raise LibraryError(f"File does not exist: {path}")
    try:
        duration = probe_duration(path)
    except EditorError:
        duration = None

    # created_at defaults to "now" -- correct for the capture pipeline's
    # own call (a video that was JUST finished by OBS), but WRONG for
    # scan_and_ingest_new_videos() re-discovering a file well after it
    # was actually made (moved out and back in, dropped in manually,
    # or re-created after a prune/ingest cycle) -- that caller passes
    # the file's own mtime instead. Reported directly: "it says all of
    # my videos were created today," after files had been moved out of
    # the clips folder and back in, which re-ingested them as brand
    # new rows with no way to know their real original date other than
    # what the filesystem itself still remembers.
    now = created_at or datetime.now(timezone.utc).isoformat()
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


# Sort modes for list_videos()'s sort_by parameter. "Last modified" isn't
# a DB column -- edits (and undo) overwrite the video file in place
# (editor.py's commit_trim/undo_trim both replace the file at its
# existing path), so the filesystem's own mtime already reflects "last
# actually changed" with no schema/migration needed; it's just read at
# sort time instead of being a SQL ORDER BY column like the others.
SORT_CREATED_NEWEST = "created_newest"
SORT_CREATED_OLDEST = "created_oldest"
SORT_MODIFIED_NEWEST = "modified_newest"
SORT_MODIFIED_OLDEST = "modified_oldest"
SORT_NAME_A_TO_Z = "name_a_to_z"
SORT_NAME_Z_TO_A = "name_z_to_a"
SORT_LENGTH_SHORT_TO_LONG = "length_short_to_long"
SORT_LENGTH_LONG_TO_SHORT = "length_long_to_short"
DEFAULT_SORT = SORT_CREATED_NEWEST


def _video_mtime(video: "Video") -> float:
    try:
        return Path(video.path).stat().st_mtime
    except OSError:
        return 0.0  # file momentarily missing/racing a move -- sorts as oldest rather than erroring


def _sort_videos(videos: list["Video"], sort_by: str) -> list["Video"]:
    if sort_by == SORT_CREATED_NEWEST:
        return sorted(videos, key=lambda v: v.created_at, reverse=True)
    if sort_by == SORT_CREATED_OLDEST:
        return sorted(videos, key=lambda v: v.created_at)
    if sort_by == SORT_MODIFIED_NEWEST:
        return sorted(videos, key=_video_mtime, reverse=True)
    if sort_by == SORT_MODIFIED_OLDEST:
        return sorted(videos, key=_video_mtime)
    if sort_by == SORT_NAME_A_TO_Z:
        return sorted(videos, key=lambda v: v.title.casefold())
    if sort_by == SORT_NAME_Z_TO_A:
        return sorted(videos, key=lambda v: v.title.casefold(), reverse=True)
    if sort_by == SORT_LENGTH_SHORT_TO_LONG:
        return sorted(videos, key=lambda v: v.duration_sec if v.duration_sec is not None else 0.0)
    if sort_by == SORT_LENGTH_LONG_TO_SHORT:
        return sorted(videos, key=lambda v: v.duration_sec if v.duration_sec is not None else 0.0, reverse=True)
    return videos


def list_videos(tag_filter: list[str] | None = None, uploaded_only: bool = False,
                 local_only: bool = False, search: str | None = None,
                 sort_by: str = DEFAULT_SORT, tag_exclude: list[str] | None = None,
                 favorite_only: bool = False) -> list[Video]:
    """
    tag_filter: list of tag names, ANDed together (a video must have ALL of
    them) -- this matches "multiple filters can be applied at once" as an
    intersection, which is the more useful reading for finding a specific clip.
    tag_exclude: list of tag names, any of which BLOCK a video from
    appearing at all (an OR-of-NOT -- a single blocked tag is enough to
    hide it), applied independently of tag_filter.
    favorite_only: only videos starred as a favorite.
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

        if tag_exclude:
            exclude_placeholders = ",".join("?" for _ in tag_exclude)
            conditions.append(f"""
                v.id NOT IN (
                    SELECT vt2.video_id FROM video_tags vt2
                    JOIN tags t2 ON t2.id = vt2.tag_id AND t2.name IN ({exclude_placeholders})
                )
            """)
            params.extend(tag_exclude)

        if favorite_only:
            conditions.append("v.favorite = 1")
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

        # created_at ordering here is just a stable default fetch order;
        # the real sort (including the non-SQL modified-date case) is
        # applied in Python below via _sort_videos.
        query += " ORDER BY v.created_at DESC"

        rows = conn.execute(query, params).fetchall()
        videos = [_row_to_video(r, _tags_for_video(conn, r["id"])) for r in rows]
        return _sort_videos(videos, sort_by)


MANIFEST_PATH = config.CONFIG_DIR / "library_manifest.json"


def write_library_manifest() -> None:
    """A plain, human-readable JSON snapshot of every video's metadata
    (title, tags, favorite, edited status, date) written to
    MANIFEST_PATH -- a durable backup living OUTSIDE the SQLite DB, per
    Max's direct request after a real data-loss incident (every tag
    association on every video was silently wiped when
    prune_missing_videos() saw a false mass "missing" after the whole
    clips folder was moved out and back in -- see that function's own
    safety-net comment for the full story and the fix that stops it
    from happening again going forward).

    This is NOT itself a live source of truth the app reads back from
    automatically -- it's a recovery reference a person could open and
    manually cross-check or restore from by hand if the database itself
    is ever lost or corrupted, which is a much simpler and more robust
    thing to build correctly than either baking metadata into the video
    files themselves (renaming would fight the app's own filename-
    matching logic throughout scan/prune/backup handling, and most
    video containers have no simple universal place to embed arbitrary
    per-tag metadata without a much larger, riskier rework of the
    editing/export pipeline) or trying to auto-restore from it, which
    would need its own conflict-resolution rules for what to do when
    the manifest and the DB disagree.

    Called after every mutation that changes what this describes
    (tag add/remove, rename, favorite, delete, and both scan/prune
    passes) -- cheap enough that writing on every such change (rather
    than batching or debouncing) isn't worth the added complexity, and
    keeping it always current is the entire point of a backup like
    this. Best-effort: a failure to write here (a full disk, a
    permissions issue) is logged but never raised, since a person's
    actual action (renaming a video, adding a tag) should never fail
    just because this side-channel backup couldn't be written.
    """
    try:
        videos = list_videos()
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "videos": [
                {
                    "filename": v.filename,
                    "path": v.path,
                    "title": v.title,
                    "description": v.description,
                    "created_at": v.created_at,
                    "favorite": v.favorite,
                    "has_edit": v.has_edit,
                    "tags": v.tags,
                }
                for v in videos
            ],
        }
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    except OSError as e:
        import logging
        logging.getLogger("afterglow").error(f"write_library_manifest: failed to write {MANIFEST_PATH}: {e}")


def set_favorite(video_id: int, favorite: bool) -> Video:
    with db.get_conn() as conn:
        conn.execute("UPDATE videos SET favorite = ? WHERE id = ?", (1 if favorite else 0, video_id))
    result = get_video(video_id)
    write_library_manifest()
    return result


def all_known_tags() -> list[str]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT name FROM tags ORDER BY name").fetchall()
        return [r["name"] for r in rows]


def all_tags_with_ids() -> list[tuple[int, str]]:
    """(id, name) pairs -- a tag's `id` is its stable identity (see
    db.py's schema comment on video_tags referencing tags by id, not
    name), so renaming never needs a migration; this is the read side
    other callers (e.g. the rename UI, per-tag icon assignment) need
    that all_known_tags() above doesn't provide, without changing that
    function's existing plain-names-only return type."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM tags ORDER BY name").fetchall()
        return [(r["id"], r["name"]) for r in rows]


def rename_tag(tag_id: int, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        raise LibraryError("Tag name can't be empty.")
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM tags WHERE name = ? COLLATE NOCASE AND id != ?",
            (new_name, tag_id),
        ).fetchone()
        if existing is not None:
            raise LibraryError(f"A tag named '{new_name}' already exists.")
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", (new_name, tag_id))


def set_tag_icon(tag_id: int, icon_path: str | None) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE tags SET icon_path = ? WHERE id = ?", (icon_path, tag_id))


def tag_icons() -> dict[str, str]:
    """{tag_name: icon_path} for every tag that has one assigned --
    used to auto-show icons above/below clip thumbnails per the
    Settings > Filters display options."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name, icon_path FROM tags WHERE icon_path IS NOT NULL AND icon_path != ''"
        ).fetchall()
        return {r["name"]: r["icon_path"] for r in rows}


def set_tag_outline_color(tag_id: int, outline_color: str | None) -> None:
    """outline_color: a hex string, or None to fall back to the global
    Filter Outline color (AppearanceSettings.card_text_outline_color)."""
    with db.get_conn() as conn:
        conn.execute("UPDATE tags SET outline_color = ? WHERE id = ?", (outline_color, tag_id))


def tag_outline_colors() -> dict[str, str]:
    """{tag_name: outline_color} for every tag with a per-tag Filter
    Outline color override -- tags not in this dict fall back to the
    global default."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name, outline_color FROM tags WHERE outline_color IS NOT NULL AND outline_color != ''"
        ).fetchall()
        return {r["name"]: r["outline_color"] for r in rows}


def tag_category_ids() -> dict[int, int | None]:
    """{tag_id: category_id or None} -- used by the Settings > Filters
    page to pre-select each tag's current category in its dropdown."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, category_id FROM tags").fetchall()
        return {r["id"]: r["category_id"] for r in rows}


def create_tag(tag_name: str) -> None:
    """Create a tag/filter without applying it to any video -- used by
    the Editor's Create New Filter dialog when "Apply to current video?"
    is left unchecked. add_tag_to_video() already creates the tag as a
    side effect when it IS being applied, so this only needs to cover
    the create-without-applying case."""
    tag_name = tag_name.strip()
    if not tag_name:
        raise LibraryError("Tag name can't be empty.")
    with db.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))


# ---------------------------------------------------------------- edit metadata

def rename_video(video_id: int, title: str | None = None, description: str | None = None) -> Video:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
        if row is None:
            raise LibraryError(f"No video with id {video_id}")
        new_title = title if title is not None else row["title"]
        new_desc = description if description is not None else row["description"]

        # Renaming a video's title used to only ever touch the DB row --
        # the Editor and Library both showed the new title (even after
        # reopening), but the actual file on disk kept its original
        # auto-generated name forever, which is confusing/unhelpful for
        # anyone who goes looking for the file directly (e.g. to send it
        # to someone -- see the Library's Copy context menu action).
        # Rename the real file to match whenever the title actually
        # changes, and keep filename/path in the DB in sync with it.
        new_path_str = row["path"]
        new_filename = row["filename"]
        if title is not None and title != row["title"]:
            current_path = Path(row["path"])
            if current_path.exists():
                target_stem = _sanitize_filename_stem(new_title)
                if target_stem != current_path.stem:
                    target_path = _unique_path(current_path.parent, target_stem, current_path.suffix)
                    current_path.rename(target_path)
                    new_path_str = str(target_path)
                    new_filename = target_path.name
            # If the file's missing, just update the DB text fields below --
            # same "don't treat a missing file as fatal for a metadata
            # edit" leniency prune_missing_videos() already assumes
            # elsewhere in this module.

        conn.execute(
            "UPDATE videos SET title = ?, description = ?, path = ?, filename = ? WHERE id = ?",
            (new_title, new_desc, new_path_str, new_filename, video_id),
        )
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
        result = _row_to_video(updated_row, tags)
    # write_library_manifest() opens its OWN connection via list_videos()
    # -- same reason as the comment just above about get_video(): it
    # can't see this transaction's write until the `with` block above
    # has actually exited/committed, so this has to happen after it,
    # not inside it.
    write_library_manifest()
    return result


def delete_video(video_id: int, delete_file: bool = True) -> None:
    video = get_video(video_id)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    write_library_manifest()
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

    SAFETY NET: refuses to prune anything if that would remove more than
    PRUNE_SAFETY_FRACTION of the whole library in one pass (and always
    allows pruning up to PRUNE_SAFETY_MIN_ABSOLUTE regardless, so a
    small library isn't left unable to ever prune anything at all).
    This exists because of a real, direct data-loss report: tags on
    every video vanished (though the video files and trim edits
    themselves were fine) after this ran -- almost certainly because
    Path(row["path"]).exists() returned a false "missing" for every
    video at once (a drive transiently unmounted, a race at daemon
    startup before a mount was ready, or some other filesystem-
    visibility difference between the GUI and daemon processes, most
    likely surfaced by the newer offload_library_scan_to_daemon
    setting running this same check from a different process than
    before). DELETE CASCADEs to video_tags, so a false-positive mass
    prune doesn't just hide entries -- it destroys every tag
    association on them, and re-ingesting the same files afterward
    (which still exist) creates brand new rows with fresh ids and zero
    tags, exactly matching what was reported. A single video going
    missing (moved, deleted) is completely ordinary and still pruned
    immediately; ALL or MOST of them appearing to vanish at once is
    the actual signal something is wrong with the check itself, not
    with the files -- correct behavior there is to do nothing and
    leave the existing (correct) data alone rather than confidently
    deleting most of the library on a hunch.
    """
    PRUNE_SAFETY_FRACTION = 0.5
    PRUNE_SAFETY_MIN_ABSOLUTE = 5

    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM videos").fetchall()
        missing = [row["id"] for row in rows if not Path(row["path"]).exists()]
        if not missing:
            return []
        if len(missing) > PRUNE_SAFETY_MIN_ABSOLUTE and len(missing) > len(rows) * PRUNE_SAFETY_FRACTION:
            import logging
            logging.getLogger("afterglow").error(
                f"prune_missing_videos: refusing to prune {len(missing)} of {len(rows)} videos "
                f"in one pass -- this looks like a false-positive mass \"missing\" detection "
                f"(a transiently unmounted drive, a race at startup, etc.) rather than genuinely "
                f"deleted files. Doing nothing this pass rather than risk destroying tag data on "
                f"videos that are actually still there."
            )
            return []
        for video_id in missing:
            conn.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    write_library_manifest()
    return missing


def remove_stray_orig_entries() -> list[int]:
    """
    Remove any video rows that are themselves .orig backup files --
    covers entries that got ingested by an earlier version of
    scan_and_ingest_new_videos(), before it excluded them. Only removes
    the DB row/tag associations, same as prune_missing_videos() -- the
    actual .orig file on disk is a legitimate backup and is left alone.
    """
    removed_ids = []
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path FROM videos").fetchall()
        for row in rows:
            if Path(row["path"]).stem.endswith(".orig"):
                conn.execute("DELETE FROM videos WHERE id = ?", (row["id"],))
                removed_ids.append(row["id"])
    if removed_ids:
        write_library_manifest()
    return removed_ids


def repair_incorrect_creation_dates() -> list[int]:
    """One-time-per-video repair for a real data-corruption bug: before
    prune_missing_videos() had its own safety net (see that function's
    docstring for the full story), a mass false-positive "missing"
    detection could delete a video's row and have it immediately
    re-created fresh by scan_and_ingest_new_videos() -- which, before
    THIS session's OWN fix, always stamped created_at as "now" rather
    than reading the file's actual mtime. Videos affected that way are
    still sitting in the DB today with a created_at of whenever the
    bad re-ingestion happened, not their real date -- fixing the root
    cause going forward doesn't retroactively repair data it already
    corrupted.

    Detects candidates by a simple, safe physical-impossibility check:
    a video's content (its file's mtime) can never be NEWER than when
    the DB claims the video was "created" for a genuine original
    capture -- if the file's mtime is EARLIER than its stored
    created_at, that created_at can only be an artifact of a later
    re-ingestion, not the real date, so it's corrected to the mtime.
    Anything where mtime >= created_at is left completely alone (the
    normal, correct case for every video that was never affected by
    this).

    Safe to call repeatedly/on every startup -- already-correct videos
    are no-ops, and a file that's gone missing is silently skipped
    (prune_missing_videos, not this, is responsible for that).
    Returns the ids of videos whose date was actually corrected.
    """
    repaired = []
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, path, created_at FROM videos").fetchall()
        for row in rows:
            path = Path(row["path"])
            if not path.exists():
                continue
            try:
                mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            try:
                stored = datetime.fromisoformat(row["created_at"])
            except (TypeError, ValueError):
                continue
            if stored.tzinfo is None:
                stored = stored.replace(tzinfo=timezone.utc)
            # A generous tolerance, not a strict mtime < stored check --
            # a file's mtime is set the moment its LAST byte is written,
            # which happens a little BEFORE add_video() gets called and
            # stamps created_at, even for a perfectly legitimate fresh
            # capture -- that gap is normally well under a second, but
            # without any tolerance at all, that tiny, completely
            # ordinary ordering difference was enough to falsely flag
            # every single fresh, correctly-dated video as "wrong" too.
            # An hour is far more slack than the capture pipeline could
            # ever need, while still easily catching the actual bug
            # pattern (created_at off by literal days or weeks).
            if (stored - mtime).total_seconds() > 3600:
                conn.execute(
                    "UPDATE videos SET created_at = ? WHERE id = ?",
                    (mtime.isoformat(), row["id"]),
                )
                repaired.append(row["id"])
    if repaired:
        write_library_manifest()
    return repaired


def scan_and_ingest_new_videos() -> list[Video]:
    """
    Pick up video files that exist in the clips folder but aren't tracked
    in the DB yet -- e.g. dragged/dropped in manually, or copied over from
    elsewhere. Previously the library only ever gained entries through
    add_video() being called from the capture pipeline, so anything placed
    into the clips folder by other means was invisible to it forever.

    Skips the Edit Backups folder (those are .orig copies of tracked
    videos, not videos in their own right), any stray .orig file that
    might exist directly in the clips folder itself (e.g. left over from
    before backups moved into the Edit Backups folder, or from a version
    of the app where a capture-time trim briefly created one -- see
    clips.py's trigger_clip), and any file already present at that exact
    path in the DB. Ingested videos get their filename stem as a default
    title and no tags/clip_config -- same starting state a freshly-
    captured clip would have before the user names it.

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
        if entry.stem.endswith(".orig"):
            continue  # a backup file (<name>.orig.<ext>), not a real clip
        if entry.name.endswith(".trim_tmp" + entry.suffix):
            continue  # ffmpeg's in-progress trim output, not a real clip
        if str(entry) in known_paths:
            continue
        # mtime, not "now" -- see add_video()'s own comment on why: this
        # file is being ingested well after it was actually made (moved
        # out and back in, dropped in manually, or re-created after a
        # prune/ingest cycle), so "now" would be flatly wrong for when
        # it was really recorded. mtime survives a same-filesystem move
        # (an ordinary `mv`), which is the exact scenario reported --
        # it does NOT survive a copy-then-delete or a cross-filesystem
        # move, where the OS has no way to know the original time
        # either, so this is the best available answer, not a
        # guaranteed-perfect one.
        ingested_at = datetime.fromtimestamp(entry.stat().st_mtime, tz=timezone.utc).isoformat()
        newly_added.append(add_video(entry, title=entry.stem, created_at=ingested_at))
    if newly_added:
        write_library_manifest()
    return newly_added


# ---------------------------------------------------------------- tags

def all_categories() -> list[tuple[int, str]]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM filter_categories ORDER BY name").fetchall()
        return [(r["id"], r["name"]) for r in rows]


def create_category(name: str) -> int:
    name = name.strip()
    if not name:
        raise LibraryError("Category name can't be empty.")
    with db.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO filter_categories (name) VALUES (?)", (name,))
        return conn.execute(
            "SELECT id FROM filter_categories WHERE name = ?", (name,)
        ).fetchone()["id"]


def rename_category(category_id: int, new_name: str) -> None:
    new_name = new_name.strip()
    if not new_name:
        raise LibraryError("Category name can't be empty.")
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM filter_categories WHERE name = ? COLLATE NOCASE AND id != ?",
            (new_name, category_id),
        ).fetchone()
        if existing is not None:
            raise LibraryError(f"A category named '{new_name}' already exists.")
        conn.execute("UPDATE filter_categories SET name = ? WHERE id = ?", (new_name, category_id))


def delete_category(category_id: int) -> None:
    """Deletes the category itself; any tags in it become uncategorized
    (ON DELETE SET NULL on tags.category_id), not deleted."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM filter_categories WHERE id = ?", (category_id,))


def set_tag_category(tag_id: int, category_id: int | None) -> None:
    with db.get_conn() as conn:
        conn.execute("UPDATE tags SET category_id = ? WHERE id = ?", (category_id, tag_id))


def tags_grouped_by_category() -> tuple[dict[str, list[str]], list[str]]:
    """({category_name: [tag_name, ...]}, [uncategorized_tag_name, ...]) --
    used to build the Filters dropdown's folder-style submenus. Category
    dict preserves category name alphabetical order (dict insertion
    order); each category's tag list is alphabetical too."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT t.name AS tag_name, c.name AS category_name
               FROM tags t LEFT JOIN filter_categories c ON c.id = t.category_id
               ORDER BY c.name, t.name"""
        ).fetchall()
    grouped: dict[str, list[str]] = {}
    uncategorized: list[str] = []
    for row in rows:
        if row["category_name"] is None:
            uncategorized.append(row["tag_name"])
        else:
            grouped.setdefault(row["category_name"], []).append(row["tag_name"])
    return grouped, uncategorized


def add_tag_to_video(video_id: int, tag_name: str) -> None:
    tag_name = tag_name.strip()
    if not tag_name:
        raise LibraryError("Tag name can't be empty.")
    with db.get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag_name,)).fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO video_tags (video_id, tag_id) VALUES (?, ?)",
                     (video_id, tag_id))
    write_library_manifest()


def remove_tag_from_video(video_id: int, tag_name: str) -> None:
    with db.get_conn() as conn:
        conn.execute(
            """DELETE FROM video_tags WHERE video_id = ? AND tag_id =
               (SELECT id FROM tags WHERE name = ?)""",
            (video_id, tag_name),
        )
    write_library_manifest()


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


def mark_as_edited(video_id: int) -> Video:
    """Manually flags a video as edited (has_edit=1) without an actual
    trim/backup -- useful for a video that's already edited from
    elsewhere before being brought in, or just to suppress the
    unedited-highlight border on one the user doesn't consider "raw"
    even though the app itself never touched it. Leaves backup_path
    alone (stays None if there wasn't already one) -- undo_edit() and
    clear_edit_backup() both already correctly refuse to act without a
    real backup_path regardless of has_edit, so this can't leave the
    video in a state where Undo would try to restore from nothing."""
    with db.get_conn() as conn:
        conn.execute("UPDATE videos SET has_edit = 1 WHERE id = ?", (video_id,))
    result = get_video(video_id)
    write_library_manifest()
    return result


def mark_as_unedited(video_id: int) -> Video:
    """The reverse of mark_as_edited() -- manually clears has_edit,
    for a video the app or the user marked as edited that shouldn't
    be anymore (undoing a manual mark, or a video that turns out not
    to actually be edited after all). Does NOT touch backup_path --
    if a real trim backup exists, it's left completely alone; this
    only affects the has_edit flag itself and, in turn, whether the
    Library shows the unedited-highlight border on this video."""
    with db.get_conn() as conn:
        conn.execute("UPDATE videos SET has_edit = 0 WHERE id = ?", (video_id,))
    result = get_video(video_id)
    write_library_manifest()
    return result
    write_library_manifest()
    return result


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


@dataclass
class LibraryStats:
    """Snapshot for Settings > Stats. Computed on demand (see
    compute_stats()) rather than kept live, since a full requery +
    per-filter scan over the whole library on every card change would
    be wasted work the vast majority of the time nobody's looking at
    this tab -- the Stats page itself only calls compute_stats() when
    its own Refresh button is pressed."""
    total: int
    with_filter: int
    per_filter: dict[str, int]  # tag name -> count, alphabetical (matches all_known_tags() order)
    unedited: int
    edited: int
    avg_length_sec: float | None
    longest_title: str | None
    longest_length_sec: float | None
    shortest_title: str | None
    shortest_length_sec: float | None


def compute_stats() -> "LibraryStats":
    """Combined Local + Uploaded totals -- list_videos() with no
    filters already returns every video regardless of upload state."""
    videos = list_videos()
    total = len(videos)
    with_filter = sum(1 for v in videos if v.tags)
    per_filter = {tag: 0 for tag in all_known_tags()}
    for v in videos:
        for tag in v.tags:
            if tag in per_filter:
                per_filter[tag] += 1
    edited = sum(1 for v in videos if v.has_edit)
    unedited = total - edited

    timed = [v for v in videos if v.duration_sec is not None]
    if timed:
        avg_length_sec = sum(v.duration_sec for v in timed) / len(timed)
        longest = max(timed, key=lambda v: v.duration_sec)
        shortest = min(timed, key=lambda v: v.duration_sec)
        longest_title, longest_length_sec = longest.title, longest.duration_sec
        shortest_title, shortest_length_sec = shortest.title, shortest.duration_sec
    else:
        avg_length_sec = None
        longest_title = longest_length_sec = None
        shortest_title = shortest_length_sec = None

    return LibraryStats(
        total=total, with_filter=with_filter, per_filter=per_filter,
        unedited=unedited, edited=edited, avg_length_sec=avg_length_sec,
        longest_title=longest_title, longest_length_sec=longest_length_sec,
        shortest_title=shortest_title, shortest_length_sec=shortest_length_sec,
    )


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
