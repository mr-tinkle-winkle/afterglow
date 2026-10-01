"""
Integration test for afterglow/input_overlay.py (the vendored Puppetry module) against a real
`puppetry-overlay` (Puppetry installed, or PUPPETRY_OVERLAY pointing at it)
and ffmpeg. Private HOME/XDG_RUNTIME_DIR; writes a synthetic input buffer +
status file exactly as FORMAT.md describes, makes a test clip, then runs the
whole flow: start_clip -> finish_clip (sidecar) -> load_sidecar ->
placements -> mpv graph (checked by running it through ffmpeg) -> export.

    python3 test_afterglow_input_overlay.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ["HOME"] = tempfile.mkdtemp()
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from afterglow import input_overlay as aio  # noqa: E402

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


def rgba_at(path, t, x, y, extra=()):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), *extra, "-i", str(path), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True).stdout
    w = int(json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                       "stream=width", "-of", "json", str(path)], capture_output=True,
                                      text=True).stdout)["streams"][0]["width"])
    i = (y * w + x) * 4
    return raw[i:i + 4]


def main() -> int:
    if not aio.find_tool():
        print("SKIP: puppetry-overlay not found (install Puppetry or set PUPPETRY_OVERLAY)")
        return 0
    run = Path(os.environ["XDG_RUNTIME_DIR"]) / "puppetry"
    run.mkdir(parents=True)
    ok, why = aio.available()
    check("available() is False (with a reason) when the helper isn't running", not ok and why)

    t_save = time.time()
    T = t_save - 4.0                       # the clip will be 4 s long
    buf = run / "input_buffer.jsonl"
    lines = [{"format": "puppetry-input-buffer", "version": 1, "clock": "unix", "start": T - 10, "length_s": 4.0,
              "held_at_start": {}, "axes_at_start": {"r": {}, "m": {}}},
             {"t": T + 1.0, "e": "kd", "k": "KEY_W", "s": "r"},
             {"t": T + 1.2, "e": "kd", "k": "KEY_E", "s": "m"},
             {"t": T + 1.3, "e": "kd", "k": "BTN_SOUTH", "s": "r"},
             {"t": T + 1.3, "e": "ax", "a": "LX", "v": -0.8, "s": "r"},
             {"t": T + 2.0, "e": "mv", "dx": -40, "dy": 0, "s": "r"},
             {"t": T + 2.2, "e": "mv", "dx": 0, "dy": -30, "s": "r"},
             {"t": T + 3.0, "e": "ku", "k": "KEY_W", "s": "r"}]
    buf.write_text("".join(json.dumps(x) + "\n" for x in lines))
    (run / "overlay_status.json").write_text(json.dumps({
        "pid": 1, "updated": time.time(), "daemon_connected": True, "pages": {},
        "replay": {"enabled": True, "file": str(buf), "length_s": 4.0, "length_source": "obs", "extra_s": 5},
        "obs": "connected"}))
    ok, why = aio.available()
    check("available() reads the status file", ok and "4 s" in why)

    d = Path(tempfile.mkdtemp())
    clip = d / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:size=1280x720:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=duration=4", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(clip)], check=True)
    clip_bytes = clip.read_bytes()

    t0 = time.time()
    glob = {"mouse": {"x": 0.9, "y": 0.1, "w": 0.08}, "keyboard": {"x": 0.3, "y": 0.3, "w": 0.3}}
    ctype = {"keyboard": {"x": 0.01, "y": 0.8, "w": 0.3}}      # this clip type moves only the keyboard
    starting = aio.resolve_placements(glob, ctype)
    check("placement defaults: clip type > global > built-in, per piece",
          starting["keyboard"]["x"] == 0.01 and starting["mouse"]["x"] == 0.9
          and starting["controller"] == dict(aio.DEFAULT_PLACEMENT["controller"], visible=True))
    job = aio.start_clip(t_save, aio.OverlaySettings(pieces=["keyboard", "mouse", "controller"], fps=30,
                                                     placements=starting))
    buf.write_text("")                     # the live buffer moves on; the job froze its own copy
    manifest = aio.finish_clip(job, clip)
    print(f"  capture -> sidecar took {time.time() - t0:.1f} s")
    job.cleanup()
    side = aio.sidecar_dir(clip)
    check("finish_clip writes a sidecar with every piece + the inputs + a manifest",
          set(manifest["pieces"]) == {"keyboard", "mouse", "controller"} and (side / "inputs.jsonl").exists()
          and (side / "manifest.json").exists() and not manifest["errors"])
    check("the clip itself is untouched", clip.read_bytes() == clip_bytes)
    kb = side / "keyboard.mov"
    dur = float(json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                                           str(kb)], capture_output=True, text=True).stdout)["format"]["duration"])
    check("pieces are cut to the clip's length", abs(dur - 4.0) < 0.1)
    corner = rgba_at(kb, 1.5, 1, 1)
    check("pieces are transparent (alpha 0 around the picture)", corner[3] == 0)

    sc = aio.load_sidecar(clip)
    check("load_sidecar finds it (so the previewer shows its toggle)", sc is not None)
    check("a new clip starts at its clip type's / the global placements",
          sc["pieces"]["keyboard"]["placement"]["y"] == 0.8 and sc["pieces"]["mouse"]["placement"]["x"] == 0.9)
    check("a clip without one -> None (no toggle)", aio.load_sidecar(d / "other.mp4") is None)
    pl = aio.placements_of(sc)
    pl["keyboard"].update(x=0.05, y=0.05, w=0.5)                   # the editor moved + resized it
    pl["controller"]["visible"] = False                            # ... and hid the controller
    aio.save_placements(sc, pl)
    check("placements persist in the manifest", aio.placements_of(aio.load_sidecar(clip))["keyboard"]["w"] == 0.5
          and aio.placements_of(aio.load_sidecar(clip))["controller"]["visible"] is False)

    files, graph = aio.mpv_overlay_args(sc, pl, 1280, 720)
    check("mpv args: every piece is an external file, hidden ones are left out of the graph",
          len(files) == 3 and "vid4" not in graph and graph.endswith("[vo]"))
    _f, off = aio.preview_overlay_args(aio.load_sidecar(clip), 1280, 720, enabled=False)
    check("previewer: toggled off -> pass-through graph", off == "[vid1] null [vo]")
    _f, on = aio.preview_overlay_args(aio.load_sidecar(clip), 1280, 720, enabled=True)
    check("previewer: toggled on -> the clip's saved placements (same graph the editor made)", on == graph)
    # run the exact same graph through ffmpeg ([vidN] -> input N-1)
    ff_graph = re.sub(r"\[vid(\d+)\]", lambda m: f"[{int(m.group(1)) - 1}:v]", graph)
    prev = d / "preview_check.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), *sum((["-i", f] for f in files), []),
                        "-filter_complex", ff_graph, "-map", "[vo]", "-t", "2", str(prev)], capture_output=True, text=True)
    check("mpv graph is valid lavfi (ran it through ffmpeg)", r.returncode == 0 and prev.exists())
    if r.returncode:
        print(r.stderr[-500:])

    out = d / "export.mp4"
    res = aio.export(clip, sc, pl, out)
    check("export burns in the visible pieces (2 of 3)", res.get("pieces") == 2 and out.exists())
    # KEY_W sits in the keyboard piece at (0.05, 0.05) width 0.5 -> somewhere in the top-left half; held at t=2
    x0, y0, w, h = aio.piece_rect(sc, "keyboard", pl["keyboard"], 1280, 720)
    region_lit = False
    for yy in range(y0, y0 + h, 6):
        px = rgba_at(out, 2.0, x0 + int(w * 0.17), yy)
        if px and px[0] > 150 and px[0] > px[2] + 40:
            region_lit = True
            break
    check("export: the held key shows where the keyboard was placed", region_lit)

    re_ = aio.rerender(clip, sc, "simple", fps=30)
    check("rerender adds / refreshes a piece from the stored inputs", (side / "simple.mov").exists()
          and "simple" in aio.load_sidecar(clip)["pieces"] and re_["width"] > 0)

    res2 = aio.composite(clip, d / "onepass.mp4", clip_end=t_save, buffer=side / "inputs.jsonl", mode="full")
    check("one-pass composite still works", res2.get("coverage") == "full")
    try:
        aio.composite(d / "missing.mp4", d / "x.mp4", clip_end=t_save)
        err = False
    except aio.OverlayError:
        err = True
    check("errors come back as OverlayError", err)
    print(f"\noutputs in {d}")
    print("ALL PASS" if not fails else f"FAILED: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
