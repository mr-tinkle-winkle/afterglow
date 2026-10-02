"""
Input overlay in the Advanced Editor UI (plan step 8): the Properties
"Input Overlay" group, the canvas handles (move / size / rotate a piece),
the preview Overlay toggle, the timeline menu and Detach Input Overlay, and
the Render-from-input button. Real widgets, real mouse events, offscreen.

    QT_QPA_PLATFORM=offscreen python3 tests/test_overlay_editor_ui.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="overlay_nle_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow import config, db, editor, library, overlay_support as osup
from afterglow import input_overlay as aio
from afterglow.nle import media, model, ops, render, save, store, overlay_export
from afterglow.nle.history import History

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def ff(*a):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def dur(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]))



from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from afterglow.gui.advanced_editor.controller import EditorController
from afterglow.gui.advanced_editor.properties import PropertiesPanel
from afterglow.gui.advanced_editor.preview import PreviewPanel

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def ff(*a):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


db.init_db()
config.load()
clips_dir = config.load().clips_path()
clips_dir.mkdir(parents=True, exist_ok=True)
W, H = 320, 180
path = clips_dir / "ui.mp4"
ff("-f", "lavfi", "-i", f"color=c=0x1020a0:s={W}x{H}:r=30:d=6", "-f", "lavfi", "-i", "sine=f=330:d=6",
   "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path)
ts = time.time()
pl = osup.resolve_placements({"keyboard": {"x": 0.1, "y": 0.2, "w": 0.5, "rotation": 0},
                              "mouse": {"x": 0.8, "y": 0.5, "w": 0.1, "rotation": 0}})
job = aio.start_clip(ts, aio.OverlaySettings(pieces=["keyboard", "mouse"], fps=30, placements=pl))
aio.finish_clip(job, path, clip_end=ts)
job.cleanup()
osup.save_placements(osup.load_sidecar(path), pl)

project = save.new_project_for_file(str(path))
ctl = EditorController()
ctl.set_project(project, "import", str(path))
props = PropertiesPanel(ctl)
prev = PreviewPanel(ctl)
prev.resize(900, 600)
prev.show()
app.processEvents()
seg = next(iter(project.all_segments()))
SID = seg.id


def S():
    return ctl.project.find_segment(SID)[1]

ctl.set_selection([SID], anchor=SID)
app.processEvents()

# ---- the Properties group
check("ov_master" in props._fields and "ov_keyboard_x" in props._fields and "ov_mouse_rotation" in props._fields,
      "Properties shows an Input Overlay group with a row per piece")
check(abs(props._fields["ov_keyboard_x"].value() - 10.0) < 0.01 and abs(props._fields["ov_keyboard_w"].value() - 50.0) < 0.01,
      "piece values come from the clip's placement")
props._fields["ov_keyboard_x"].setValue(25.0)
props._end_edit()
kb = lambda: next(o for p in S().parts for o in p.overlays if o.name == "keyboard")
check(abs(kb().x - 0.25) < 1e-6, "editing X in Properties moves the piece")
ctl.undo()
check(abs(kb().x - 0.1) < 1e-6, "…and is one undo step")
props._fields["ov_mouse_visible"].click()
check(not next(o for p in S().parts for o in p.overlays if o.name == "mouse").visible, "Shown checkbox hides a piece")
ctl.undo()
props._fields["ov_master"].click()
check(not any(o.visible for p in S().parts for o in p.overlays), "master checkbox hides every piece")
ctl.undo()

# ---- the preview toggle + canvas handles
check(not prev.overlay_btn.isHidden(), "preview shows the Overlay toggle for a clip with an overlay")
prev.overlay_btn.setChecked(False)
check(prev.renderer.overlays is False, "toggle off hides the overlay in the preview renderer")
prev.overlay_btn.setChecked(True)
check(prev.renderer.overlays is True, "toggle on shows it again")

canvas = prev.canvas
fr = canvas.frame_rect()
geo = canvas.piece_geometry(S(), kb())
center = geo[0].map(geo[1].center())


def mouse(kind, pos, mods=Qt.NoModifier):
    ev = QMouseEvent(kind, QPointF(pos), QPointF(canvas.mapToGlobal(pos.toPoint())), Qt.LeftButton,
                     Qt.LeftButton if kind != QMouseEvent.MouseButtonRelease else Qt.NoButton, mods)
    return ev


ctl.pick_overlay(None)
canvas.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, center))
check(ctl.overlay_pick == "keyboard", "clicking a piece on the preview picks it")
x0 = kb().x
canvas.mouseMoveEvent(mouse(QMouseEvent.MouseMove, center + QPointF(fr.width() * 0.1, 0)))
canvas.mouseReleaseEvent(mouse(QMouseEvent.MouseButtonRelease, center + QPointF(fr.width() * 0.1, 0)))
check(abs(kb().x - (x0 + 0.1 / S().transform.scale)) < 0.02, f"dragging the piece moves it (x {x0:.2f} -> {kb().x:.2f})")
check(len(ctl.history._undo) >= 1 if hasattr(ctl.history, "_undo") else True, "the drag is recorded")
ctl.undo()
check(abs(kb().x - x0) < 1e-6, "…and undoes in one step")

# resize with a corner handle
geo = canvas.piece_geometry(S(), kb())
corner = canvas._handles(geo)["br"]
w0 = kb().w
canvas.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, corner))
far = center + (corner - center) * 1.5
canvas.mouseMoveEvent(mouse(QMouseEvent.MouseMove, far))
canvas.mouseReleaseEvent(mouse(QMouseEvent.MouseButtonRelease, far))
check(abs(kb().w - w0 * 1.5) < 0.02, f"dragging a corner resizes (w {w0:.2f} -> {kb().w:.2f})")
cx_after = (kb().x - 0.5) * 1 + kb().w / 2
check(abs(cx_after - ((x0 - 0.5) + w0 / 2)) < 0.02, "…about the piece's centre")
ctl.undo()

# rotate with the top handle
geo = canvas.piece_geometry(S(), kb())
rot = canvas._handles(geo)["rot"]
c = geo[0].map(geo[1].center())
canvas.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, rot))
import math
ang = math.radians(30)
v = rot - c
new = c + QPointF(v.x() * math.cos(ang) - v.y() * math.sin(ang), v.x() * math.sin(ang) + v.y() * math.cos(ang))
canvas.mouseMoveEvent(mouse(QMouseEvent.MouseMove, new))
canvas.mouseReleaseEvent(mouse(QMouseEvent.MouseButtonRelease, new))
check(abs(kb().rotation - 30) < 1.0, f"dragging the top handle rotates (rotation {kb().rotation:.1f})")
ctl.undo()

# clicking the bare video clears the piece pick
ctl.pick_overlay("keyboard")
canvas.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, QPointF(fr.left() + 6, fr.bottom() - 6)))
canvas.mouseReleaseEvent(mouse(QMouseEvent.MouseButtonRelease, QPointF(fr.left() + 6, fr.bottom() - 6)))
check(ctl.overlay_pick is None, "clicking the video clears the piece pick")

# ---- the preview image really contains the overlay (toggle changes pixels)
ctl.set_selection([SID], anchor=SID)
prev._render_now()
on = canvas.image.copy()
prev.overlay_btn.setChecked(False)
prev._render_now()
off = canvas.image.copy()
diff = sum(1 for yy in range(0, on.height(), 3) for xx in range(0, on.width(), 3) if on.pixel(xx, yy) != off.pixel(xx, yy))
check(diff > 20, f"the toggle changes the rendered preview ({diff} sampled pixels differ)")
prev.overlay_btn.setChecked(True)

# ---- Render from input
n_before = len(ops.segment_overlay_names(S()))
err = ctl.add_overlay_piece("controller")
check(err is None, f"Render from input adds a piece that wasn't captured ({err})")
check("controller" in ops.segment_overlay_names(S()) and len(ops.segment_overlay_names(S())) == n_before + 1,
      "…and attaches it to the clip")
ctl.undo()
check("controller" not in ops.segment_overlay_names(S()), "…as one undo step")

# ---- Detach Input Overlay through the controller (what the button/menu call)
ctl.set_selection([SID], anchor=SID)
ctl.detach_overlay()
app.processEvents()
dets = [s_ for s_ in project.all_segments() if s_.overlay_piece]
check(len(dets) == 2 and not ops.has_overlay(S()), "Detach Input Overlay makes one independent element per piece")
check(all(s_.id in ctl.selection for s_ in dets), "…and selects them")
check(prev._has_overlay() and not prev.overlay_btn.isHidden(), "the toggle stays for detached elements")
ctl.undo()
check(any(ops.has_overlay(s_) for s_ in project.all_segments()),
      "Detach is undoable")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
