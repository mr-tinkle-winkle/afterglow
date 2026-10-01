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

import json
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import db
from . import config as config_module
from . import autofilter
from . import keyframes
from . import overlay_support
from .editor import TrimRequest, commit_trim, probe_duration, EditorError
from .obs_client import OBSClient, OBSError
from .library import add_video, add_tag_to_video, get_video, Video

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
    # Input overlay (Puppetry) -- see input_overlay.py / HANDOFF.md.
    overlay_enabled: bool = False
    overlay_pieces: list = field(default_factory=lambda: ["keyboard", "mouse"])
    overlay_visible_default: bool = True
    overlay_offset_ms: float = 0.0
    # Per-piece placement overrides for this clip type ({} = use global).
    overlay_placements: dict = field(default_factory=dict)


def _json_or(text, default):
    try:
        v = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _row_to_clip_config(row) -> ClipConfig:
    keys = row.keys()
    extra = {}
    if "overlay_enabled" in keys:
        extra = dict(
            overlay_enabled=bool(row["overlay_enabled"]),
            overlay_pieces=_json_or(row["overlay_pieces"], ["keyboard", "mouse"]),
            overlay_visible_default=bool(row["overlay_visible_default"]),
            overlay_offset_ms=float(row["overlay_offset_ms"] or 0.0),
            overlay_placements=_json_or(row["overlay_placements"], {}),
        )
    return ClipConfig(
        id=row["id"], name=row["name"], length_seconds=row["length_seconds"],
        sound_path=row["sound_path"], hotkey=row["hotkey"], sort_order=row["sort_order"],
        **extra,
    )


# ---------------------------------------------------------------- CRUD

def create_clip_config(name: str, length_seconds: int, sound_path: str | None = None,
                        hotkey: str | None = None, **overlay_fields) -> ClipConfig:
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
        new_id = cur.lastrowid
    if overlay_fields:
        return update_clip_config(new_id, **overlay_fields)
    return get_clip_config(new_id)


def update_clip_config(clip_config_id: int, **fields) -> ClipConfig:
    allowed = {"name", "length_seconds", "sound_path", "hotkey", "sort_order",
               "overlay_enabled", "overlay_pieces", "overlay_visible_default",
               "overlay_offset_ms", "overlay_placements"}
    bad = set(fields) - allowed
    if bad:
        raise ClipError(f"Unknown fields: {bad}")
    # JSON-valued / boolean overlay columns are stored as text / ints.
    for k in ("overlay_pieces", "overlay_placements"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    for k in ("overlay_enabled", "overlay_visible_default"):
        if k in fields:
            fields[k] = int(bool(fields[k]))
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


def _resolve_keyframe_sound(keyframe: str, clip_cfg: "ClipConfig", settings) -> str | None:
    """Which sound (if any) to play for a given pipeline keyframe.
    REPLAY_BUFFER_COMPLETED alone keeps its pre-Advanced-Sound fallback
    chain (per-clip-config override, then the old global default) so
    existing configs sound exactly like they did before Advanced Sound
    existed unless a person opts into the new per-keyframe setting for
    it too. The other four keyframes are pure additions with nothing to
    stay backward-compatible with."""
    if keyframe == keyframes.REPLAY_BUFFER_COMPLETED:
        return (
            clip_cfg.sound_path
            or settings.advanced_sounds.get(keyframe)
            or settings.default_sound_path
            or None
        )
    return settings.advanced_sounds.get(keyframe) or None


def _play_keyframe_sound(keyframe: str, clip_cfg: "ClipConfig", settings) -> None:
    try:
        play_sound(_resolve_keyframe_sound(keyframe, clip_cfg, settings))
    except Exception as e:
        print(f"Warning: failed to play '{keyframe}' clip sound (pipeline continues): {e}")


def _play_error_sound(keyframe: str, settings) -> None:
    sound = settings.error_sounds.get(keyframe) or settings.default_error_sound_path or None
    try:
        play_sound(sound)
    except Exception as e:
        print(f"Warning: failed to play error sound for '{keyframe}': {e}")


# ---------------------------------------------------------------- input overlay capture

_OVERLAY_THREADS: "list[threading.Thread]" = []
_OVERLAY_LOCK = threading.Lock()


def wait_for_overlays(timeout: "float | None" = None) -> None:
    """Block until every background input-overlay render has finished (tests, CLI)."""
    with _OVERLAY_LOCK:
        threads = list(_OVERLAY_THREADS)
    for t in threads:
        t.join(timeout)


class _OverlayCapture:
    """The input-overlay half of one clip capture (Puppetry integration).

    on_sent(t_save) only FREEZES Puppetry's input buffer the moment the save
    request is sent (a file copy -- instant). finish() runs once the final,
    trimmed clip is in place: it renders just the clip's own span (see
    overlay_support.capture_clip) into `<clip>.input/`. Rendering the whole
    replay length up front (aio.start_clip) took ~0.7 s per second of buffer
    per piece with the real Puppetry -- a quarter hour for a 1200 s buffer --
    so the sidecar showed up long after the clip did, if at all.
    Both swallow every failure into self.error (logged; the error noise
    plays from finish()) -- a missing overlay never costs a clip. Does
    nothing at all when the clip type has the overlay off."""

    def __init__(self, clip_cfg: "ClipConfig", settings):
        self.enabled = bool(clip_cfg.overlay_enabled)
        self._cfg, self._settings = clip_cfg, settings
        self.error: str | None = None
        self.fps: float | None = None          # unused now (the clip's own fps is probed); kept for callers
        self.t_save: float | None = None
        self._workdir: Path | None = None
        self._buffer: Path | None = None
        self._clip_info: "tuple | None" = None

    def on_sent(self, t_save: float) -> None:
        if not self.enabled:
            return
        self.t_save = t_save
        try:
            import tempfile
            self._workdir = Path(tempfile.mkdtemp(prefix="afterglow_input_"))
            self._buffer = overlay_support.freeze_input(self._workdir / "inputs.jsonl")
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            print(f"Input overlay: couldn't freeze Puppetry's input buffer: {e}")

    def finish_in_background(self, video_id: int, clip_path: Path, clip_cfg: "ClipConfig", settings):
        """finish() on its own thread (returned; None when the overlay is off).
        If the clip is renamed while its pieces render, the finished sidecar
        follows it to the new name."""
        if not self.enabled:
            return None
        try:
            self._clip_info = overlay_support.probe_clip(clip_path)   # now, before the clip can be renamed
        except Exception as e:  # noqa: BLE001 -- finish() reports it
            self.error = self.error or str(e)

        def run():
            self.finish(clip_path, clip_cfg, settings)
            if overlay_support.has_sidecar(clip_path) and not Path(clip_path).exists():
                try:
                    overlay_support.move_sidecar(clip_path, get_video(video_id).path)
                except Exception as e:  # noqa: BLE001 -- the clip was deleted meanwhile, etc.
                    print(f"Input overlay: the clip moved while rendering and the overlay couldn't follow: {e}")
                    overlay_support.delete_sidecar(clip_path)
        t = threading.Thread(target=run, name="afterglow-input-overlay", daemon=False)
        with _OVERLAY_LOCK:
            _OVERLAY_THREADS[:] = [x for x in _OVERLAY_THREADS if x.is_alive()] + [t]
        t.start()
        return t

    def finish(self, clip_path: Path, clip_cfg: "ClipConfig", settings) -> None:
        if not self.enabled:
            return
        try:
            if self._buffer is None:
                raise overlay_support.OverlayError(self.error or "Puppetry's input was never frozen")
            pl = overlay_support.resolve_placements(settings.overlay_placements, clip_cfg.overlay_placements,
                                                    overlay_support.element_types())
            started = time.time()
            manifest = overlay_support.capture_clip(
                self._buffer, clip_path, clip_end=self.t_save,
                pieces=list(clip_cfg.overlay_pieces) or ["keyboard", "mouse"], placements=pl,
                offset_ms=clip_cfg.overlay_offset_ms, visible_by_default=clip_cfg.overlay_visible_default,
                clip_info=self._clip_info)
            print(f"Input overlay: {', '.join(manifest['pieces'])} made in {time.time() - started:.1f}s")
            _play_keyframe_sound(keyframes.INPUT_OVERLAY, clip_cfg, settings)
            if manifest.get("errors"):
                print(f"Input overlay: some pieces failed: {manifest['errors']}")
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            print(f"Input overlay could not be captured (the clip was kept): {e}")
            _play_error_sound(keyframes.INPUT_OVERLAY, settings)
            overlay_support.delete_sidecar(clip_path)
        finally:
            if self._workdir is not None:
                shutil.rmtree(self._workdir, ignore_errors=True)


# ---------------------------------------------------------------- trigger pipeline

def trigger_clip(clip_config_id: int) -> Video:
    """
    The full hotkey-press pipeline. Returns the newly-created library Video.

    Wrapped end-to-end in a single try/except that tracks which named
    keyframe (see keyframes.py) is currently in flight and plays that
    keyframe's configured Error Noise -- falling back to the global
    default -- before re-raising, rather than nesting a separate
    try/except around each of the five stages individually.
    """
    clip_cfg = get_clip_config(clip_config_id)
    settings = config_module.load()
    stage = keyframes.HOTKEY_RECEIVED
    try:
        # First thing in the pipeline, before anything else has even
        # been attempted -- the practical stand-in for "the hotkey was
        # pressed", since the actual OS-level key event is caught by
        # daemon.py's listener a layer up from here, immediately calling
        # straight into this function with no other work in between.
        # HOTKEY_RECEIVED and REPLAY_BUFFER_SENT both name the ACT of
        # reaching that point (not a chunk of work that could fail on
        # its own before then), so their sound plays immediately on
        # arrival. The remaining three keyframes name something
        # completing (a save, a trim, a move+DB-write) -- for those,
        # `stage` is set BEFORE attempting the underlying work, but the
        # success sound only plays AFTER it's confirmed done, so a
        # failure during that work is correctly attributed to the
        # keyframe it belongs to instead of whichever one came earlier.
        _play_keyframe_sound(stage, clip_cfg, settings)

        # Snapshot which Auto Add Filter rules currently match right away --
        # what's open/focused at the moment the hotkey was actually pressed
        # is what matters, not several seconds later once the OBS save/trim
        # pipeline below has finished (the user may well have already
        # alt-tabbed away by then).
        auto_tag_names = autofilter.compute_active_auto_tags(settings)

        stage = keyframes.REPLAY_BUFFER_SENT
        _play_keyframe_sound(stage, clip_cfg, settings)

        stage = keyframes.REPLAY_BUFFER_COMPLETED  # set before the call: a failure inside it belongs to this keyframe
        # Input overlay (Puppetry): the job has to start the moment
        # SaveReplayBuffer is SENT -- that freezes the input and starts
        # rendering while OBS writes the file. Any failure here is
        # remembered, never raised: a missing overlay must never cost a clip.
        overlay = _OverlayCapture(clip_cfg, settings)
        with OBSClient(settings.obs) as obs_client:
            overlay.fps = obs_client.get_fps() if clip_cfg.overlay_enabled else None
            raw_path = obs_client.save_replay_buffer(on_sent=overlay.on_sent)  # already waits for exists + size-stable
        _play_keyframe_sound(stage, clip_cfg, settings)

        # Extra settle time in case OBS is still doing internal post-save work
        # (e.g. auto-remux) even though the raw file itself looks stable.
        time.sleep(POST_SAVE_SETTLE_SECONDS)

        raw_duration = probe_duration(raw_path)
        trim_start = max(0.0, raw_duration - clip_cfg.length_seconds)
        requested_duration = raw_duration - trim_start

        stage = keyframes.TRIM_FINISHED  # set before attempting it (or the skip-branch below) for the same reason
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
            commit_trim(request, has_prior_edit=False, existing_backup=None, skip_backup=True)

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
        _play_keyframe_sound(stage, clip_cfg, settings)

        stage = keyframes.CLEANED_UP_MOVED  # set before attempting it, same reason as above
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
        if auto_tag_names:
            for tag_name in auto_tag_names:
                add_tag_to_video(video.id, tag_name)
            video = get_video(video.id)
        _play_keyframe_sound(stage, clip_cfg, settings)

        # Input overlay last, in the background: the clip is already safe in
        # the library, and rendering the pieces takes a while (about as long
        # as the clip, per piece, in parallel). Its own success/error noise.
        stage = keyframes.INPUT_OVERLAY
        overlay.finish_in_background(video.id, final_path, clip_cfg, settings)

        return video
    except Exception:
        _play_error_sound(stage, settings)
        raise


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
