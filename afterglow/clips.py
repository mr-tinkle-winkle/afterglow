"""
Clip options: named presets of (length_seconds, sound, hotkey), plus the
pipeline that fires when a hotkey is pressed:

    1. tell OBS to save its replay buffer -> wait for the raw file to
       actually exist and finish being written (obs_client.py identifies
       the new file by watching OBS's output directory for new entries,
       and already deletes any unused sibling -- e.g. an auto-remux
       counterpart -- as soon as it picks the one to use)
    2. play the configured feedback sound (immediate confirmation that the
       raw capture succeeded, before the slower trim/re-encode step)
    3. wait a short settle period -- OBS can still be doing internal
       bookkeeping even after the raw file's size has stabilized
    4. trim the raw file down to this clip option's configured length
       (always frame-perfect/re-encode -- see trigger_clip()'s docstring
       for why fast/keyframe mode is unsafe here)
    5. verify the trimmed duration actually matches what was requested,
       loudly, rather than silently accepting a wrong-length result
    6. move the trimmed file into the clips library folder, named by
       timestamp alone (the clip config's name still goes in the title,
       just not the filename)
    7. register the result in the library DB
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import db
from . import config as config_module
from .editor import TrimRequest, commit_trim, probe_duration, EditorError
from .obs_client import OBSClient, OBSError
from .library import add_video, Video

# How long to wait after the raw replay file's size has stabilized before
# starting the trim. This is a defensive buffer for OBS's own internal
# post-save work (e.g. "Automatically Remux to mp4" in Advanced output
# settings runs as a separate step after the replay buffer file itself is
# written) that can still be in flight even once the file we're watching
# looks done.
POST_SAVE_SETTLE_SECONDS = 2.0

# How far off the actual trimmed duration is allowed to be from the
# requested length before we treat it as a real failure rather than normal
# encoding rounding. Generous on purpose -- this is a last-resort sanity
# check, not a precision requirement (frame_perfect mode is already what
# gets us precision; this just catches "something went very wrong").
DURATION_TOLERANCE_SECONDS = 1.5

# How close raw_duration needs to be to the requested clip length before
# we skip trimming entirely and just move the raw capture into place.
# This covers both "exactly equal" and "raw buffer is actually shorter
# than the requested length" (trim_start would be 0 either way -- there's
# nothing to cut). Re-encoding a file that's already the right length (or
# shorter) wastes time and a generation of quality for zero benefit.
SKIP_TRIM_TOLERANCE_SECONDS = 0.2


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
    Fire-and-forget playback. Tries pw-play first (PipeWire's own player,
    ships directly with the `pipewire` package -- no Pulse compatibility
    layer needed), falling back to paplay (traditionally shipped by the
    `pulseaudio` package, not `pipewire` itself, despite both being
    "the PipeWire audio stack" from a user's perspective -- worth being
    explicit about since getting this wrong is exactly the kind of thing
    that silently breaks sound feedback on a real system).

    Never raises: a missing/broken audio player should degrade to "no
    sound played" rather than aborting the whole clip capture pipeline
    that called this. The caller also wraps this call in a try/except as
    defense in depth, but this function shouldn't rely on that.
    """
    if not sound_path:
        return
    p = Path(sound_path)
    if not p.exists():
        print(f"Warning: configured sound file does not exist, skipping: {p}")
        return

    for player_cmd in (["pw-play", str(p)], ["paplay", str(p)]):
        try:
            subprocess.Popen(player_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue
    print(
        "Warning: neither pw-play nor paplay found on PATH -- no sound "
        "played. (On NixOS this usually means the `pipewire` and/or "
        "`pulseaudio` packages aren't available to this process.)"
    )


# ---------------------------------------------------------------- trigger pipeline

def trigger_clip(clip_config_id: int) -> Video:
    """
    The full hotkey-press pipeline. Returns the newly-created library Video.
    """
    clip_cfg = get_clip_config(clip_config_id)
    settings = config_module.load()

    with OBSClient(settings.obs) as obs_client:
        raw_path = obs_client.save_replay_buffer()  # already waits for exists + size-stable

    # Confirmation the raw capture is done -- play the feedback sound now,
    # immediately, rather than after the slower trim/re-encode step below.
    sound_to_play = clip_cfg.sound_path or settings.default_sound_path
    try:
        play_sound(sound_to_play)
    except Exception as e:
        print(f"Warning: failed to play clip sound (clip capture still succeeded): {e}")

    # Extra settle time in case OBS is still doing internal post-save work
    # (e.g. auto-remux) even though the raw file itself looks stable.
    time.sleep(POST_SAVE_SETTLE_SECONDS)

    raw_duration = probe_duration(raw_path)
    trim_start = max(0.0, raw_duration - clip_cfg.length_seconds)
    requested_duration = raw_duration - trim_start

    if trim_start <= SKIP_TRIM_TOLERANCE_SECONDS:
        # The raw buffer is already at or under the requested clip length
        # -- there's nothing meaningful to cut. Skip the re-encode
        # entirely and just use the raw capture as-is, renamed into place
        # below like any other result. Saves the encode time/quality cost
        # for a trim that would have been a no-op anyway.
        print(
            f"Raw capture ({raw_duration:.2f}s) is already at or under the "
            f"requested length ({clip_cfg.length_seconds}s) -- skipping "
            f"trim, using it as-is."
        )
        actual_duration = raw_duration
    else:
        request = TrimRequest(
            video_path=raw_path, start_sec=trim_start, end_sec=raw_duration,
            # Trigger-time trims must always be frame-perfect (full re-encode),
            # NOT fast/keyframe-seek mode -- confirmed by direct reproduction:
            # OBS's replay buffer output can have a keyframe interval larger
            # than the requested clip length (sometimes only a single keyframe
            # near the very start of the saved segment, depending on encoder
            # settings). Fast mode snaps the start time back to the nearest
            # keyframe at-or-before the request -- if that's the file's only
            # keyframe, at time 0, you get the ENTIRE raw buffer back with no
            # trim applied at all, regardless of the requested clip length.
            # frame_perfect remains an explicit opt-in only in the manual
            # Editor, where a person is choosing a precise custom range rather
            # than relying on "give me the last N seconds."
            frame_perfect=True,
            # "veryfast" rather than editor.py's "medium" default -- measured
            # directly: on a realistic 1080p60 test clip, veryfast cut total
            # trim time roughly in half versus medium, with file size much
            # closer to medium's efficiency than "ultrafast" (which is faster
            # still but bloats file size significantly). Speed matters more
            # here than optimal compression -- this is the automatic "give me
            # my clip right now" pipeline, not a considered export.
            preset="veryfast",
        )
        commit_trim(request, has_prior_edit=False, existing_backup=None)

        # Verify the trim actually produced roughly the requested length,
        # loudly, rather than trusting ffmpeg's exit code alone. This is what
        # would have caught "clipped but didn't trim" as an immediate error
        # instead of a silent wrong-length file reaching the library.
        actual_duration = probe_duration(raw_path)
        if abs(actual_duration - requested_duration) > DURATION_TOLERANCE_SECONDS:
            raise ClipError(
                f"Trim produced an unexpected duration: got {actual_duration:.2f}s, "
                f"expected ~{requested_duration:.2f}s. The raw OBS file has been left "
                f"in place at {raw_path} for inspection rather than being moved/deleted."
            )

        # commit_trim leaves a ".orig" backup next to raw_path we don't need
        # here (this is a fresh OBS export, not a library video with undo
        # semantics) -- clean it up.
        stray_backup = raw_path.with_name(raw_path.stem + ".orig" + raw_path.suffix)
        if stray_backup.exists():
            stray_backup.unlink()

    clips_dir = settings.clips_path()
    clips_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    # Filename is just the timestamp now (not prefixed with the clip
    # config's name) -- the config's name still shows up in the title
    # below, this only changes what the file on disk is called.
    final_name = f"{timestamp}{raw_path.suffix}"
    final_path = clips_dir / final_name
    shutil.move(str(raw_path), str(final_path))

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
