"""
Thin wrapper around obs-websocket (v5 protocol, via the obsws-python library).

Responsibilities:
- Connect using the host/port/password from Settings.
- Make sure the replay buffer is running (start it if not).
- Trigger a save, and figure out which file it just wrote.

We do NOT rely on GetLastReplayBufferReplay's reported path to identify
the new file -- confirmed unreliable in practice: it returned a STALE
filename from a PREVIOUS save, not the one just requested, which then
correctly (if confusingly) failed our own existence check since that
older file had already been consumed and moved away by an earlier
successful capture. Instead: snapshot OBS's output directory before
requesting the save, then watch for whatever new file(s) actually appear
-- directory contents are ground truth in a way a possibly-cached API
response isn't.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import obsws_python as obs

from .config import OBSSettings

# obsws-python logs raw connection tracebacks itself (logger.exception(...))
# on failure, which looks like an uncaught crash even when we've correctly
# caught and wrapped the error as OBSError below. Quiet it so only our own
# clean error messages show.
logging.getLogger("obsws_python").setLevel(logging.CRITICAL)

logger = logging.getLogger("afterglow.obs_client")

# How long to wait for a new file to appear in OBS's output directory
# after requesting a save, and separately, how long to wait for that new
# file's size to stop changing before treating it as finished.
DIR_WATCH_MAX_WAIT_SEC = 30
STABILIZE_MAX_WAIT_SEC = 30
POLL_INTERVAL_SEC = 0.3


class OBSError(RuntimeError):
    pass


class OBSClient:
    def __init__(self, settings: OBSSettings):
        self._settings = settings
        self._client: obs.ReqClient | None = None

    def connect(self) -> None:
        try:
            self._client = obs.ReqClient(
                host=self._settings.host,
                port=self._settings.port,
                password=self._settings.password,
                timeout=5,
            )
        except Exception as e:  # obsws-python raises varied exceptions on connect failure
            raise OBSError(
                f"Could not connect to OBS at {self._settings.host}:{self._settings.port}. "
                f"Is OBS running with obs-websocket enabled? ({e})"
            ) from e

    def disconnect(self) -> None:
        if self._client is not None:
            self._client.disconnect()
            self._client = None

    def __enter__(self) -> "OBSClient":
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()

    def _require_client(self) -> obs.ReqClient:
        if self._client is None:
            raise OBSError("Not connected. Call connect() first.")
        return self._client

    def ensure_replay_buffer_active(self) -> None:
        c = self._require_client()
        try:
            status = c.get_replay_buffer_status()
            if not status.output_active:
                logger.info("Replay buffer wasn't active -- starting it.")
                c.start_replay_buffer()
                # Give OBS a moment to actually spin up before we ever try to save.
                time.sleep(0.5)
            else:
                logger.debug("Replay buffer already active.")
        except OBSError:
            raise
        except Exception as e:
            # obsws-python can connect lazily -- a dead/unreachable OBS
            # sometimes only surfaces as a socket error on the first real
            # request rather than at connect() time. Normalize it here so
            # callers only ever have to catch OBSError.
            raise OBSError(
                f"Lost communication with OBS while checking the replay "
                f"buffer status. Is OBS still running? ({e})"
            ) from e

    def get_replay_buffer_max_seconds(self) -> int | None:
        """
        Best-effort read of OBS's configured replay buffer length, so we can
        warn the user in Settings if a clip option's length exceeds it.
        Not all obs-websocket versions expose this cleanly, so this may
        return None -- callers should treat that as 'unknown, skip the check'.
        """
        c = self._require_client()
        try:
            resp = c.get_profile_parameter(
                parameter_category="Output", parameter_name="RecRBTime"
            )
            return int(resp.parameter_value)
        except Exception:
            return None

    def get_output_directory(self) -> Path | None:
        """
        OBS's configured recording output directory -- the replay buffer
        writes here too (they share the same output path in OBS's Advanced
        output settings). Returns None if the request fails or the
        reported directory doesn't exist from this process's point of
        view; callers should treat that as "can't watch, fall back or
        error clearly" rather than assume a default path.
        """
        c = self._require_client()
        try:
            resp = c.get_record_directory()
            directory = getattr(resp, "record_directory", None)
            if not directory:
                return None
            path = Path(directory)
            return path if path.exists() else None
        except Exception as e:
            logger.warning(f"Could not query OBS's output directory: {e}")
            return None

    def save_replay_buffer(self) -> Path:
        """
        Trigger OBS to flush its replay buffer to disk, and return the path
        of the NEW file it wrote -- identified by watching OBS's output
        directory for new entries, not by trusting GetLastReplayBufferReplay
        (see module docstring for why). If OBS's "Automatically Remux to
        mp4" setting produces a second file alongside the raw one, the
        remuxed file is preferred as the target (matches OBS's own notion
        of "the finished output" when that setting is on), and any other
        new file is deleted once we've confirmed which one we're using.
        """
        c = self._require_client()
        self.ensure_replay_buffer_active()

        output_dir = self.get_output_directory()
        if output_dir is None:
            raise OBSError(
                "Could not determine OBS's output directory (GetRecordDirectory "
                "failed, or returned a path that doesn't exist from this "
                "process -- e.g. a Windows-style or otherwise unreachable path). "
                "Can't watch for new replay files without knowing where to look."
            )

        # Snapshot BEFORE triggering the save so we can identify exactly
        # which file(s) this specific save produces, regardless of
        # anything OBS's own API reports back.
        try:
            before_files = set(output_dir.iterdir())
        except OSError as e:
            raise OBSError(f"Could not read OBS's output directory {output_dir}: {e}") from e

        try:
            c.save_replay_buffer()
            logger.info(f"Requested replay buffer save from OBS. Watching {output_dir} for new files...")
        except Exception as e:
            raise OBSError(f"OBS rejected the save-replay-buffer request. ({e})") from e

        new_files: set[Path] = set()
        waited = 0.0
        last_logged_second = 0
        while waited < DIR_WATCH_MAX_WAIT_SEC:
            try:
                current_files = set(output_dir.iterdir())
                new_files = current_files - before_files
            except OSError:
                new_files = set()
            if new_files:
                logger.info(f"New file(s) detected: {[f.name for f in new_files]}")
                break
            if int(waited) > last_logged_second and int(waited) % 5 == 0:
                last_logged_second = int(waited)
                logger.info(f"Still waiting for a new file to appear ({waited:.1f}s elapsed)...")
            time.sleep(POLL_INTERVAL_SEC)
            waited += POLL_INTERVAL_SEC

        if not new_files:
            raise OBSError(
                f"No new file appeared in OBS's output directory ({output_dir}) "
                f"within {DIR_WATCH_MAX_WAIT_SEC}s of requesting the save."
            )

        # A remux counterpart (e.g. OBS's "Automatically Remux to mp4")
        # doesn't necessarily appear in the SAME poll tick as the raw file
        # -- it's a separate step OBS kicks off after the raw file exists,
        # typically a moment later. Keep watching for a short grace period
        # after the first new file(s) show up, so a sibling that appears
        # shortly after isn't missed and mistaken for "nothing else is
        # coming."
        grace_period_sec = 3.0
        grace_waited = 0.0
        while grace_waited < grace_period_sec:
            time.sleep(POLL_INTERVAL_SEC)
            grace_waited += POLL_INTERVAL_SEC
            try:
                current_files = set(output_dir.iterdir())
                newly_found = current_files - before_files
            except OSError:
                newly_found = new_files
            if newly_found != new_files:
                logger.info(f"Additional new file(s) detected during grace period: {[f.name for f in (newly_found - new_files)]}")
                new_files = newly_found

        # Prefer a remuxed .mp4 if present -- if OBS's "Automatically Remux
        # to mp4" produced one alongside the raw file, that's OBS's own
        # notion of the finished output, and generally the more broadly
        # compatible format to build our own clip from.
        mp4_candidates = sorted(f for f in new_files if f.suffix.lower() == ".mp4")
        other_candidates = sorted(f for f in new_files if f.suffix.lower() != ".mp4")

        if mp4_candidates:
            target_path = mp4_candidates[0]
        elif other_candidates:
            target_path = other_candidates[0]
        else:
            raise OBSError(f"Unexpected new file(s) in OBS output directory: {new_files}")

        # Anything else new (e.g. the raw .mkv, if we're using its .mp4
        # remux instead) isn't needed -- we've already got what we came
        # for. Best-effort cleanup; a failure here doesn't invalidate the
        # capture itself.
        for extra in new_files - {target_path}:
            try:
                extra.unlink()
                logger.info(f"Removed unused sibling file: {extra}")
            except OSError as e:
                logger.warning(f"Could not remove unused sibling file {extra}: {e}")

        # Wait for the target file's size to stop changing before treating
        # it as finished -- it can still be being written/remuxed even
        # though it already exists on disk.
        stable_count = 0
        stable_checks_needed = 2
        last_size = -1
        stabilize_waited = 0.0

        while stabilize_waited < STABILIZE_MAX_WAIT_SEC:
            try:
                size = target_path.stat().st_size
            except OSError:
                size = -1
            if size > 0 and size == last_size:
                stable_count += 1
                if stable_count >= stable_checks_needed:
                    logger.info(f"Replay file ready after {stabilize_waited:.1f}s: {target_path} ({size} bytes)")
                    return target_path
            else:
                stable_count = 0
            last_size = size
            time.sleep(POLL_INTERVAL_SEC)
            stabilize_waited += POLL_INTERVAL_SEC

        logger.info(
            f"Replay file exists but size never fully stabilized within "
            f"{STABILIZE_MAX_WAIT_SEC}s, using it anyway: {target_path}"
        )
        return target_path


if __name__ == "__main__":
    import config

    settings = config.load()
    with OBSClient(settings.obs) as client:
        print("Connected to OBS.")
        max_len = client.get_replay_buffer_max_seconds()
        print(f"Configured replay buffer length: {max_len}s" if max_len else "Could not read replay buffer length")
        out_dir = client.get_output_directory()
        print(f"Output directory: {out_dir}" if out_dir else "Could not determine output directory")
