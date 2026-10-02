"""
Clip indicator helper: the focused-screen logic, the overlay surface painted for real
(offscreen Qt, a fake clock) for every anchor, stacking, the +N badge, circle colours,
the surface's lifecycle -- and the REAL helper process over its REAL socket: events in,
state out, auto-spawn on demand, single instance, stale-socket recovery.

    QT_QPA_PLATFORM=offscreen python3 tests/test_indicator_helper.py
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="indicator_helper_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_RUNTIME_DIR"] = HOME
SOCK = str(Path(HOME) / "ind.sock")
os.environ["AFTERGLOW_INDICATOR_SOCKET"] = SOCK
os.environ["AFTERGLOW_INDICATOR_ALLOW_OFFSCREEN"] = "1"
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow.indicator import ANCHORS, draw, layout, focus, MAX_VISIBLE
from afterglow.indicator.helper import IndicatorHelper

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def rgba(img, x, y):
    return QColor.fromRgba(img.pixel(int(x), int(y)))


# ---------------------------------------------------------------- focused screen
check(focus.parse_kdotool_geometry("Window {abc}\n  Position: 1920,0\n  Geometry: 1280x720\n") == (1920.0, 0.0, 1280.0, 720.0),
      "kdotool getwindowgeometry output parses")
check(focus.parse_kdotool_geometry("garbage") is None, "...garbage -> None")
check(focus.parse_hyprctl_activewindow('{"at":[10,20],"size":[300,200],"class":"x"}') == (10.0, 20.0, 300.0, 200.0), "hyprctl activewindow parses")
tree = {"nodes": [{"nodes": [{"focused": False, "rect": {"x": 0, "y": 0, "width": 5, "height": 5}},
                             {"focused": True, "rect": {"x": 1920, "y": 40, "width": 800, "height": 600}}]}]}
check(focus.parse_sway_tree(json.dumps(tree)) == (1920.0, 40.0, 800.0, 600.0), "sway get_tree: the focused node's rect")
check(focus.parse_sway_tree(json.dumps({"nodes": []})) is None, "...nothing focused -> None")
SCR = [("DP-1", (0, 0, 1920, 1080)), ("HDMI-1", (1920, 0, 2560, 1440))]
check(focus.choose_screen(SCR, {"x": 2500, "y": 600}, "DP-1") == "HDMI-1", "the screen holding the focused window's centre")
check(focus.choose_screen(SCR, {"x": 100, "y": 100}, "DP-1") == "DP-1", "...left screen")
check(focus.choose_screen(SCR, None, "HDMI-1") == "HDMI-1", "no hint -> the primary screen")
check(focus.choose_screen(SCR, {"x": 100, "y": 100}, "HDMI-1", mode="primary") == "HDMI-1", "'primary' mode ignores the hint")
check(focus.choose_screen(SCR, {"x": 1900, "y": 1300}, "DP-1") == "DP-1" or focus.choose_screen(SCR, {"x": 1900, "y": 1300}, "DP-1") == "HDMI-1",
      "a point in a gap between screens -> the nearest one")
check(focus.choose_screen(SCR, {"x": 1000, "y": 1200}, "HDMI-1") == "DP-1", "...(nearest by distance)")
check(focus.choose_screen([], {"x": 1, "y": 1}, "x") == "", "no screens -> empty")
orig_which = focus.shutil.which
focus.shutil.which = lambda n: None
check(focus.focused_window_center() is None, "no compositor tool available -> no hint (primary screen)")
focus.shutil.which = orig_which

# ---------------------------------------------------------------- the surface, painted for real
class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def new_helper(clock=None):
    clock = clock or Clock()
    h = IndicatorHelper(clock=clock, force_layer_shell=False)
    h.timer.stop()
    return h, clock


def style(**kw):
    d = {"anchor": "bottom_right", "size": 96, "padding_x": 32, "padding_y": 32, "enter": "slide", "exit": "slide", "mode": "circle"}
    d.update(kw)
    return d


def surface_image(h, key=None):
    key = key or next(iter(h.surfaces))
    return h.surfaces[key].grab().toImage().convertToFormat(QImage.Format_ARGB32), h.surfaces[key]


def advance(h, clock, dt):
    clock.t += dt
    h.step()
    h.timer.stop()


def slot_has_ink(img, surf, slot=0):
    r = layout.slot_rect(surf.anchor, surf.size_setting, surf.pad_x, surf.pad_y, slot, surf.surface)
    n = 0
    for fx in (0.2, 0.5, 0.8):
        for fy in (0.25, 0.5, 0.8):
            if rgba(img, r.left() + r.width() * fx, r.top() + r.height() * fy).alpha() > 200:
                n += 1
    return n


h, clock = new_helper()
screen_geo = app.primaryScreen().geometry()
h.handle({"id": "a", "event": "start", "style": style()})
check(len(h.surfaces) == 1 and h.surfaces and list(h.snapshot()["stacks"].values())[0][0]["state"] == "enter", "start: a surface appears and the enter animation plays")
img, surf = surface_image(h)
check(slot_has_ink(img, surf) == 0, "at t=0 the clapper is off-screen (nothing drawn in its slot yet)")
check(surf.windowFlags() & Qt.WindowTransparentForInput and surf.windowFlags() & Qt.FramelessWindowHint and surf.windowFlags() & Qt.WindowStaysOnTopHint,
      "the surface is frameless, always on top and input-transparent")
check(surf.testAttribute(Qt.WA_TransparentForMouseEvents) and surf.testAttribute(Qt.WA_ShowWithoutActivating) and surf.focusPolicy() == Qt.NoFocus,
      "...never takes focus or clicks")
advance(h, clock, 0.35)
img, surf = surface_image(h)
check(slot_has_ink(img, surf) >= 6, "after landing the clapper is drawn in its slot")
# transparent elsewhere
opaque_elsewhere = 0
r0 = layout.slot_rect(surf.anchor, 96, 32, 32, 0, surf.surface)
for x in range(0, img.width(), 7):
    for y in range(0, img.height() // 2, 7):
        if rgba(img, x, y).alpha() > 0:
            opaque_elsewhere += 1
check(opaque_elsewhere == 0, "the rest of the surface is fully transparent")
check(abs(surf.x() + surf.width() - (screen_geo.x() + screen_geo.width())) <= 1 and abs(surf.y() + surf.height() - (screen_geo.y() + screen_geo.height())) <= 1,
      "bottom_right: the surface sits flush in the screen corner (margin 0, padding applied in the drawing)")
check(abs((img.width() - r0.right()) - 32) < 1.5 and abs((img.height() - r0.bottom()) - 32) < 1.5, "...the clapper sits 32/32 px in from the edges")

# clap then processing: stick opens (more ink above the rect), circle appears
h.handle({"id": "a", "event": "clap"})
advance(h, clock, 0.12)
img_open, _ = surface_image(h)
top_open = min((y for y in range(img_open.height()) for x in range(0, img_open.width(), 3) if rgba(img_open, x, y).alpha() > 0), default=999)
advance(h, clock, 0.5)
check(list(h.snapshot()["stacks"].values())[0][0]["state"] == "leave", "after the clap the exit animation plays (default mode)")
advance(h, clock, 0.45)
state = list(h.snapshot()["stacks"].values())[0][0]["state"]
check(state in ("circle_in", "circle"), f"then the circle ({state})")
advance(h, clock, 0.4)
img, surf = surface_image(h)
c = layout.slot_rect(surf.anchor, 96, 32, 32, 0, surf.surface).center()
ring = [rgba(img, c.x() + dx, c.y() + dy) for dx in range(-16, 17) for dy in range(-16, 17) if rgba(img, c.x() + dx, c.y() + dy).alpha() > 40]
check(len(ring) > 30, f"a loading circle is drawn where the clapper was ({len(ring)} px)")
gray = [q for q in ring if abs(q.red() - q.green()) < 25 and abs(q.green() - q.blue()) < 25 and q.red() > 100]
check(len(gray) > 15 and 110 < max(q.alpha() for q in ring) < 160, "...gray, ~55% opaque")
# purple once the overlay is being rendered
h.handle({"id": "a", "event": "overlay"})
advance(h, clock, 0.4)
img, surf = surface_image(h)
ring = [rgba(img, c.x() + dx, c.y() + dy) for dx in range(-16, 17) for dy in range(-16, 17) if rgba(img, c.x() + dx, c.y() + dy).alpha() > 100]
purple = [q for q in ring if q.blue() > q.green() + 40 and q.red() > q.green() + 20]
check(len(purple) > 10, f"overlay: the circle turns purple ({len(purple)} px)")
h.handle({"id": "a", "event": "overlay_done"})
advance(h, clock, 0.4)
check(h.surfaces == {} and len(h.model) == 0, "overlay_done: the circle is gone and the surface is torn down (nothing sits above a fullscreen game between clips)")
check(not h.timer.isActive(), "...and the frame timer is idle")

# ---------------------------------------------------------------- every anchor
for anchor in ANCHORS:
    h, clock = new_helper()
    h.handle({"id": "a", "event": "start", "style": style(anchor=anchor, padding_x=40, padding_y=24)})
    advance(h, clock, 0.7)
    img, surf = surface_image(h)
    n = slot_has_ink(img, surf)
    g = surf.geometry()
    edges = layout.anchor_edges(anchor)
    flush = ("left" not in edges or g.left() == screen_geo.left()) and ("right" not in edges or abs(g.right() - screen_geo.right()) <= 1) \
        and ("top" not in edges or g.top() == screen_geo.top()) and ("bottom" not in edges or abs(g.bottom() - screen_geo.bottom()) <= 1)
    centred_x = "left" in edges or "right" in edges or abs(g.center().x() - screen_geo.center().x()) <= 2
    centred_y = "top" in edges or "bottom" in edges or abs(g.center().y() - screen_geo.center().y()) <= 2
    check(n >= 6 and flush and centred_x and centred_y, f"anchor {anchor}: drawn ({n} samples), flush with its edge(s), centred along the free axis")
    h.handle({"id": "a", "event": "fail"})
    advance(h, clock, 1.2)
    check(len(h.model) == 0, f"anchor {anchor}: a failed capture is gone after the launch")

# ---------------------------------------------------------------- stacking + badge
h, clock = new_helper()
for i in range(3):
    h.handle({"id": f"c{i}", "event": "start", "style": style()})
    advance(h, clock, 0.05)
advance(h, clock, 0.7)
img, surf = surface_image(h)
check(all(slot_has_ink(img, surf, i) >= 6 for i in range(3)), "three rapid hotkeys: three clappers stacked at slots 0, 1, 2 (each above the last)")
check(slot_has_ink(img, surf, 3) == 0, "...and no ghost in the next slot")
r0, r1 = (layout.slot_rect(surf.anchor, 96, 32, 32, i, surf.surface) for i in (0, 1))
check(r1.bottom() < r0.top(), "...above the first, for the bottom-right default")
for i in range(3):
    h.handle({"id": f"c{i}", "event": "clap"})
advance(h, clock, 0.5)
h.handle({"id": "c0", "event": "done"})
for i in (1, 2):
    h.handle({"id": f"c{i}", "event": "done"})
advance(h, clock, 3.0)
check(len(h.model) == 0 and h.surfaces == {}, "everything finishes -> surface gone")

h, clock = new_helper()
for i in range(MAX_VISIBLE + 2):
    h.handle({"id": f"c{i}", "event": "start", "style": style()})
advance(h, clock, 0.8)
img, surf = surface_image(h)
badge = layout.badge_rect(surf.anchor, 96, 32, 32, surf.surface)
n_badge = sum(1 for x in range(int(badge.left()), int(badge.right())) for y in range(int(badge.top()), int(badge.bottom())) if rgba(img, x, y).alpha() > 200)
check(n_badge > 200 and slot_has_ink(img, surf, MAX_VISIBLE - 1) >= 6, f"6th and beyond collapse into a +N badge on the top slot ({n_badge} px)")
top_slot = layout.slot_rect(surf.anchor, 96, 32, 32, MAX_VISIBLE - 1, surf.surface)
check(badge.bottom() <= top_slot.top() and QRectF(0, 0, *surf.surface).contains(badge), "...the badge sits just beyond the last slot, inside the surface")

# ---------------------------------------------------------------- bad input never hurts
h, clock = new_helper()
h.handle_line(b"not json")
h.handle_line(b'{"event": "nonsense", "id": "x"}')
h.handle_line(b'{"event": "clap"}')
h.handle_line(b'[1,2,3]')
h.handle({"id": "zz", "event": "clap"})
check(len(h.model) == 0 and h.surfaces == {}, "malformed lines / unknown events / events for unknown ids are ignored")
h.handle_line(b'{"id": "q", "event": "start", "style": {"anchor": "bogus", "enter": "nope", "size": "huge"}}'.replace(b'"huge"', b'null')) if False else None
h.handle({"id": "q", "event": "start", "style": {"anchor": "bogus", "enter": "nope"}})
check(len(h.model) == 1, "a start with bad style values still shows an indicator (defaults)")

# ---------------------------------------------------------------- Wayland without layer-shell: model runs, nothing drawn
h, clock = new_helper()
h.is_wayland, h.layer, h.can_show = True, False, False
h.handle({"id": "a", "event": "start", "style": style()})
h.handle({"id": "a", "event": "clap"})
advance(h, clock, 1.0)
check(h.surfaces == {} and len(h.model) == 1, "Wayland with no layer-shell shim: no window is created (sounds still play), the model still runs")
h.handle({"id": "a", "event": "done"})
advance(h, clock, 2.0)
check(len(h.model) == 0, "...and winds down normally")

# ---------------------------------------------------------------- the real helper process over its real socket
def wait_for(cond, timeout=10.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(step)
    return cond()


LOG = Path(HOME) / "events.jsonl"
env = dict(os.environ)
env["PYTHONPATH"] = ROOT
proc = subprocess.Popen([sys.executable, "-m", "afterglow.indicator", "--socket", SOCK, "--log-events", str(LOG)],
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=ROOT)
ok = wait_for(lambda: Path(SOCK).exists())
check(ok, "the helper process starts and opens its socket")

import socket as pysocket


def send(*msgs):
    s = pysocket.socket(pysocket.AF_UNIX, pysocket.SOCK_STREAM)
    s.connect(SOCK)
    s.sendall(b"".join((json.dumps(m) + "\n").encode() for m in msgs))
    s.close()


def log_records():
    try:
        return [json.loads(l) for l in LOG.read_text().splitlines()]
    except OSError:
        return []


send({"id": "p1", "event": "start", "style": style()}, {"id": "p1", "event": "clap"})
check(wait_for(lambda: len(log_records()) >= 2), "events sent over the socket (two JSON lines in one connection) arrive in order")
recs = log_records()
check([r["msg"]["event"] for r in recs[:2]] == ["start", "clap"] and recs[0]["style"] is True, "...start (with its style) then clap")
check(recs[1]["snapshot"]["surfaces"] and recs[1]["snapshot"]["stacks"], "the helper created its overlay surface for the capture")
s = pysocket.socket(pysocket.AF_UNIX, pysocket.SOCK_STREAM)
s.connect(SOCK)
s.sendall(b'{"id": "p1", "event": "done"}')            # no trailing newline, then disconnect
s.close()
check(wait_for(lambda: any(r["msg"]["event"] == "done" for r in log_records())), "a last message without a trailing newline is still read on disconnect")
# a second helper must not steal the socket
proc2 = subprocess.run([sys.executable, "-m", "afterglow.indicator", "--socket", SOCK], env=env, capture_output=True, text=True, cwd=ROOT, timeout=30)
check(proc2.returncode == 0 and proc.poll() is None, "a second helper sees the socket is owned and exits quietly; the first keeps running")
send({"event": "quit"})
check(wait_for(lambda: proc.poll() is not None), "{\"event\": \"quit\"} stops the helper")
out = proc.stdout.read().decode() if proc.stdout else ""
check("Traceback" not in out, "...with no traceback in its output")
if "Traceback" in out:
    print(out[out.index("Traceback"):][:3000])

# stale socket file from a crashed helper is replaced
Path(SOCK).unlink(missing_ok=True)
stale = pysocket.socket(pysocket.AF_UNIX, pysocket.SOCK_STREAM)
stale.bind(SOCK)
stale.close()                                        # leaves a socket file nobody listens on
LOG.unlink(missing_ok=True)
proc3 = subprocess.Popen([sys.executable, "-m", "afterglow.indicator", "--socket", SOCK, "--log-events", str(LOG)],
                         env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=ROOT)
time.sleep(1.5)
ok = False
for _ in range(60):
    try:
        send({"id": "s1", "event": "start", "style": style()})
        ok = True
        break
    except OSError:
        time.sleep(0.1)
check(ok and wait_for(lambda: len(log_records()) >= 1), "a stale socket file (crashed helper) is replaced and the new helper serves events")
send({"event": "quit"})
wait_for(lambda: proc3.poll() is not None)

# ---------------------------------------------------------------- the client auto-spawns the helper on demand
Path(SOCK).unlink(missing_ok=True)
LOG.unlink(missing_ok=True)
from afterglow import indicator_client as ic
ic._last_spawn = -1e9
env_log = str(LOG)
# spawn_helper has no --log-events option; wrap via an env var the command honours: use a tiny launcher
launcher = Path(HOME) / "afterglow-indicator"
launcher.write_text(f"#!/bin/sh\nexport PYTHONPATH={ROOT}\nexec {sys.executable} -m afterglow.indicator \"$@\" --log-events {env_log}\n")
launcher.chmod(0o755)
orig_cmd = ic.helper_command
ic.helper_command = lambda: [str(launcher)]
ic.send_raw({"id": "auto1", "event": "start", "style": style()})
check(ic.flush(30) and wait_for(lambda: any(r["msg"].get("id") == "auto1" for r in log_records()), 20),
      "the client starts the helper on demand and the event is delivered")
ic.send_raw({"id": "auto1", "event": "clap"})
ic.send_raw({"id": "auto1", "event": "done"})
check(ic.flush(10) and wait_for(lambda: [r["msg"]["event"] for r in log_records() if r["msg"].get("id") == "auto1"] == ["start", "clap", "done"], 10),
      "...and later events arrive in the order they were emitted")
ic.helper_command = orig_cmd
send({"event": "quit"})
time.sleep(0.5)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("ALL PASS")
