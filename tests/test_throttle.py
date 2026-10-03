"""
Processing throttle: which processes it targets, the duty cycle really pausing them (measured CPU
time), auto mode's load -> duty mapping, the bypass file, nothing left paused when it stops; the
clip indicator's clickable processing element (rects, click toggles the bypass, the
"THROTTLING = OFF" label, the bypass ending with the last capture); the Settings control and the
start event's flag.

    QT_QPA_PLATFORM=offscreen python3 tests/test_throttle.py
"""
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="throttle_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["XDG_DATA_HOME"] = str(Path(HOME) / ".local" / "share")
os.environ["XDG_RUNTIME_DIR"] = HOME
os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow import throttle, config
from afterglow.indicator import draw
from afterglow.indicator.helper import IndicatorHelper
from afterglow.indicator.paint import StackPainter
from afterglow.indicator import model as M

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


# ---------------------------------------------------------------- fake heavy processes
BIN = Path(HOME) / "bin"
BIN.mkdir()
busy = BIN / "ffmpeg"                       # a busy loop that looks like ffmpeg (argv[1] of its interpreter)
busy.write_text(f"#!{sys.executable}\nimport time\nt = time.time()\nwhile time.time() - t < 30:\n    pass\n")
busy.chmod(0o755)
spawner = BIN / "puppetry-overlay"          # a heavy tool that starts a child of its own
spawner.write_text("#!/bin/sh\nsleep 30 &\nwait\n")
spawner.chmod(0o755)


def cpu_ticks(pid):
    s = throttle._stat(pid)
    return s[1] if s else 0


def state(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        return raw[raw.rfind(")") + 2]
    except OSError:
        return "?"


# ---------------------------------------------------------------- targets
p_busy = subprocess.Popen([str(busy)])
p_spawn = subprocess.Popen([str(spawner)])
p_plain = subprocess.Popen(["sleep", "30"])              # not heavy: never touched
time.sleep(0.4)
t = throttle.throttle_targets(os.getpid())
kids_of_spawner = [pid for pid in t if throttle._stat(pid) and throttle._stat(pid)[0] == p_spawn.pid]
check(p_busy.pid in t and p_spawn.pid in t, "targets: ffmpeg / puppetry-overlay children of this process")
check(len(kids_of_spawner) >= 1, "...and what they start")
check(p_plain.pid not in t, "...but no other child (a plain `sleep`)")

# ---------------------------------------------------------------- auto mapping
check(throttle.auto_duty(10) == 1.0 and throttle.auto_duty(throttle.AUTO_LOW) == 1.0, "auto: full speed while the machine is mostly idle")
check(throttle.auto_duty(100) == throttle.AUTO_MIN_DUTY and throttle.auto_duty(throttle.AUTO_HIGH) == throttle.AUTO_MIN_DUTY,
      "auto: held to the minimum duty when CPU / GPU are near full")
mid = throttle.auto_duty((throttle.AUTO_LOW + throttle.AUTO_HIGH) / 2)
check(throttle.AUTO_MIN_DUTY < mid < 1.0, f"auto: in between, in between ({mid:.2f})")

# ---------------------------------------------------------------- the duty cycle really slows the work
mode = {"m": "off"}
th = throttle.Throttler(lambda: mode["m"])
th.start()


def measure(seconds=2.0):
    a = cpu_ticks(p_busy.pid)
    time.sleep(seconds)
    return cpu_ticks(p_busy.pid) - a


time.sleep(0.6)
full = measure()
mode["m"] = "heavy"
time.sleep(0.8)
heavy = measure()
ratio = heavy / max(1, full)
check(full > 100, f"off: the busy process runs flat out ({full} ticks in 2 s)")
check(0.08 < ratio < 0.40, f"heavy: it gets ~20% of that ({ratio:.2f})")
check(th.duty == throttle.DUTY["heavy"], "...the throttler reports the heavy duty")
throttle.set_bypass(True)
time.sleep(0.8)
byp = measure()
check(byp / max(1, full) > 0.75 and th.duty == 1.0, f"bypass file: back to full speed ({byp / max(1, full):.2f})")
throttle.set_bypass(False)
time.sleep(0.8)
again = measure()
check(again / max(1, full) < 0.45, f"bypass removed: throttled again ({again / max(1, full):.2f})")
mode["m"] = "medium"
time.sleep(0.8)
med = measure()
check(0.25 < med / max(1, full) < 0.65, f"medium: ~45% ({med / max(1, full):.2f})")
mode["m"] = "auto"
time.sleep(1.5)
check(0.0 <= th.pressure <= 100.0 and throttle.AUTO_MIN_DUTY <= th.duty <= 1.0, f"auto: reads the load ({th.pressure:.0f}% -> duty {th.duty:.2f})")
mode["m"] = "heavy"
time.sleep(0.6)
th.stop()
time.sleep(0.2)
check(state(p_busy.pid) not in ("T", "t"), f"stopping the throttler never leaves a process paused (state {state(p_busy.pid)})")
check(not th.stopped, "...nothing is recorded as paused")
for pr in (p_busy, p_spawn, p_plain):
    pr.kill()
for pid in kids_of_spawner:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
check(throttle.MODES == ("off", "light", "medium", "heavy", "auto") and config.AppSettings().processing_throttle == "off",
      "modes: off / light / medium / heavy / auto, off by default")

# ---------------------------------------------------------------- the indicator's clickable processing element
NOW = [0.0]
h = IndicatorHelper(clock=lambda: NOW[0], force_layer_shell=False)


def ev(cid, name, t, style=None):
    NOW[0] = t
    h.handle({"id": cid, "event": name, **({"style": style} if style else {})})


style = {"style": "clapper", "enter": "fade", "exit": "fade", "mode": "circle", "throttle": True, "size": 156}
ev("a", "start", 0.0, style)
ev("a", "clap", 1.0)
NOW[0] = 1.2
h.step()
key = next(iter(h.surfaces))
surf = h.surfaces[key]
check(surf.painter.click_rects(h.model, key, NOW[0]) == [], "not clickable before processing")
ev("a", "processing", 2.0)
NOW[0] = 4.0
h.step()
rects = surf.painter.click_rects(h.model, key, NOW[0])
check(len(rects) == 1 and rects[0].width() > 10, f"the loading circle is clickable while processing ({rects})")
check(surf._click_rects and not surf.testAttribute(Qt.WA_TransparentForMouseEvents), "...the surface takes clicks there")


def click(at: QPointF):
    e = QMouseEvent(QMouseEvent.MouseButtonPress, at, surf.mapToGlobal(at.toPoint()), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    surf.mousePressEvent(e)


click(QPointF(5, 5))
check(not h.bypass, "a click elsewhere on the surface does nothing")
click(rects[0].center())
check(h.bypass and throttle.bypassed(), "clicking it turns throttling off (the bypass file exists)")
img = QImage(int(surf.surface[0]), int(surf.surface[1]), QImage.Format_ARGB32_Premultiplied)
img.fill(Qt.transparent)
p = QPainter(img)
surf.painter.paint_stack(p, h.model, key, NOW[0])
p.end()
label = sum(1 for y in range(0, int(rects[0].top())) for x in range(img.width())
            if abs(QColor.fromRgba(img.pixel(x, y)).red() - 255) < 30 and abs(QColor.fromRgba(img.pixel(x, y)).green() - 0xd3) < 40
            and QColor.fromRgba(img.pixel(x, y)).blue() < 120 and QColor.fromRgba(img.pixel(x, y)).alpha() > 200)
check(label > 40, f"\"THROTTLING = OFF\" shows above it ({label} label pixels)")
click(rects[0].center())
check(not h.bypass and not throttle.bypassed(), "clicking again turns throttling back on")
click(rects[0].center())
check(h.bypass, "...and off again")
ev("a", "done", 6.0)
NOW[0] = 20.0
h.step()
check(not h.bypass and not throttle.bypassed(), "the bypass ends with the last capture (it is temporary)")

# throttling off: nothing is clickable, the surface stays click-through
ev("b", "start", 30.0, dict(style, throttle=False))
ev("b", "clap", 31.0)
ev("b", "processing", 32.0)
NOW[0] = 34.0
h.step()
key_b = next(iter(h.surfaces))
check(h.surfaces[key_b].painter.click_rects(h.model, key_b, NOW[0]) == [] and h.surfaces[key_b].testAttribute(Qt.WA_TransparentForMouseEvents),
      "throttling off: the processing element is not clickable, the surface stays click-through")
check(M.Style.from_dict({"throttle": True}).throttle and not M.Style.from_dict({}).throttle, "the style carries the throttle flag")

# ---------------------------------------------------------------- start event + Settings
from afterglow import indicator_client
st = config.load()
st.processing_throttle = "auto"
config.save(st)
check(indicator_client.build_style(config.load())["throttle"] is True, "a throttle mode other than off marks the start event clickable")
st.processing_throttle = "off"
config.save(st)
check(indicator_client.build_style(config.load())["throttle"] is False, "...off does not")

from afterglow import db
db.init_db()
from afterglow.gui.settings_page import SettingsPage
page = SettingsPage()
check(page.throttle_combo.currentData() == "off" and page.throttle_combo.count() == 5, "Settings: the Throttle list, Off by default")
page.throttle_combo.setCurrentIndex(page.throttle_combo.findData("auto"))
page._save()
check(config.load().processing_throttle == "auto", "...and the choice is saved")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
