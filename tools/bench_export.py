"""
Editor export benchmark: one 30 s 2560x1440 120 fps clip on the timeline,
exported (nle.render.export) in several shapes:

  plain      nothing changed                       (everything is copied)
  caption    a 4 s text caption over part of it    (4 s rendered, the rest copied)
  effects    caption + a zoom + a fade out         ("some effects": the typical export)
  all        a caption across the whole clip       (every frame rendered: the worst case)

    QT_QPA_PLATFORM=offscreen python3 tools/bench_export.py                 # all four
    QT_QPA_PLATFORM=offscreen python3 tools/bench_export.py effects --profile /tmp/exp.prof
    ... --seconds 10 --size 1920x1080 --fps 60

Each export goes to a new file (no chunk reuse from a previous export), then
`effects` is exported a second time over itself after a small caption change
(what re-exporting after a tweak costs).
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("cases", nargs="*", default=["plain", "caption", "effects", "all"])
ap.add_argument("--size", default="2560x1440")
ap.add_argument("--fps", type=int, default=120)
ap.add_argument("--seconds", type=float, default=30.0)
ap.add_argument("--keyint", type=float, default=2.0)
ap.add_argument("--cache", default="/tmp/afterglow_bench_cache")
ap.add_argument("--profile", default="")
ap.add_argument("--no-smart", action="store_true")
args = ap.parse_args()

HOME = tempfile.mkdtemp(prefix="bench_export_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

os.makedirs(args.cache, exist_ok=True)
SRC = Path(args.cache) / f"clip_{args.size}_{args.fps}_{args.keyint:g}_{args.seconds:g}.mp4"
if not SRC.exists():
    print(f"making {SRC.name} (once)...", flush=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={args.size}:rate={args.fps}", "-f", "lavfi", "-i", "sine=f=440",
                    "-t", f"{args.seconds:g}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-g", str(round(args.keyint * args.fps)), "-keyint_min", str(round(args.keyint * args.fps)),
                    "-sc_threshold", "0", "-bf", "0", "-c:a", "aac", str(SRC)], check=True)

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from afterglow.gui import fonts  # noqa: E402
fonts.load_bundled_fonts()
from afterglow.nle import media, ops, render  # noqa: E402
from afterglow.nle.model import KIND_TEXT, Part, Segment, TextStyle, default_project  # noqa: E402

W, H = (int(x) for x in args.size.split("x"))


def caption(start, length, text="NICE SHOT"):
    return Segment(start=start, parts=[Part(kind=KIND_TEXT, src_in=0, src_out=length, has_audio=False,
                                            text=TextStyle(text=text, font_size=0.12, color="#ffffff"))])


def build(case):
    p = default_project(W, H, float(args.fps))
    clip = ops.add_media(p, media.probe(str(SRC)), start=0, track_index=1)
    d = args.seconds
    if case in ("caption", "effects"):
        ops.place(p, caption(d * 0.3, min(4.0, d * 0.2)), 0, d * 0.3)
    if case == "effects":
        # zoom on the back third, fade out at the very end
        a, b = ops.split_segment(p, clip.id, d * 0.6)
        b2, c = ops.split_segment(p, b.id, d * 0.75)
        ops.set_zoom(p, [b2.id], 1.4, 0.4, 0.4)
        ops.set_fades(p, [c.id], fade_out=1.0)
    if case == "all":
        ops.place(p, caption(0.0, d, "WATERMARK"), 0, 0.0)
    return p


def run(case, out, project=None):
    project = project or build(case)
    t = time.perf_counter()
    render.export(project, out, smart=not args.no_smart)
    dt = time.perf_counter() - t
    st = dict(render.LAST_EXPORT_STATS)
    n = round(args.seconds * args.fps)
    brief = {k: v for k, v in st.items() if isinstance(v, (int, float, str))}
    print(f"{case:9s} {dt:7.2f}s  ({n / dt:6.0f} frames/s, {args.seconds / dt:4.2f}x realtime)  "
          f"{os.path.getsize(out) / 1e6:5.0f} MB  {brief}", flush=True)
    return project


print(f"{args.size} {args.fps} fps, {args.seconds:g} s, keyframes every {args.keyint:g} s, cpus={os.cpu_count()}")
for case in args.cases:
    out = os.path.join(HOME, f"{case}.mp4")
    if args.profile:
        import cProfile
        cProfile.run("run(case, out)", args.profile)
        continue
    project = run(case, out)
    if case == "effects":
        for seg in project.all_segments():
            if seg.parts and seg.parts[0].kind == KIND_TEXT:
                seg.parts[0].text.text = "NICE SHOT!"
        run("  re-export", out, project)
os._exit(0)
