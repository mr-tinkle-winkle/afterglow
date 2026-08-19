"""
SQLite database for clip configs, the local/uploaded video library, and tags.

One DB file, lives next to the config: ~/.config/afterglow/library.db
(Not inside ~/Videos/Clips, so the clips folder can be a plain folder of
media files with nothing weird in it — easier to browse, sync, back up.)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from contextlib import contextmanager

from .config import CONFIG_DIR

DB_PATH = CONFIG_DIR / "library.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS clip_configs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    length_seconds  INTEGER NOT NULL,
    sound_path      TEXT,                  -- NULL/empty = use default sound
    hotkey          TEXT,                  -- serialized combo, e.g. "ctrl+shift+f9"
    sort_order      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS videos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    path            TEXT NOT NULL UNIQUE,   -- absolute path to the working file on disk
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    duration_sec    REAL,                   -- NULL until probed
    created_at      TEXT NOT NULL,          -- ISO timestamp, clip capture time
    clip_config_id  INTEGER,                -- which clip option produced this (nullable, e.g. manual import)
    has_edit        INTEGER NOT NULL DEFAULT 0,   -- 1 if a trim has been committed
    backup_path     TEXT,                   -- pre-edit copy, for undo. NULL if no pending undo.
    youtube_video_id TEXT,                  -- NULL until uploaded
    youtube_privacy TEXT,                   -- 'unlisted' | 'public' | 'private', NULL if not uploaded
    FOREIGN KEY (clip_config_id) REFERENCES clip_configs(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS video_tags (
    video_id    INTEGER NOT NULL,
    tag_id      INTEGER NOT NULL,
    PRIMARY KEY (video_id, tag_id),
    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_video_tags_tag ON video_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_video_tags_video ON video_tags(video_id);
"""

# Clip config names need to be unique, case-insensitively, since they're
# used as the stable identifier for hotkey-triggered CLI invocations
# (e.g. `cli.py trigger --name Ace`) -- a system keybind shouldn't have to
# know a numeric DB id. This is a separate CREATE UNIQUE INDEX (rather than
# an inline UNIQUE column constraint) so it can be applied to a database
# that was already created before this constraint existed, without an
# ALTER TABLE migration.
UNIQUE_NAME_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_clip_configs_name "
    "ON clip_configs(name COLLATE NOCASE)"
)


def init_db() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        try:
            conn.execute(UNIQUE_NAME_INDEX)
        except sqlite3.IntegrityError:
            # Pre-existing DB already has duplicate clip config names.
            # Don't hard-fail app startup over this -- surface it instead,
            # since dedup requires a human decision (which one keeps the name).
            dupes = conn.execute(
                """SELECT name, COUNT(*) c FROM clip_configs
                   GROUP BY name COLLATE NOCASE HAVING c > 1"""
            ).fetchall()
            names = ", ".join(f"'{r['name']}'" for r in dupes)
            print(
                f"Warning: duplicate clip config names found ({names}). "
                f"Rename one of each pair -- clip config names must be unique "
                f"to be used with `trigger --name`."
            )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"DB initialized at {DB_PATH}")
