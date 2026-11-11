"""
Bugs found by the app + daemon audit, each with the check that would have
caught it:

  * a clip hotkey did nothing while any other key was held (W, shift, ctrl...)
  * a keyboard that was unplugged / reconnected lost its hotkeys until the
    daemon was restarted; keys held on it stayed "held" forever
  * discover_keyboard_devices leaked one open device per keyboard per call
  * the settings file was written in place: another process could read it
    empty or cut off
  * a settings field written by a newer build stopped an older one loading
  * old card thumbnails were never removed

    QT_QPA_PLATFORM=offscreen python3 tests/test_bug_sweep.py
"""
import os, sys, tempfile, time, threading, subprocess
from pathlib import Path
HOME = tempfile.mkdtemp(prefix="sweep_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from afterglow import hotkeys, config

fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


def wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# ---------------------------------------------------------------- combos
fired = []
sm = hotkeys.ComboStateMachine()
sm.register("f9", lambda: fired.append("f9"))
sm.register("ctrl+f9", lambda: fired.append("ctrl+f9"))
sm.register("alt+shift+c", lambda: fired.append("asc"))


def press(*keys):
    out = None
    for k in keys:
        out = sm.key_down(k)
    return out


def release(*keys):
    for k in keys:
        sm.key_up(k)


check(press("KEY_F9") == "f9", "a plain hotkey fires")
release("KEY_F9")
check(press("KEY_W", "KEY_LEFTSHIFT", "KEY_F9") == "f9", "...also while moving: W and shift held (did nothing before)")
check(sm.key_down("KEY_F9", is_repeat=True) is None, "holding it doesn't fire again")
check(sm.key_down("KEY_A") is None, "pressing an unrelated key while the hotkey is held doesn't fire it again")
release("KEY_W", "KEY_LEFTSHIFT", "KEY_F9", "KEY_A")
check(press("KEY_LEFTCTRL", "KEY_F9") == "ctrl+f9", "with both f9 and ctrl+f9 registered, ctrl+f9 wins when ctrl is held")
release("KEY_LEFTCTRL", "KEY_F9")
check(press("KEY_RIGHTCTRL", "KEY_SPACE", "KEY_F9") == "ctrl+f9", "...with other keys down too, either ctrl key")
release("KEY_RIGHTCTRL", "KEY_SPACE", "KEY_F9")
check(press("KEY_LEFTALT", "KEY_LEFTSHIFT") is None, "an incomplete combo doesn't fire")
check(sm.key_down("KEY_C") == "shift+alt+c", "completing it does")
release("KEY_LEFTALT", "KEY_LEFTSHIFT", "KEY_C")
check(press("KEY_C") is None, "its last key alone doesn't")
release("KEY_C")
check(press("KEY_F9", "KEY_LEFTCTRL") == "ctrl+f9", "keys may be pressed in any order, as before (modifier last completes ctrl+f9)")
release("KEY_F9", "KEY_LEFTCTRL")
time.sleep(0.1)
check(fired == ["f9", "f9", "ctrl+f9", "ctrl+f9", "asc", "f9", "ctrl+f9"], f"one callback per completed combo {fired}")
sm.key_down("KEY_Z")
sm.release_all(["KEY_Z", "KEY_NEVER"])
check(sm.currently_held_display() == set(), "release_all forgets keys")

# ---------------------------------------------------------------- devices
import evdev
from evdev import ecodes


class FakeDevice:
    """Enough of evdev.InputDevice: a pipe for select(), queued events, a 'gone' switch."""
    opened = 0
    closed = 0

    def __init__(self, path):
        if path not in PLUGGED:
            raise OSError(19, "No such device")
        self.path = path
        self._r, self._w = os.pipe()
        self.fd = self._r
        self._events = []
        self.gone = False
        FakeDevice.opened += 1
        DEVICES.setdefault(path, []).append(self)

    def capabilities(self):
        return {ecodes.EV_KEY: [ecodes.KEY_A, ecodes.KEY_F1, ecodes.KEY_F9]}

    def feed(self, code, value):
        self._events.append(evdev.InputEvent(0, 0, ecodes.EV_KEY, code, value))
        os.write(self._w, b"x")

    def unplug(self):
        self.gone = True
        os.write(self._w, b"x")

    def read(self):
        os.read(self._r, 64)
        if self.gone:
            raise OSError(19, "No such device")
        ev, self._events = self._events, []
        return ev

    def close(self):
        FakeDevice.closed += 1
        for fd in (self._r, self._w):
            try:
                os.close(fd)
            except OSError:
                pass


PLUGGED = {"/dev/input/event3"}
DEVICES: dict = {}
evdev.InputDevice = FakeDevice
evdev.list_devices = lambda: sorted(PLUGGED)

paths = hotkeys.discover_keyboard_devices()
check(paths == ["/dev/input/event3"] and FakeDevice.opened == FakeDevice.closed == 1,
      "discover_keyboard_devices closes every device it opened (it left them open)")

fired2 = []
sm2 = hotkeys.ComboStateMachine()
sm2.register("f9", lambda: fired2.append(time.time()))
hotkeys.EvdevHotkeyListener.RESCAN_SECONDS = 0.15
listener = hotkeys.EvdevHotkeyListener(sm2)
listener.start()
check(wait(lambda: listener.active_devices() == ["/dev/input/event3"]), "the keyboard is being read")
reader = lambda path: [d for d in DEVICES[path] if not d.gone and d._events is not None][-1]
kb = DEVICES["/dev/input/event3"][-1]
kb.feed(ecodes.KEY_F9, 1)
kb.feed(ecodes.KEY_F9, 0)
check(wait(lambda: len(fired2) == 1), "a hotkey pressed on it fires")

# unplug while a key is held: the reader ends, the key is released, nothing is stuck
kb.feed(ecodes.KEY_LEFTSHIFT, 1)
check(wait(lambda: sm2.currently_held_display() == {"shift"}), "(shift is held on it)")
PLUGGED.clear()
kb.unplug()
check(wait(lambda: listener.active_devices() == []), "unplugged: its reader ends instead of dying silently")
check(sm2.currently_held_display() == set(), "...and the key that was held on it is released (it stayed stuck before)")

# plugged back in (even under another event number): picked up without a restart
PLUGGED.add("/dev/input/event7")
check(wait(lambda: listener.active_devices() == ["/dev/input/event7"], 3), "plugged back in: found by the rescan")
kb2 = DEVICES["/dev/input/event7"][-1]
before_open = FakeDevice.opened
kb2.feed(ecodes.KEY_F9, 1)
kb2.feed(ecodes.KEY_F9, 0)
check(wait(lambda: len(fired2) == 2), "hotkeys work on it again, with no daemon restart")
time.sleep(0.5)
check(FakeDevice.opened == before_open, "the rescan doesn't reopen keyboards that are already being read")
# a second keyboard added later
PLUGGED.add("/dev/input/event9")
check(wait(lambda: listener.active_devices() == ["/dev/input/event7", "/dev/input/event9"], 3),
      "a second keyboard connected later is picked up too")
DEVICES["/dev/input/event9"][-1].feed(ecodes.KEY_F9, 1)
check(wait(lambda: len(fired2) == 3), "...and its hotkeys fire")
listener.stop()
check(wait(lambda: listener.active_devices() == [], 2), "stop() ends every reader")

# explicit device list (the record dialog's own use): no watcher, as before
l2 = hotkeys.EvdevHotkeyListener(hotkeys.ComboStateMachine(), device_paths=["/dev/input/event7"])
l2.start()
check(wait(lambda: l2.active_devices() == ["/dev/input/event7"]) and not l2._watch, "an explicit device list isn't rescanned")
l2.stop()

# ---------------------------------------------------------------- settings file
s = config.load()
s.obs.password = "secret-" + "x" * 2000          # a big file: a non-atomic write is easy to catch mid-way
config.save(s)
stop = threading.Event()
problems = []


def reader_proc():
    # what the daemon does: re-read the file over and over, from scratch
    import tomllib
    while not stop.is_set():
        try:
            with open(config.CONFIG_FILE, "rb") as f:
                raw = tomllib.load(f)
            if raw.get("obs", {}).get("password", "")[:7] != "secret-":
                problems.append("empty or partial settings read")
        except FileNotFoundError:
            problems.append("file missing")
        except Exception as e:  # noqa: BLE001
            problems.append(f"unreadable: {type(e).__name__}")


threads = [threading.Thread(target=reader_proc) for _ in range(3)]
for t in threads:
    t.start()
for i in range(150):
    s.obs.port = 4000 + i
    config.save(s)
stop.set()
for t in threads:
    t.join()
check(not problems, f"150 saves with 3 readers hammering the file: never empty, cut off or missing ({set(problems) or 'clean'})")
check(config.load().obs.port == 4149, "the last save is what is on disk")
check([p.name for p in config.CONFIG_DIR.iterdir() if p.name.startswith(".")] == [], "no temporary files are left behind")

text = config.CONFIG_FILE.read_text()
text = "from_a_newer_build = 1\n" + text.replace("[obs]", "[obs]\nnewer_obs_field = true") \
    .replace("[appearance]", "[appearance]\nnewer_look = \"x\"") + "\n[section_of_the_future]\na = 1\n"
config.CONFIG_FILE.write_text(text)
config.invalidate_cache()
try:
    loaded = config.load()
    ok = loaded.obs.port == 4149
except Exception as e:  # noqa: BLE001
    ok = False
    print("   ", type(e).__name__, e)
check(ok, "settings written by a newer build (unknown fields and sections) still load")

# ---------------------------------------------------------------- thumbnails
from afterglow import thumbnails
clip = Path(HOME) / "c.mp4"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-t", "1",
                "-c:v", "libx264", "-preset", "ultrafast", str(clip)], check=True)
first = thumbnails.get_card_thumbnail(7, clip)
os.utime(clip, (time.time() + 50, time.time() + 50))      # the clip was trimmed: new mtime
second = thumbnails.get_card_thumbnail(7, clip)
other = thumbnails.get_card_thumbnail(70, clip)
left = sorted(p.name for p in thumbnails.CACHE_DIR.glob("*_card.jpg"))
check(first != second and second.exists() and not first.exists() and other.exists() and len(left) == 2,
      f"a re-made card thumbnail replaces the video's old one, other videos' are kept {left}")

print("\n%d failed" % len(fails) if fails else "\nall passed")
os._exit(1 if fails else 0)
