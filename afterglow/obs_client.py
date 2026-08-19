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
                c.start_replay_buffer()
                # Give OBS a moment to actually spin up before we ever try to save.
                time.sleep(0.5)
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
        of the file it wrote. Raises OBSError if OBS doesn't report a path
        within a few seconds (e.g. buffer wasn't actually running).
        """
        c = self._require_client()
        self.ensure_replay_buffer_active()

        try:
            c.save_replay_buffer()
        except Exception as e:
            raise OBSError(f"OBS rejected the save-replay-buffer request. ({e})") from e

        # SaveReplayBuffer is fire-and-forget; poll for the resulting path.
        last_path = None
        last_error: Exception | None = None
        for _ in range(20):  # ~5s max wait
            try:
                resp = c.get_last_replay_buffer_replay()
                last_path = getattr(resp, "saved_replay_path", None)
                last_error = None
            except Exception as e:
                last_path = None
                last_error = e
            if last_path:
                break
            time.sleep(0.25)

        if last_error is not None and last_path is None:
            raise OBSError(
                f"Lost communication with OBS while waiting for the replay "
                f"buffer to save. ({last_error})"
            ) from last_error

        if not last_path:
            raise OBSError(
                "OBS didn't report a saved replay buffer file. Check that "
                "the replay buffer is enabled in OBS's Output settings."
            )

        path = Path(last_path)
        if not path.exists():
            raise OBSError(f"OBS reported a replay file that doesn't exist on disk: {path}")
        return path


if __name__ == "__main__":
    import config

    settings = config.load()
    with OBSClient(settings.obs) as client:
        print("Connected to OBS.")
        max_len = client.get_replay_buffer_max_seconds()
        print(f"Configured replay buffer length: {max_len}s" if max_len else "Could not read replay buffer length")
