"""
Puppetry's v4 input visualizer (a user-arranged LAYOUT of keyboards, mice,
controllers and movement views): the new movement pieces (comet / mousepad /
joystick) and single layout elements ("el:<id>", stored as "el-<id>.mov") all
the way through afterglow -- vendored module, capture (clips.trigger_clip),
Settings (clip type dialog + global placements list the layout's elements),
previewer graph, Editor (attach, Properties, Render from input) and the
exported sidecar. Uses tests/fake_puppetry_overlay.py.

    QT_QPA_PLATFORM=offscreen python3 tests/test_overlay_layout_v4.py
"""
import filecmp
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="layout_v4_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
os.environ["FAKE_PUPPETRY_ELEMENTS"] = "kb:keyboard,pad1:controller,trail:comet"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow import clips, config, db, input_overlay as aio, keyframes, overlay_support as osup
from afterglow.nle import ops, overlay_export, render, save

FAILS = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)


def ff(*a):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, a)], check=True)


db.init_db()
config.load()
clips_dir = config.load().clips_path()
clips_dir.mkdir(parents=True, exist_ok=True)

# ---- the vendored module is v4, byte-identical to the prep package
check(filecmp.cmp(ROOT / "afterglow/input_overlay.py", ROOT / "integrations/puppetry_input_overlay/afterglow_input_overlay.py",
                  shallow=False), "afterglow/input_overlay.py is byte-identical to the v4 prep package")
check(all(p in aio.PIECES for p in ("comet", "mousepad", "joystick")) and aio.piece_file("el:pad1") == "el-pad1.mov",
      "v4 pieces: comet / mousepad / joystick, el:<id> -> el-<id>.mov")

# ---- the layout, labels, placements
check([e["id"] for e in osup.layout_elements(0)] == ["kb", "pad1", "trail"], "layout elements come from Puppetry's status")
allp = osup.all_pieces()
check(allp[:len(aio.PIECES)] == list(aio.PIECES) and allp[len(aio.PIECES):] == ["el:kb", "el:pad1", "el:trail"],
      "pickable pieces = the standard ones + one per layout element")
check(osup.piece_label("el:pad1") == "Layout: pad1 (controller)" and osup.piece_label("comet") == "Movement: comet",
      "pieces get readable names")
pl = osup.resolve_placements({"el:pad1": {"x": 0.3}}, {"comet": {"x": 0.7, "rotation": 15}}, osup.element_types())
check(pl["el:pad1"]["x"] == 0.3 and pl["el:pad1"]["w"] == aio.DEFAULT_PLACEMENT["controller"]["w"]
      and pl["comet"]["x"] == 0.7 and pl["comet"]["rotation"] == 15.0 and pl["el:pad1"]["rotation"] == 0.0,
      "placements: el:<id> pieces default by their element type; rotation kept on every piece")

# ---- capture through the real pipeline, with a layout element + a movement view
class FakeOBS:
    def __init__(self, s):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_fps(self):
        return 30.0

    def save_replay_buffer(self, on_sent=None):
        if on_sent:
            on_sent(time.time())
        raw = Path(HOME) / f"raw_{time.time_ns()}.mp4"
        ff("-f", "lavfi", "-i", "color=c=0x203040:s=320x180:r=30:d=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", raw)
        return raw


clips.OBSClient = FakeOBS
clips.POST_SAVE_SETTLE_SECONDS = 0
clips.play_sound = lambda p: None
cfg = clips.create_clip_config(name="Layout", length_seconds=5, hotkey="ctrl+f8", overlay_enabled=True,
                               overlay_pieces=["keyboard", "comet", "el:pad1"],
                               overlay_placements={"el:pad1": {"x": 0.4, "y": 0.6, "w": 0.2, "rotation": 10,
                                                               "visible": True}})
video = clips.trigger_clip(cfg.id)
clips.wait_for_overlays(120)
sc = osup.load_sidecar(video.path)
check(sc is not None and set(sc["pieces"]) == {"keyboard", "comet", "el:pad1"},
      f"capture makes keyboard + comet + a single layout element ({sc and sorted(sc['pieces'])})")
if sc:
    check(sc["pieces"]["el:pad1"]["file"] == "el-pad1.mov" and (Path(sc["dir"]) / "el-pad1.mov").exists(),
          "the element piece is stored as el-pad1.mov")
    p_ = osup.placements_of(sc)["el:pad1"]
    check((p_["x"], p_["w"], p_["rotation"]) == (0.4, 0.2, 10.0), "the clip type's placement for the element is used")
    files, graph = osup.preview_graph(sc, 320, 180, True)
    check(any(f.endswith("el-pad1.mov") for f in files) and graph.count("overlay=") == 3,
          "the previewer graph draws all three (element included)")

# unknown element id: that piece fails, the others are kept, the clip is never lost
cfg2 = clips.update_clip_config(cfg.id, overlay_pieces=["keyboard", "el:gone"])
v2 = clips.trigger_clip(cfg2.id)
clips.wait_for_overlays(120)
sc2 = osup.load_sidecar(v2.path)
check(Path(v2.path).exists() and sc2 is not None and set(sc2["pieces"]) == {"keyboard"}
      and "el:gone" in sc2.get("errors", {}), "an element no longer in the layout fails alone (clip + others kept)")

# ---- Settings: the clip type dialog and the global page list the layout's elements
from afterglow.gui.overlay_options import InputOverlaySettingsPage, OverlayOptionsDialog
dlg = OverlayOptionsDialog(["keyboard", "el:pad1"], True, 0.0, {"el:pad1": {"x": 0.4, "y": 0.6, "w": 0.2}})
check(set(dlg._piece_checks) >= {"comet", "mousepad", "joystick", "el:kb", "el:pad1", "el:trail"}
      and dlg._piece_checks["el:pad1"].isChecked() and not dlg._piece_checks["el:kb"].isChecked(),
      "clip type dialog: movement views + every layout element as a checkbox")
dlg._piece_checks["el:trail"].setChecked(True)
vals = dlg.result_values()
check(vals["overlay_pieces"] == ["keyboard", "el:pad1", "el:trail"] and abs(vals["overlay_placements"]["el:pad1"]["x"] - 0.4) < 1e-9,
      "…and its result keeps element pieces + their placements")
dlg_old = OverlayOptionsDialog(["el:retired"], True, 0.0, {})
check("el:retired" in dlg_old._piece_checks, "a stored element that left the layout is still shown (not silently dropped)")
page = InputOverlaySettingsPage(config.load())
check({"el:kb", "el:pad1", "el:trail", "comet"} <= set(page.editor._rows), "global placements list the layout elements too")

# ---- Editor: attach, render from input, export sidecar
project = save.new_project_for_file(str(video.path))
seg = next(iter(project.all_segments()))
names = ops.segment_overlay_names(seg)
check(set(names) == {"keyboard", "comet", "el:pad1"}, f"the Editor attaches every piece, element included ({names})")
r = render.Renderer(project)
on = r.frame(1.0, 320, 180)
r.overlays = False
off = r.frame(1.0, 320, 180)
check(on.constBits().tobytes() != off.constBits().tobytes(), "the Editor preview draws them")
r.close()
from afterglow.gui.advanced_editor.controller import EditorController
from afterglow.gui.advanced_editor.properties import PropertiesPanel
ctl = EditorController()
ctl.set_project(project, "import", str(video.path))
props = PropertiesPanel(ctl)
ctl.set_selection([seg.id], anchor=seg.id)
app.processEvents()
check("ov_el:pad1_x" in props._fields and props._ov_render_combo.findData("el:trail") >= 0,
      "Properties: a row for the element; Render can add another layout element")
err = ctl.add_overlay_piece("el:trail")
check(err is None and "el:trail" in ops.segment_overlay_names(ctl.project.find_segment(seg.id)[1]),
      f"Render from input adds a layout element to the clip ({err})")
out = Path(HOME) / "export.mp4"
render.export(ctl.project, str(out))
man = overlay_export.write_sidecar(ctl.project, out)
check(man is not None and {"el:pad1", "el:trail"} <= set(man["pieces"]) and man["pieces"]["el:trail"]["file"] == "el-trail.mov"
      and (osup.sidecar_dir(out) / "el-trail.mov").exists(), "the exported sidecar keeps element pieces as el-<id>.mov")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
