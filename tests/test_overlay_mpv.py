"""
The previewer's overlay graph, verified in a REAL libmpv (the Puppetry side
only validated it through ffmpeg): external-files + lavfi-complex on a
clip with sound; overlay on / off / on again at runtime; rotation; audio
still plays. Frames are captured with mpv's `image` video output, so no GPU
or display is needed. Skips when libmpv isn't installed.

    QT_QPA_PLATFORM=offscreen python3 tests/test_overlay_mpv.py
"""
import locale
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="overlay_mpv_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["PUPPETRY_OVERLAY"] = str(Path(__file__).resolve().parent / "fake_puppetry_overlay.py")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
locale.setlocale(locale.LC_NUMERIC, "C")

try:
    import mpv
    mpv.MPV(vo="null").terminate()
except Exception as e:  # noqa: BLE001
    print(f"SKIP: libmpv not available ({e.__class__.__name__})")
    sys.exit(0)

from PIL import Image
from afterglow import input_overlay as aio, overlay_support as osup

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


work = Path(tempfile.mkdtemp(prefix="overlay_mpv_"))
clip = work / "c.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=30:d=6", "-f", "lavfi",
                "-i", "sine=f=440:d=6", "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", clip],
               check=True)


def make_sidecar(rotation):
    ts = time.time()
    pl = osup.resolve_placements({"keyboard": {"x": 0.1, "y": 0.2, "w": 0.5, "rotation": rotation},
                                  "mouse": {"x": 0.8, "y": 0.5, "w": 0.1}})
    j = aio.start_clip(ts, aio.OverlaySettings(pieces=["keyboard", "mouse"], fps=30, placements=pl))
    aio.finish_clip(j, clip, clip_end=ts)
    j.cleanup()
    sc = osup.load_sidecar(clip)
    osup.save_placements(sc, pl)
    return osup.load_sidecar(clip)


def show_frame(m, outdir, t):
    before = set(os.listdir(outdir))
    m.seek(t, "absolute", "exact")
    for _ in range(60):
        time.sleep(0.1)
        new = set(os.listdir(outdir)) - before
        if new:
            time.sleep(0.2)
            return Image.open(os.path.join(outdir, sorted(os.listdir(outdir), key=lambda f: os.path.getmtime(os.path.join(outdir, f)))[-1])).convert("RGB")
    return None


def is_overlay_pixel(px):
    return px[0] > 30 and px[2] < 200       # blue video with a translucent red piece mixed in


def run(rotation):
    sc = make_sidecar(rotation)
    files, graph = osup.preview_graph(sc, 320, 180, True)
    out = tempfile.mkdtemp(dir=work)
    errors = []
    m = mpv.MPV(vo="image", vo_image_format="png", vo_image_outdir=out, ao="null", loglevel="info", pause=True,
                log_handler=lambda lvl, comp, text: errors.append(text.strip()) if lvl in ("error", "fatal") else None)
    m["external-files"] = files
    m["lavfi-complex"] = graph
    m.play(str(clip))
    time.sleep(1.0)
    on = show_frame(m, out, 1.0)
    m["lavfi-complex"] = osup.preview_graph(sc, 320, 180, False)[1]
    off = show_frame(m, out, 2.0)
    m["lavfi-complex"] = graph
    on2 = show_frame(m, out, 3.0)
    tracks = [(t["type"], t.get("external")) for t in m.track_list]
    aud = m._get_property("audio-params")
    m.terminate()
    return on, off, on2, tracks, aud, errors


on, off, on2, tracks, aud, errors = run(0.0)
check(on is not None and off is not None and on2 is not None, "mpv rendered frames with the overlay graph")
check(sum(t[0] == "video" and t[1] for t in tracks) == 2, "both pieces were loaded as external video tracks")
check(not errors, f"no mpv errors with the graph ({errors[:2]})")
if on is not None:
    check(is_overlay_pixel(on.getpixel((100, 60))), f"overlay ON: keyboard visible at its placement {on.getpixel((100, 60))}")
    check(not is_overlay_pixel(on.getpixel((300, 170))), "overlay ON: the rest of the frame is the plain video")
    check(is_overlay_pixel(on.getpixel((270, 100))), f"overlay ON: mouse visible at its placement {on.getpixel((270, 100))}")
if off is not None:
    check(not is_overlay_pixel(off.getpixel((100, 60))) and not is_overlay_pixel(off.getpixel((270, 100))),
          "toggled OFF at runtime: clean video, no overlay")
if on2 is not None:
    check(is_overlay_pixel(on2.getpixel((100, 60))), "toggled back ON at runtime: overlay returns")
check(bool(aud), f"audio still plays with the overlay graph active ({aud and aud.get('format')})")

# rotation: a 90 degree turn of a wide keyboard piece moves its pixels from a wide strip to a tall one
_, _, flat, *_ = run(0.0)
_, _, rot, *_ = run(90.0)


def extent(im):
    xs = [x for x in range(im.width) for y in range(im.height) if is_overlay_pixel(im.getpixel((x, y))) and x < 230]
    ys = [y for x in range(im.width) for y in range(im.height) if is_overlay_pixel(im.getpixel((x, y))) and x < 230]
    return (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1) if xs else (0, 0)


fw, fh = extent(flat)
rw, rh = extent(rot)
check(fw > fh * 2 and rh > rw * 2, f"rotation=90 turns the wide piece tall in mpv ({fw}x{fh} -> {rw}x{rh})")

# toggling the overlay must not change how the VIDEO looks (it used to: format=auto blended in RGB,
# a slight brightness/saturation shift). Colorful source, the overlay's corner pieces only; compare
# the video area away from them against no graph at all -- 8-bit BT.709 limited, 10-bit, full range.
def frame_with(clip_path, graph, files):
    out = tempfile.mkdtemp(dir=work)
    m = mpv.MPV(vo="image", vo_image_format="png", vo_image_outdir=out, ao="null", pause=True)
    if graph:
        m["external-files"] = files
        m["lavfi-complex"] = graph
    m.play(str(clip_path))
    time.sleep(1.0)
    im = show_frame(m, out, 1.0)
    m.terminate()
    return im


for label, args in (("8-bit BT.709 limited", ["-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_range", "tv"]),
                    ("10-bit", ["-pix_fmt", "yuv420p10le", "-colorspace", "bt709", "-color_range", "tv"]),
                    ("full range", ["-pix_fmt", "yuvj420p", "-colorspace", "bt709", "-color_range", "pc"])):
    src = work / f"color_{label.split()[0]}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=4",
                    "-c:v", "libx264", *args, str(src)], check=True)
    ts = time.time()
    pl = osup.resolve_placements({"keyboard": {"x": 0.0, "y": 0.0, "w": 0.2}, "mouse": {"x": 0.9, "y": 0.0, "w": 0.05}})
    j = aio.start_clip(ts, aio.OverlaySettings(pieces=["keyboard", "mouse"], fps=30, placements=pl))
    aio.finish_clip(j, src, clip_end=ts)
    j.cleanup()
    sc = osup.load_sidecar(src)
    osup.save_placements(sc, pl)
    sc = osup.load_sidecar(src)
    color = osup.video_color(src)
    files, g_on = osup.preview_graph(sc, 640, 360, True, color)
    g_off = osup.preview_graph(sc, 640, 360, False, color)[1]
    base, f_on, f_off = frame_with(src, None, []), frame_with(src, g_on, files), frame_with(src, g_off, files)
    pts = [(x, y) for x in range(20, 620, 23) for y in range(120, 350, 19)]       # below the corner pieces

    def diff(a, b):
        return sum(sum(abs(p - q) for p, q in zip(a.getpixel(pt), b.getpixel(pt))) for pt in pts) / len(pts)
    d_on, d_off = diff(base, f_on), diff(base, f_off)
    check(d_on < 0.5 and d_off < 0.5, f"{label}: the video looks identical with the overlay on, off, or no graph "
                                      f"(mean |diff| on {d_on:.2f}, off {d_off:.2f})")
    check(is_overlay_pixel(f_on.getpixel((40, 15))), f"{label}: the keyboard piece still draws ({f_on.getpixel((40, 15))})")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
