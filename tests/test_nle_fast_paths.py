"""
Export / capture speed-ups that must not change the picture:

  * nle/yuv_blend.py: an overlay blended straight into a YUV frame
  * Renderer.overlay_base / overlay_frame: "things drawn over an untouched
    clip" rendered without the RGB round trip, and only then
  * a whole export with a caption: right frames, right count, decodes clean
  * media.frame_to_qimage (single copy), smartcut.prefetched (decode ahead),
    editor.probe_duration (in-process) and the capture's faster head encode

    QT_QPA_PLATFORM=offscreen python3 tests/test_nle_fast_paths.py
"""
import os, sys, tempfile, subprocess, math
from pathlib import Path
HOME = tempfile.mkdtemp(prefix="fast_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import av
from PySide6.QtWidgets import QApplication
app = QApplication([])
from afterglow.gui import fonts
fonts.load_bundled_fonts()
from afterglow import smartcut, editor
from afterglow.nle import media, ops, render, yuv_blend
from afterglow.nle.model import KIND_TEXT, Part, Segment, TextStyle, default_project

fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


W, H, FPS = 640, 360, 30
SRC = os.path.join(HOME, "src.mp4")
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}",
                "-f", "lavfi", "-i", "sine=f=440", "-t", "4", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-g", "30", "-bf", "0", "-colorspace", "bt709", "-color_primaries", "bt709",
                "-color_trc", "bt709", "-color_range", "tv", "-c:a", "aac", SRC], check=True)


def psnr(a, b):
    d = a.astype(np.float64) - b.astype(np.float64)
    mse = float((d * d).mean())
    return 99.0 if mse == 0 else 10 * math.log10(255 * 255 / mse)


def first_frame(path, t=0.0):
    with av.open(path) as c:
        vs = c.streams.video[0]
        if t > 0:
            c.seek(int(t / vs.time_base), stream=vs)
        for f in c.decode(vs):
            if float(f.pts * vs.time_base) >= t - 1e-3:
                return f
    return None


# ---------------------------------------------------------------- yuv_blend
base = first_frame(SRC, 1.0)
base_arr = base.to_ndarray()
empty = np.zeros((H, W, 4), np.uint8)
check(yuv_blend.bounding_box(empty) is None and yuv_blend.blend_onto_frame(base, empty) is base,
      "an empty overlay returns the source frame itself")
ov = empty.copy()
ov[101:151, 201:333] = (0, 0, 255, 255)          # opaque red, odd edges
box = yuv_blend.bounding_box(ov)
check(box == (200, 100, 334, 152), f"bounding box is grown to even coordinates {box}")
for matrix, cs in (("ITU709", "ITU709"), ("ITU601", "ITU601")):
    out = yuv_blend.blend_onto_frame(base, ov, matrix)
    arr = out.to_ndarray()
    ref = av.VideoFrame.from_ndarray(np.ascontiguousarray(ov[110:140, 210:320]), format="bgra") \
        .reformat(format="yuv420p", dst_colorspace=cs).to_ndarray()
    y, u, v = int(arr[120, 250]), int(arr[H:H + H // 4].reshape(H // 2, W // 2)[60, 125]), \
        int(arr[H + H // 4:].reshape(H // 2, W // 2)[60, 125])
    ry, ru, rv = int(ref[5, 5]), int(ref[30:30 + 7].reshape(15, 55)[5, 5]) if False else None, None
    ry = int(ref[5, 5])
    ru = int(ref[30:].reshape(-1)[: (15 * 55)].reshape(15, 55)[5, 5])
    rv = int(ref[30:].reshape(-1)[(15 * 55):].reshape(15, 55)[5, 5])
    check(abs(y - ry) <= 1 and abs(u - ru) <= 1 and abs(v - rv) <= 1,
          f"opaque red in {matrix} matches swscale's conversion (Y {y}/{ry} U {u}/{ru} V {v}/{rv})")
mask = np.ones((H, W), bool)
mask[100:152, 200:334] = False
check(np.array_equal(arr[:H][mask], base_arr[:H][mask]), "pixels outside the box are the source's own, bit for bit")
check(np.array_equal(base.to_ndarray(), base_arr), "the source frame itself is not modified")
half = empty.copy()
half[100:150, 200:300] = (0, 0, 0, 128)          # 50% black (premultiplied)
out = yuv_blend.blend_onto_frame(base, half, "ITU709").to_ndarray()
exp = np.round(base_arr[110:140, 210:290].astype(np.float32) * (1 - 128 / 255) + 16 * 128 / 255)
check(np.abs(out[110:140, 210:290].astype(np.float32) - exp).max() <= 1, "50% black darkens luma toward 16 (limited range)")
try:
    yuv_blend.blend_onto_frame(base.reformat(format="rgb24"), ov)
    raised = False
except ValueError:
    raised = True
check(raised, "a format it doesn't handle raises ValueError (the caller renders the long way)")

# ---------------------------------------------------------------- overlay_base
def caption(start, length):
    return Segment(start=start, parts=[Part(kind=KIND_TEXT, src_in=0, src_out=length, has_audio=False,
                                            text=TextStyle(text="NICE", font_size=0.2, color="#ff3030"))])


p = default_project(W, H, float(FPS))
clip = ops.add_media(p, media.probe(SRC), start=0, track_index=1)
ops.place(p, caption(1.0, 1.5), 0, 1.0)
r = render.Renderer(p, overlays=False)
check(r.overlay_base(0.5, W, H) is not None and r.passthrough_part(0.5, W, H) is not None,
      "no caption yet: plain passthrough (the base check agrees)")
found = r.overlay_base(1.5, W, H)
check(found is not None and found[0].id == clip.id and r.passthrough_part(1.5, W, H) is None,
      "caption over the untouched clip: the clip is the base, everything else is drawn on top")
check(r.overlay_base(1.5, W // 2, H // 2) is None, "a scaled output is rendered the long way")
color = render._out_color(p)
matrix = smartcut.SWS_COLORSPACE.get(int(color.get("colorspace", 2) or 2)) or "ITU601"
fast = r.overlay_frame(1.5, W, H, matrix)
slow_img = r.frame(1.5, W, H)
slow = smartcut.to_output_yuv(av.VideoFrame.from_ndarray(
    np.ascontiguousarray(np.frombuffer(slow_img.constBits(), np.uint8).reshape(H, W, 4)), format="bgra"), color)
fa, sa = fast.to_ndarray(), slow.to_ndarray()
box = r._ov_dirty
check(fast is not None and box is not None and (box[2] - box[0]) < W and (box[3] - box[1]) < H, f"the caption's box {box}")
x0, y0, x1, y1 = box
check(psnr(fa[y0:y1, x0:x1], sa[y0:y1, x0:x1]) > 38, f"inside the box it matches the RGB path (PSNR {psnr(fa[y0:y1, x0:x1], sa[y0:y1, x0:x1]):.1f} dB)")
src_now = r._source(SRC).frame_at(1.5).to_ndarray()
m = np.ones((H, W), bool)
m[y0:y1, x0:x1] = False
check(np.array_equal(fa[:H][m], src_now[:H][m]), "outside it the frame is the source's own (the RGB path alters every pixel a little)")
f2 = r.overlay_frame(1.6, W, H, matrix)
check(r._ov_dirty is not None and not np.any(r._ov_buf[:max(0, r._ov_dirty[1] - 2)]), "the reused canvas is cleared between frames")
check(r.overlay_frame(3.0, W, H, matrix) is not None and r._ov_dirty is None, "after the caption: nothing drawn, canvas clean")

p2 = default_project(W, H, float(FPS))
c2 = ops.add_media(p2, media.probe(SRC), start=0, track_index=1)
ops.place(p2, caption(0.0, 4.0), 0, 0.0)
ops.set_zoom(p2, [c2.id], 1.3, 0.3, 0.3)
r2 = render.Renderer(p2, overlays=False)
check(r2.overlay_base(2.0, W, H) is None and r2.overlay_frame(2.0, W, H) is None, "a zoomed clip underneath: not a base (rendered the long way)")
p3 = default_project(W, H, float(FPS))
c3 = ops.add_media(p3, media.probe(SRC), start=0, track_index=1)
ops.set_fades(p3, [c3.id], fade_out=1.0)
r3 = render.Renderer(p3, overlays=False)
check(r3.overlay_base(1.0, W, H) is not None and r3.overlay_base(3.6, W, H) is None, "a fading clip is only a base outside its fade")
for x in (r, r2, r3):
    x.close()

# ---------------------------------------------------------------- a whole export
out_path = os.path.join(HOME, "out.mp4")
render.export(p, out_path)
st = dict(render.LAST_EXPORT_STATS)
check(st.get("mode") == "smart" and st["copied"] > 0 and st["rendered"] > 0 and st["copied"] + st["rendered"] == 4 * FPS,
      f"smart export: caption frames rendered, the rest copied {st}")
with av.open(out_path) as c:
    frames = [f.to_ndarray() for f in c.decode(video=0)]
check(len(frames) == 4 * FPS, f"every frame is there ({len(frames)})")
with av.open(SRC) as c:
    src_frames = [f.to_ndarray() for f in c.decode(video=0)]
i_plain, i_cap = int(3.4 * FPS), int(1.5 * FPS)      # (the last second is its own copied GOP)
check(np.array_equal(frames[i_plain], src_frames[i_plain]), "a frame without the caption is the source frame exactly (copied)")
d = np.abs(frames[i_cap][:H].astype(int) - src_frames[i_cap][:H].astype(int))
rr = render.Renderer(p, overlays=False)
rr.overlay_frame(i_cap / FPS + 1e-5, W, H, matrix)
x0, y0, x1, y1 = rr._ov_dirty
rr.close()
inside = d[y0:y1, x0:x1].mean()
outside = (d.sum() - d[y0:y1, x0:x1].sum()) / (d.size - (y1 - y0) * (x1 - x0))
check(inside > 5 and outside < 1.5, f"a caption frame differs from the source where the caption is ({inside:.1f}) and barely elsewhere ({outside:.2f})")
r_ok = subprocess.run(["ffmpeg", "-v", "error", "-i", out_path, "-f", "null", "-"], capture_output=True, text=True)
check(r_ok.returncode == 0 and not r_ok.stderr.strip(), "the export decodes cleanly end to end")

# ---------------------------------------------------------------- smaller pieces
f0 = first_frame(SRC, 0.5)
img = media.frame_to_qimage(f0, W, H)
ref = f0.reformat(format="bgra").to_ndarray()
got = np.frombuffer(img.constBits(), np.uint8).reshape(H, img.bytesPerLine())[:, :W * 4].reshape(H, W, 4)
check(np.array_equal(ref, got), "frame_to_qimage (one copy) gives the same pixels as before")
img_small = media.frame_to_qimage(f0, 322, 182)
check(img_small.width() == 322 and img_small.height() == 182 and not img_small.isNull(), "...also at a size whose row isn't a multiple of 8 bytes")
del f0
import gc
gc.collect()
check(img.pixelColor(10, 10).isValid() and np.array_equal(
    ref, np.frombuffer(img.constBits(), np.uint8).reshape(H, img.bytesPerLine())[:, :W * 4].reshape(H, W, 4)),
    "the QImage owns its pixels (still right after the frame is freed)")

check(list(smartcut.prefetched(iter(range(50)), ahead=4)) == list(range(50)), "prefetched keeps order and count")


def boom():
    yield 1
    yield 2
    raise RuntimeError("decode failed")


got = []
try:
    for x in smartcut.prefetched(boom()):
        got.append(x)
    raised = False
except RuntimeError as e:
    raised = str(e) == "decode failed"
check(got == [1, 2] and raised, "prefetched passes the producer's exception on, after the items before it")
closed = []


def endless():
    try:
        i = 0
        while True:
            yield i
            i += 1
    finally:
        closed.append(True)


g = smartcut.prefetched(endless(), ahead=3)
next(g), next(g)
g.close()
import time
end = time.time() + 3
while not closed and time.time() < end:
    time.sleep(0.02)
check(closed == [True], "dropping it early stops the helper thread and closes the producer")

ff = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", SRC],
                          capture_output=True, text=True).stdout.strip())
check(abs(editor.probe_duration(Path(SRC)) - ff) < 1e-3, f"probe_duration in-process equals ffprobe's ({ff})")
try:
    editor.probe_duration(Path(HOME) / "nope.mp4")
    raised = False
except Exception:  # noqa: BLE001
    raised = True
check(raised, "a missing file still raises (falls through to ffprobe's error)")

# the capture's head encode: ultrafast only for the smart cut, same exact result
out_a, out_b = Path(HOME) / "a.mp4", Path(HOME) / "b.mp4"
from afterglow.editor import TrimRequest, commit_trim
import shutil
for out, kw in ((out_a, {}), (out_b, {"smart_preset": "ultrafast", "smart_crf": 17})):
    raw = Path(HOME) / f"raw_{out.stem}.mp4"
    shutil.copy(SRC, raw)
    commit_trim(TrimRequest(video_path=raw, start_sec=1.37, end_sec=4.0, frame_perfect=True, preset="veryfast", **kw),
                has_prior_edit=False, existing_backup=None, skip_backup=True, output_path=out, known_duration=4.0)
da, db_ = editor.probe_duration(out_a), editor.probe_duration(out_b)
check(abs(da - db_) < 1e-3 and abs(da - 2.63) < 0.05, f"the faster head preset gives the same length ({da:.3f} / {db_:.3f})")
with av.open(str(out_a)) as ca, av.open(str(out_b)) as cb:
    fa_ = [f.to_ndarray() for f in ca.decode(video=0)]
    fb_ = [f.to_ndarray() for f in cb.decode(video=0)]
check(len(fa_) == len(fb_) and psnr(fa_[0], fb_[0]) > 36 and np.array_equal(fa_[-1], fb_[-1]),
      f"same frame count, the re-encoded head looks the same (PSNR {psnr(fa_[0], fb_[0]):.1f} dB), the copied part is identical")

print("\n%d failed" % len(fails) if fails else "\nall passed")
os._exit(1 if fails else 0)
