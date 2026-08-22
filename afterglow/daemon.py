"""
Background daemon. Runs independently of the GUI: loads clip configs from
the DB, listens for their hotkeys via evdev, and fires clips.trigger_clip()
when one matches.

Reload behavior: polls the clip_configs table every RELOAD_INTERVAL_SEC for
changes (compares a cheap fingerprint of id+name+hotkey rows) and rebuilds
the hotkey registrations if anything changed, so editing a clip config or
its hotkey in the GUI takes effect without restarting the daemon.

Serialization: clip triggers run through a single-worker queue, not one
thread per press. Two hotkeys fired back-to-back both need to talk to OBS's
replay buffer, and OBS can only usefully do one save-and-report cycle at a
time -- letting them race would mean the second trigger's
GetLastReplayBufferReplay call could plausibly pick up the first trigger's
saved file. Queuing keeps it simple and correct at the cost of the second
clip's capture starting a beat later, which is an acceptable tradeoff for
how this is used. This serialization is bounded, not unbounded, though --
see CAPTURE_TIMEOUT_SECONDS below for why a single hung capture can't take
every future hotkey press down with it.
"""
from __future__ import annotations

import logging
import queue
import threading
import time

from . import db
from . import clips
from .clips import ClipConfig
from .hotkeys import ComboStateMachine, EvdevHotkeyListener

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("clipping-daemon")

RELOAD_INTERVAL_SEC = 2.0

# If a single capture takes longer than this, the worker loop stops
# waiting on it and moves on to any further queued hotkey presses, rather
# than blocking on it forever. Every capture up to now funnels through one
# worker thread (see the module docstring for why), which means a single
# truly-hung trigger_clip() call -- an OBS websocket that never responds,
# an ffmpeg process that never exits -- would otherwise silently and
# permanently stop ALL future hotkey presses from doing anything, with no
# obvious symptom besides "it just stopped working." Generous on purpose:
# even a slow real capture (large buffer, high resolution) should finish
# well under this; it's a safety net for genuine hangs, not a normal-case
# limit.
CAPTURE_TIMEOUT_SECONDS = 120


def _fingerprint(configs: list[ClipConfig]) -> tuple:
    return tuple(sorted((c.id, c.name, c.hotkey) for c in configs))


class ClipDaemon:
    def __init__(self):
        self.state_machine = ComboStateMachine()
        self.listener: EvdevHotkeyListener | None = None
        self._trigger_queue: "queue.Queue[int]" = queue.Queue()
        self._stop_flag = threading.Event()
        self._last_fingerprint: tuple | None = None

    # ------------------------------------------------------------ setup

    def _reload_registrations(self) -> None:
        configs = clips.list_clip_configs()
        fingerprint = _fingerprint(configs)
        if fingerprint == self._last_fingerprint:
            return  # nothing changed, skip the rebuild

        self.state_machine.clear_registrations()
        registered = 0
        for cfg in configs:
            if not cfg.hotkey:
                continue
            try:
                # Capture cfg.id by default arg to avoid the classic
                # late-binding closure bug in a loop.
                self.state_machine.register(
                    cfg.hotkey,
                    lambda clip_id=cfg.id: self._trigger_queue.put(clip_id),
                )
                registered += 1
            except Exception as e:
                logger.warning(f"Skipping bad hotkey for clip config '{cfg.name}': {e}")

        logger.info(f"Reloaded hotkey registrations: {registered} active "
                    f"({len(configs) - registered} clip config(s) have no hotkey set)")
        self._last_fingerprint = fingerprint

    def _reload_loop(self) -> None:
        while not self._stop_flag.is_set():
            try:
                self._reload_registrations()
            except Exception as e:
                logger.error(f"Error reloading clip configs: {e}")
            time.sleep(RELOAD_INTERVAL_SEC)

    # ------------------------------------------------------------ trigger worker

    def _trigger_worker_loop(self) -> None:
        while not self._stop_flag.is_set():
            try:
                clip_config_id = self._trigger_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            def _run_capture(clip_config_id=clip_config_id):
                try:
                    cfg = clips.get_clip_config(clip_config_id)
                    logger.info(f"Hotkey fired: '{cfg.name}' -- capturing clip...")
                    video = clips.trigger_clip(clip_config_id)
                    logger.info(f"Captured: {video.title} -> {video.path}")
                except Exception as e:
                    logger.error(f"Failed to capture clip (config id {clip_config_id}): {e}")

            capture_thread = threading.Thread(target=_run_capture, daemon=True)
            capture_thread.start()
            capture_thread.join(timeout=CAPTURE_TIMEOUT_SECONDS)

            if capture_thread.is_alive():
                # It's still running -- let it keep going in the background
                # (it may yet finish and register its clip normally; killing
                # it mid-ffmpeg/mid-OBS-call is riskier than just not
                # waiting on it further) but stop blocking the worker loop
                # on it, so the next queued hotkey press can be processed.
                logger.error(
                    f"Clip capture (config id {clip_config_id}) has been running for "
                    f"over {CAPTURE_TIMEOUT_SECONDS}s and appears stuck. Moving on to "
                    f"process any further queued hotkey presses rather than blocking "
                    f"on it indefinitely -- the stuck capture keeps running in the "
                    f"background and may still complete on its own. If this keeps "
                    f"happening, it points to something hanging inside OBS "
                    f"communication or ffmpeg specifically, and is worth reporting "
                    f"with these logs."
                )

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        db.init_db()
        self._reload_registrations()

        self.listener = EvdevHotkeyListener(self.state_machine)
        try:
            self.listener.start()
        except Exception as e:
            # This is the single most likely reason hotkeys silently do
            # nothing: evdev couldn't read any keyboard device, almost
            # always a /dev/input permissions issue. Make this loud and
            # specific rather than letting a bare traceback be the only
            # trace of it in the logs.
            logger.error(
                f"FAILED TO START HOTKEY LISTENER: {e}\n"
                f"This almost always means this user can't read /dev/input/event* "
                f"devices. On NixOS: confirm this user is in the 'input' group "
                f"(`groups` should list it), and note that group membership only "
                f"takes effect on a fresh login -- if 'input' was just added via "
                f"a NixOS rebuild, you need to log out and back in (or restart "
                f"this systemd user service after doing so) before it takes effect."
            )
            raise

        logger.info("Hotkey listener started.")

        threading.Thread(target=self._reload_loop, daemon=True).start()
        threading.Thread(target=self._trigger_worker_loop, daemon=True).start()

        combos = self.state_machine.registered_combos()
        logger.info(f"Daemon ready. Active hotkeys: {combos or '(none configured yet)'}")

    def stop(self) -> None:
        self._stop_flag.set()
        if self.listener:
            self.listener.stop()

    def run_forever(self) -> None:
        try:
            self.start()
        except Exception:
            logger.error("Daemon failed to start (see error above). Exiting.")
            raise
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down.")
            self.stop()


def main() -> None:
    ClipDaemon().run_forever()


if __name__ == "__main__":
    main()
