"""
Clip options: named presets of (length_seconds, sound, hotkey), plus the
pipeline that fires when a hotkey is pressed:

    1. tell OBS to save its replay buffer -> get the raw saved file
    2. trim the raw file down to this clip option's configured length
       (always fast/keyframe mode -- trigger-time trims are not the
       "frame perfect accuracy" opt-in, that's only in the Editor)
    3. move the trimmed file into the clips library folder
    4. play the configured feedback sound
    5. register it in the DB (title defaults to clip config name + timestamp,
       so it shows up immediately in the library and can be renamed later)
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import db
import config as config_module
from editor import TrimRequest, commit_trim, probe_duration, EditorError
from obs_client import OBSClient, OBSError
from library import add_video, Video


class ClipError(RuntimeError):
    pass


@dataclass
class ClipConfig:
    id: int
    name: str
    length_seconds: int
    sound_path: str | None
    hotkey: str | None
    sort_order: int


def _row_to_clip_config(row) -> ClipConfig:
    return ClipConfig(
        id=row["id"], name=row["name"], length_seconds=row["length_seconds"],
        sound_path=row["sound_path"], hotkey=row["hotkey"], sort_order=row["sort_order"],
    )


# ---------------------------------------------------------------- CRUD

def create_clip_config(name: str, length_seconds: int, sound_path: str | None = None,
                        hotkey: str | None = None) -> ClipConfig:
    name = name.strip()
    if not name:
        raise ClipError("Clip config name can't be empty.")
    if length_seconds <= 0:
        raise ClipError("Clip length must be positive.")
    with db.get_conn() as conn:
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM clip_configs").fetchone()["m"]
        try:
            cur = conn.execute(
                """INSERT INTO clip_configs (name, length_seconds, sound_path, hotkey, sort_order)
                   VALUES (?, ?, ?, ?, ?)""",
                (name, length_seconds, sound_path, hotkey, max_order + 1),
            )
        except sqlite3.IntegrityError:
            raise ClipError(
                f"A clip config named '{name}' already exists. Names must be "
                f"unique (case-insensitive) since they're used to trigger clips "
                f"by name, e.g. `trigger --name {name}`."
            )
        row = conn.execute("SELECT * FROM clip_configs WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_clip_config(row)


def update_clip_config(clip_config_id: int, **fields) -> ClipConfig:
    allowed = {"name", "length_seconds", "sound_path", "hotkey", "sort_order"}
    bad = set(fields) - allowed
    if bad:
        raise ClipError(f"Unknown fields: {bad}")
    if "name" in fields:
        fields["name"] = fields["name"].strip()
        if not fields["name"]:
            raise ClipError("Clip config name can't be empty.")
    if not fields:
        return get_clip_config(clip_config_id)
    with db.get_conn() as conn:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        try:
            conn.execute(f"UPDATE clip_configs SET {set_clause} WHERE id = ?",
                         (*fields.values(), clip_config_id))
        except sqlite3.IntegrityError:
            raise ClipError(
                f"A clip config named '{fields.get('name')}' already exists. "
                f"Names must be unique (case-insensitive)."
            )
    return get_clip_config(clip_config_id)


def get_clip_config(clip_config_id: int) -> ClipConfig:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM clip_configs WHERE id = ?", (clip_config_id,)).fetchone()
        if row is None:
            raise ClipError(f"No clip config with id {clip_config_id}")
        return _row_to_clip_config(row)


def get_clip_config_by_name(name: str) -> ClipConfig:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM clip_configs WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row is None:
            available = [r["name"] for r in conn.execute("SELECT name FROM clip_configs ORDER BY sort_order")]
            hint = f" Available: {', '.join(available)}" if available else " No clip configs exist yet."
            raise ClipError(f"No clip config named '{name}'.{hint}")
        return _row_to_clip_config(row)


def list_clip_configs() -> list[ClipConfig]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM clip_configs ORDER BY sort_order").fetchall()
        return [_row_to_clip_config(r) for r in rows]


def delete_clip_config(clip_config_id: int) -> None:
    with db.get_conn() as conn:
        conn.execute("DELETE FROM clip_configs WHERE id = ?", (clip_config_id,))


# ---------------------------------------------------------------- sound

def play_sound(sound_path: str | None) -> None:
    """
    Fire-and-forget playback. Uses paplay (PipeWire/PulseAudio compat layer,
    present on basically every NixOS desktop setup) so we don't need an
    extra Python audio dependency. Falls back silently if no sound is set.
    """
    if not sound_path:
        return
    p = Path(sound_path)
    if not p.exists():
        return
    subprocess.Popen(
        ["paplay", str(p)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------- trigger pipeline

def trigger_clip(clip_config_id: int) -> Video:
    """
    The full hotkey-press pipeline. Returns the newly-created library Video.
    """
    clip_cfg = get_clip_config(clip_config_id)
    settings = config_module.load()

    with OBSClient(settings.obs) as obs_client:
        raw_path = obs_client.save_replay_buffer()

    raw_duration = probe_duration(raw_path)
    trim_start = max(0.0, raw_duration - clip_cfg.length_seconds)

    request = TrimRequest(
        video_path=raw_path, start_sec=trim_start, end_sec=raw_duration,
        frame_perfect=False,  # trigger-time trims are always fast; frame-perfect is an Editor-only opt-in
    )
    commit_trim(request, has_prior_edit=False, existing_backup=None)
    # commit_trim leaves a ".orig" backup next to raw_path we don't need here
    # (this is a fresh OBS export, not a library video with undo semantics) --
    # clean it up.
    stray_backup = raw_path.with_name(raw_path.stem + ".orig" + raw_path.suffix)
    if stray_backup.exists():
        stray_backup.unlink()

    clips_dir = settings.clips_path()
    clips_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    final_name = f"{clip_cfg.name}_{timestamp}{raw_path.suffix}"
    final_path = clips_dir / final_name
    shutil.move(str(raw_path), str(final_path))

    sound_to_play = clip_cfg.sound_path or settings.default_sound_path
    play_sound(sound_to_play)

    video = add_video(
        final_path,
        title=f"{clip_cfg.name} - {timestamp}",
        description="",
        clip_config_id=clip_cfg.id,
    )
    return video


if __name__ == "__main__":
    db.init_db()
    cfg = create_clip_config(name="Ace", length_seconds=30, hotkey="ctrl+shift+f9")
    print("Created clip config:", cfg)
    print("All configs:", list_clip_configs())
    updated = update_clip_config(cfg.id, length_seconds=45, hotkey="ctrl+shift+f10")
    print("Updated:", updated)
    delete_clip_config(cfg.id)
    print("After delete:", list_clip_configs())
    print("\n(trigger_clip() needs a live OBS instance -- not exercised in this sandbox)")
