"""
Thin wrapper around obs-websocket (v5 protocol, via the obsws-python library).

Responsibilities:
- Connect using the host/port/password from Settings.
- Make sure the replay buffer is running (start it if not).
- Trigger a save, and figure out which file it just wrote.

obs-websocket's SaveReplayBuffer doesn't return the output path directly in
all versions, so we ask GetLastReplayBufferReplay after saving, which is
the reliable way to get the path OBS just wrote.
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

    def save_replay_buffer(self) -> Path:
        """
        Trigger OBS to flush its replay buffer to disk, and return the path
        of the file it wrote. Raises OBSError if OBS doesn't report a path,
        or the file never appears, within the wait budgets below.
        """
        c = self._require_client()
        self.ensure_replay_buffer_active()

        try:
            c.save_replay_buffer()
            logger.info("Requested replay buffer save from OBS.")
        except Exception as e:
            raise OBSError(f"OBS rejected the save-replay-buffer request. ({e})") from e

        # SaveReplayBuffer is fire-and-forget; poll for the resulting path.
        # Budget is generous on purpose -- a longer/higher-resolution real
        # buffer can plausibly take OBS longer to report back than a quick
        # synthetic test suggests, and a too-short timeout here fails with
        # a message that's easy to misread as "OBS isn't doing anything"
        # when OBS may just still be working on it.
        max_report_wait_sec = 20
        report_poll_interval = 0.5
        last_path = None
        last_error: Exception | None = None
        attempts = int(max_report_wait_sec / report_poll_interval)

        for attempt in range(1, attempts + 1):
            try:
                resp = c.get_last_replay_buffer_replay()
                last_path = getattr(resp, "saved_replay_path", None)
                last_error = None
            except Exception as e:
                last_path = None
                last_error = e
            if last_path:
                logger.info(f"OBS reported saved replay path after {attempt} attempt(s): {last_path}")
                break
            if attempt % 4 == 0:  # log roughly every 2s, not every 0.5s
                logger.info(
                    f"Still waiting for OBS to report the saved replay path "
                    f"(attempt {attempt}/{attempts}, {attempt * report_poll_interval:.1f}s elapsed)..."
                )
            time.sleep(report_poll_interval)

        if last_error is not None and last_path is None:
            raise OBSError(
                f"Lost communication with OBS while waiting for the replay "
                f"buffer to save, after {max_report_wait_sec}s. ({last_error})"
            ) from last_error

        if not last_path:
            raise OBSError(
                f"OBS never reported a saved replay buffer file within "
                f"{max_report_wait_sec}s of requesting the save. Check that "
                f"the replay buffer is enabled in OBS's Output settings, "
                f"and that this isn't a very large buffer taking longer "
                f"than {max_report_wait_sec}s for OBS itself to finish "
                f"writing/registering internally."
            )

        path = Path(last_path)

        # OBS reporting a path via GetLastReplayBufferReplay does not mean
        # the file is actually finished being written yet -- it can still
        # be remuxing/flushing to disk, especially for larger buffers. Wait
        # for the file to exist AND for its size to stop changing across
        # consecutive checks before treating it as ready, rather than a
        # single immediate existence check.
        max_wait_sec = 30
        poll_interval = 0.3
        waited = 0.0
        last_size = -1
        stable_count = 0
        stable_checks_needed = 2
        last_logged_second = 0

        while waited < max_wait_sec:
            if path.exists():
                try:
                    size = path.stat().st_size
                except OSError:
                    size = -1
                if size > 0 and size == last_size:
                    stable_count += 1
                    if stable_count >= stable_checks_needed:
                        logger.info(f"Replay file ready after {waited:.1f}s: {path} ({size} bytes)")
                        return path
                else:
                    stable_count = 0
                last_size = size
            elif int(waited) > last_logged_second and int(waited) % 5 == 0:
                last_logged_second = int(waited)
                logger.info(f"Still waiting for replay file to appear on disk ({waited:.1f}s elapsed): {path}")
            time.sleep(poll_interval)
            waited += poll_interval

        if not path.exists():
            raise OBSError(
                f"OBS reported a replay file that never appeared on disk "
                f"after {max_wait_sec}s: {path}. This can happen if OBS is "
                f"still remuxing a large buffer, or if the path OBS "
                f"reported isn't reachable from this process."
            )
        # It exists but its size never stabilized within the wait window --
        # still return it rather than failing outright. A slow disk
        # finishing the write a moment later is far more likely than
        # genuine corruption, and the trim step downstream will fail
        # loudly and specifically if the file actually is bad.
        logger.info(f"Replay file exists but size never fully stabilized within {max_wait_sec}s, using it anyway: {path}")
        return path


if __name__ == "__main__":
    import config

    settings = config.load()
    with OBSClient(settings.obs) as client:
        print("Connected to OBS.")
        max_len = client.get_replay_buffer_max_seconds()
        print(f"Configured replay buffer length: {max_len}s" if max_len else "Could not read replay buffer length")
