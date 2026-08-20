"""
Global hotkey capture, evdev/uinput-style (same family of approach as the
macro daemon), so combos work regardless of window/game focus and
regardless of which desktop environment or window manager is running --
this matters since the friend group spans several different setups.

This module is split into two layers on purpose:

  - ComboStateMachine: pure logic, no I/O. Feeds it abstract key-down/up
    events, it tells you when a registered combo is "hit". Fully unit
    testable without real hardware -- this is what's tested below, since
    this sandbox has no real input devices to test the evdev layer against.

  - EvdevHotkeyListener: thin I/O wrapper that reads real keyboard devices
    via python-evdev and feeds ComboStateMachine. Not exercised in this
    sandbox (no /dev/input here) -- needs a real test on your machine.
    Requires the running user to have read access to /dev/input/event*
    (typically membership in the "input" group on NixOS -- the same
    permission the macro daemon already needs, so if that works today,
    this should too).

Combo string format: modifiers first in a fixed order, then other keys
alphabetically, lowercase, joined with "+". E.g. "ctrl+shift+f9".
Left/right variants of a modifier are treated as the same modifier
(ctrl+f9 fires whether it's left or right ctrl).
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

# --------------------------------------------------------------- combo format

_MODIFIER_ORDER = ["ctrl", "shift", "alt", "meta"]

_MODIFIER_KEY_MAP = {
    "KEY_LEFTCTRL": "ctrl", "KEY_RIGHTCTRL": "ctrl",
    "KEY_LEFTSHIFT": "shift", "KEY_RIGHTSHIFT": "shift",
    "KEY_LEFTALT": "alt", "KEY_RIGHTALT": "alt",
    "KEY_LEFTMETA": "meta", "KEY_RIGHTMETA": "meta",
}


def _display_name(evdev_key_name: str) -> str:
    if evdev_key_name in _MODIFIER_KEY_MAP:
        return _MODIFIER_KEY_MAP[evdev_key_name]
    name = evdev_key_name
    if name.startswith("KEY_"):
        name = name[len("KEY_"):]
    return name.lower()


def normalize_combo(evdev_key_names: set[str]) -> str:
    """
    Turn a set of raw evdev key names (e.g. {'KEY_LEFTCTRL', 'KEY_F9'})
    into the canonical display/storage string (e.g. 'ctrl+f9').
    """
    display_names = {_display_name(k) for k in evdev_key_names}
    modifiers = [m for m in _MODIFIER_ORDER if m in display_names]
    others = sorted(display_names - set(modifiers))
    return "+".join(modifiers + others)


class ComboError(ValueError):
    pass


def parse_combo(combo_str: str) -> frozenset[str]:
    """Validate + canonicalize a user-entered or stored combo string into
    a set of display-name parts, for equality comparisons."""
    if not combo_str or not combo_str.strip():
        raise ComboError("Hotkey combo can't be empty.")
    parts = frozenset(p.strip().lower() for p in combo_str.split("+") if p.strip())
    if not parts:
        raise ComboError(f"Could not parse hotkey combo: '{combo_str}'")
    return parts


# --------------------------------------------------------------- pure state machine

@dataclass
class _Registration:
    combo_parts: frozenset[str]
    callback: Callable[[], None]


class ComboStateMachine:
    """
    Pure logic: tracks which keys are currently held (by evdev key name)
    and fires a registered callback when the held set's normalized combo
    exactly matches a registered combo, on the key-down transition that
    completes the match (not on repeat, not on every held-set change).
    """

    def __init__(self):
        self._held: set[str] = set()
        self._registrations: dict[str, _Registration] = {}  # combo_str -> registration
        self._lock = threading.Lock()

    def register(self, combo_str: str, callback: Callable[[], None]) -> None:
        parts = parse_combo(combo_str)
        canonical = "+".join(
            [m for m in _MODIFIER_ORDER if m in parts] + sorted(parts - set(_MODIFIER_ORDER))
        )
        with self._lock:
            self._registrations[canonical] = _Registration(parts, callback)

    def clear_registrations(self) -> None:
        with self._lock:
            self._registrations.clear()

    def registered_combos(self) -> list[str]:
        with self._lock:
            return list(self._registrations.keys())

    def key_down(self, evdev_key_name: str, is_repeat: bool = False) -> str | None:
        """
        Feed a key-down event. Returns the combo string that fired, if any,
        else None. Repeat events (holding a key) never (re)trigger.
        """
        if is_repeat:
            return None
        with self._lock:
            self._held.add(evdev_key_name)
            held_display = {_display_name(k) for k in self._held}
            for combo_str, reg in self._registrations.items():
                if reg.combo_parts == held_display:
                    callback = reg.callback
                    break
            else:
                return None
        # Fire outside the lock so a slow callback doesn't block key processing.
        threading.Thread(target=callback, daemon=True).start()
        return combo_str

    def key_up(self, evdev_key_name: str) -> None:
        with self._lock:
            self._held.discard(evdev_key_name)

    def currently_held_display(self) -> set[str]:
        with self._lock:
            return {_display_name(k) for k in self._held}


# --------------------------------------------------------------- recording (for the GUI)

class ComboRecorder:
    """
    Used by the Settings GUI's 'Record' button: capture a chord as the user
    presses it, and finalize on the first key-up (the common "press the
    combo, then let go" convention). Pure logic; the GUI/daemon feeds it
    events from whichever I/O layer is available (evdev on the daemon
    side; the GUI can either run its own tiny evdev listener while
    recording, or ask the daemon to do it -- see EvdevHotkeyListener notes
    below for why the GUI needs read access to /dev/input either way).
    """

    def __init__(self):
        self._held: set[str] = set()
        self._max_held: set[str] = set()
        self._done = False

    def key_down(self, evdev_key_name: str, is_repeat: bool = False) -> None:
        if is_repeat or self._done:
            return
        self._held.add(evdev_key_name)
        self._max_held = set(self._held)  # snapshot of the fullest chord reached

    def key_up(self, evdev_key_name: str) -> str | None:
        """Returns the finalized combo string once any key is released
        after at least one key was pressed, else None (still recording)."""
        if self._done:
            return None
        self._held.discard(evdev_key_name)
        if self._max_held:
            self._done = True
            return normalize_combo(self._max_held)
        return None

    @property
    def is_done(self) -> bool:
        return self._done


class RecorderAdapter:
    """
    Lets EvdevHotkeyListener feed a ComboRecorder instead of a
    ComboStateMachine, without EvdevHotkeyListener needing to know the
    difference -- both expose key_down(name, is_repeat)/key_up(name).
    Used by the Settings GUI's 'Record Hotkey' dialog so it can reuse the
    exact same evdev I/O layer the daemon uses, rather than a second
    hand-rolled device-reading implementation.
    """

    def __init__(self, on_recorded: Callable[[str], None]):
        self._recorder = ComboRecorder()
        self._on_recorded = on_recorded
        self._fired = False

    def key_down(self, evdev_key_name: str, is_repeat: bool = False) -> None:
        self._recorder.key_down(evdev_key_name, is_repeat=is_repeat)

    def key_up(self, evdev_key_name: str) -> None:
        result = self._recorder.key_up(evdev_key_name)
        if result is not None and not self._fired:
            self._fired = True
            self._on_recorded(result)


# --------------------------------------------------------------- evdev I/O layer
# NOT exercised in this sandbox -- no /dev/input available. Small and
# direct on purpose so the only untested part is "read evdev events and
# call state_machine.key_down/key_up", which is a thin, low-risk mapping.

def _is_keyboard_device(device) -> bool:
    import evdev
    caps = device.capabilities().get(evdev.ecodes.EV_KEY, [])
    # Require presence of common alnum + function keys to filter out
    # mice/other EV_KEY devices (e.g. power buttons) that technically
    # report EV_KEY but aren't keyboards.
    has_letters = evdev.ecodes.KEY_A in caps
    has_fkeys = evdev.ecodes.KEY_F1 in caps
    return has_letters and has_fkeys


def discover_keyboard_devices() -> list[str]:
    import evdev
    paths = evdev.list_devices()
    keyboards = []
    for path in paths:
        try:
            dev = evdev.InputDevice(path)
            if _is_keyboard_device(dev):
                keyboards.append(path)
        except (PermissionError, OSError):
            continue
    return keyboards


class EvdevHotkeyListener:
    """
    Reads all discovered keyboard devices in background threads and feeds
    a ComboStateMachine. Does NOT grab() devices -- events pass through
    normally to the rest of the system, this is passive listening only,
    which is what we want (a clip hotkey shouldn't eat the keypress from
    whatever game/app is focused).
    """

    def __init__(self, state_machine: ComboStateMachine, device_paths: list[str] | None = None):
        self.state_machine = state_machine
        self._device_paths = device_paths
        self._threads: list[threading.Thread] = []
        self._stop_flag = threading.Event()

    def start(self) -> None:
        import evdev
        paths = self._device_paths or discover_keyboard_devices()
        if not paths:
            raise RuntimeError(
                "No readable keyboard devices found. Check that this user "
                "has read access to /dev/input/event* (e.g. is in the "
                "'input' group on NixOS)."
            )
        for path in paths:
            t = threading.Thread(target=self._read_loop, args=(path,), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop_flag.set()

    def _read_loop(self, path: str) -> None:
        import select
        import evdev
        from evdev import categorize, ecodes

        try:
            device = evdev.InputDevice(path)
        except (PermissionError, OSError):
            return

        try:
            while not self._stop_flag.is_set():
                # select() with a timeout, rather than device.read_loop()'s
                # unconditional blocking read, so this thread actually wakes
                # up and exits promptly on stop() even if no key is pressed
                # on this specific device in the meantime. Without this, a
                # thread blocked in read_loop() would only ever notice
                # stop() on its *next* event -- which, for the hotkey-record
                # dialog opened and closed repeatedly, meant reader threads
                # piling up for the lifetime of the process.
                ready, _, _ = select.select([device.fd], [], [], 0.2)
                if not ready:
                    continue
                for event in device.read():
                    if event.type != ecodes.EV_KEY:
                        continue
                    key_event = categorize(event)
                    key_name = key_event.keycode
                    if isinstance(key_name, list):  # evdev sometimes gives aliases as a list
                        key_name = key_name[0]

                    if key_event.keystate == key_event.key_down:
                        self.state_machine.key_down(key_name, is_repeat=False)
                    elif key_event.keystate == key_event.key_hold:
                        self.state_machine.key_down(key_name, is_repeat=True)
                    elif key_event.keystate == key_event.key_up:
                        self.state_machine.key_up(key_name)
        finally:
            device.close()


if __name__ == "__main__":
    # Pure-logic smoke test -- no real devices needed.
    sm = ComboStateMachine()
    fired = []
    sm.register("ctrl+shift+f9", lambda: fired.append("ace"))
    sm.register("f10", lambda: fired.append("quick"))

    print("Combo string parsing/normalization:")
    print(" ", normalize_combo({"KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_F9"}))
    print(" ", normalize_combo({"KEY_RIGHTCTRL", "KEY_F9", "KEY_LEFTSHIFT"}))  # different physical keys, same combo

    print("\nSimulating ctrl+shift+f9 press:")
    sm.key_down("KEY_LEFTCTRL")
    sm.key_down("KEY_LEFTSHIFT")
    sm.key_down("KEY_F9")
    time.sleep(0.05)
    print("  fired:", fired)

    print("\nHolding (repeat) should NOT refire:")
    sm.key_down("KEY_F9", is_repeat=True)
    time.sleep(0.05)
    print("  fired:", fired)

    print("\nRelease and press f10 alone:")
    sm.key_up("KEY_F9")
    sm.key_up("KEY_LEFTSHIFT")
    sm.key_up("KEY_LEFTCTRL")
    sm.key_down("KEY_F10")
    time.sleep(0.05)
    print("  fired:", fired)

    print("\nPartial combo (just ctrl+shift, no f9) should NOT fire anything:")
    fired.clear()
    sm.key_up("KEY_F10")
    sm.key_down("KEY_LEFTCTRL")
    sm.key_down("KEY_LEFTSHIFT")
    time.sleep(0.05)
    print("  fired:", fired, "(expected empty)")

    print("\nRecorder test -- press ctrl+shift+f9 then release shift first:")
    rec = ComboRecorder()
    rec.key_down("KEY_LEFTCTRL")
    rec.key_down("KEY_LEFTSHIFT")
    rec.key_down("KEY_F9")
    result = rec.key_up("KEY_LEFTSHIFT")
    print("  recorded combo:", result)
