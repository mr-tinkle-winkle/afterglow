"""
Renderer/exporter tests against REAL media (generated with ffmpeg into
a temp dir): pixels and audio levels are checked at known times.

    QT_QPA_PLATFORM=offscreen python3 tests/test_nle_render.py

Test media:
- blocks.mp4: 6 s, 320x180 @ 30fps, one solid color per second
  (second 0 = red, 1 = green, 2 = blue, 3 = yellow, 4 = magenta,
  5 = cyan), with a 440 Hz tone at amplitude 0.5.
- tone.wav:   10 s, 440 Hz, amplitude 0.5.
- square.mp4: 4 s, 240x180 (4:3) solid white, no audio.
"""
import math
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import av
import numpy as np
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from afterglow.nle import media, ops
from afterglow.nle.model import KIND_TEXT, Part, Segment, TextStyle, default_project
from afterglow.nle.render import RATE, Renderer, export

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


TMP = tempfile.mkdtemp(prefix="nle_test_")
COLORS = ["red", "green", "blue", "yellow", "magenta", "cyan"]
RGB = {"lime": (0, 255, 0), "red": (255, 0, 0), "green": (0, 128, 0),  # ffmpeg's "green" is #008000
       "blue": (0, 0, 255), "yellow": (255, 255, 0),
       "magenta": (255, 0, 255), "cyan": (0, 255, 255), "white": (255, 255, 255), "black": (0, 0, 0)}


def ff(*args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args], check=True)


BLOCKS = os.path.join(TMP, "blocks.mp4")
TONE = os.path.join(TMP, "tone.wav")
SQUARE = os.path.join(TMP, "square.mp4")
inputs = []
for c in COLORS:
    inputs += ["-f", "lavfi", "-i", f"color=c={c}:s=320x180:r=30:d=1"]
# aevalsrc, not sine: ffmpeg's sine source defaults to 1/8 amplitude.
ff(*inputs, "-f", "lavfi", "-i", "aevalsrc=0.5*sin(2*PI*440*t):s=48000:d=6",
   "-filter_complex", "".join(f"[{i}:v]" for i in range(6)) + "concat=n=6:v=1:a=0[v]",
   "-map", "[v]", "-map", "6:a", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-g", "15",
   "-pix_fmt", "yuv444p", "-c:a", "aac", "-b:a", "256k", BLOCKS)
ff("-f", "lavfi", "-i", "aevalsrc=0.5*sin(2*PI*440*t):s=48000:d=10", TONE)   # mono, exact 0.5 amplitude
ff("-f", "lavfi", "-i", "color=c=white:s=240x180:r=30:d=4", "-c:v", "libx264", "-preset", "ultrafast",
   "-pix_fmt", "yuv444p", "-qp", "0", SQUARE)


def px(img, x, y):
    c = img.pixelColor(int(x), int(y))
    return (c.red(), c.green(), c.blue())


def near(rgb, name, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(rgb, RGB[name]))


def rms(a):
    return float(np.sqrt(np.mean(np.square(a)))) if a.size else 0.0


def zero_crossings_per_sec(a):
    ch = a[:, 0]
    return np.count_nonzero(np.diff(np.signbit(ch))) / 2 / (len(ch) / RATE)


# ---- probe ----------------------------------------------------------------
info = media.probe(BLOCKS)
check(abs(info["duration"] - 6) < 0.1 and info["has_video"] and info["has_audio"]
      and (info["width"], info["height"]) == (320, 180) and abs(info["fps"] - 30) < 0.01,
      f"probe: {info['duration']:.2f}s 320x180@30 with audio")
tinfo = media.probe(TONE)
check(not tinfo["has_video"] and tinfo["has_audio"] and abs(tinfo["duration"] - 10) < 0.05, "probe: audio-only file")


def project_with(*segments, w=320, h=180):
    p = default_project(w, h, 30)
    for track_i, seg in segments:
        ops.place(p, seg, track_i, seg.start)
    return p


def seg_of(path, start=0.0, src_in=0.0, src_out=None, **kw):
    i = media.probe(path)
    part = Part(source=path, src_in=src_in, src_out=i["duration"] if src_out is None else src_out,
                source_duration=i["duration"], has_video=i["has_video"], has_audio=i["has_audio"])
    return Segment(start=start, parts=[part], **kw)


# ---- source-time mapping ---------------------------------------------------
p = project_with((1, seg_of(BLOCKS)))
r = Renderer(p)
ok = all(near(px(r.frame(sec + 0.5), 160, 90), COLORS[sec]) for sec in range(6))
check(ok, "each second shows the right color block (frame-accurate decode)")
ok = near(px(r.frame(0.99), 160, 90), "red") and near(px(r.frame(1.01), 160, 90), "green")
check(ok, "exact boundary: 0.99s red, 1.01s green")
seq = [px(r.frame(5.5 - i * 0.4), 160, 90) for i in range(12)]   # backwards scrubbing
check(all(near(c, COLORS[int(5.5 - i * 0.4)]) for i, c in enumerate(seq)), "backward scrubbing (seeks) stays accurate")
check(near(px(r.frame(7.0), 160, 90), "black"), "after the last segment: black")
r.close()

p = project_with((1, seg_of(BLOCKS, start=1.0, src_in=3.0, src_out=5.0)))
r = Renderer(p)
check(near(px(r.frame(0.5), 160, 90), "black"), "gap before the segment is black")
check(near(px(r.frame(1.5), 160, 90), "yellow") and near(px(r.frame(2.5), 160, 90), "magenta"),
      "trimmed segment (src 3-5) placed at t=1 shows yellow then magenta")
r.close()

# ---- stacking / visibility ---------------------------------------------------
top = seg_of(BLOCKS, src_in=0, src_out=1)          # red
bottom = seg_of(BLOCKS, src_in=2, src_out=3)       # blue
p = project_with((1, top), (2, bottom))
r = Renderer(p)
check(near(px(r.frame(0.5), 160, 90), "red"), "upper track draws over lower track")
top.visible = False
check(near(px(r.frame(0.5), 160, 90), "blue"), "hidden (V) upper segment reveals the one below")
r.close()

# ---- speed ------------------------------------------------------------------
s = seg_of(BLOCKS)
p = project_with((1, s))
ops.set_speed(p, s.id, 2.0)
r = Renderer(p)
check(abs(p.duration - 3) < 1e-6 and near(px(r.frame(1.25), 160, 90), "blue"), "2x speed: t=1.25 shows source 2.5 (blue)")
r.close()

# ---- fades ------------------------------------------------------------------
s = seg_of(BLOCKS, fade_in=1.0, fade_out=1.0)
p = project_with((1, s))
r = Renderer(p)
mid = px(r.frame(0.5), 160, 90)
check(100 < mid[0] < 160 and mid[1] < 20, f"fade-in halfway = half-bright red {mid}")
check(near(px(r.frame(5.5), 160, 90), "black", tol=150) and px(r.frame(5.5), 160, 90)[1] < 160,
      "fade-out dims the last second")
r.close()

# ---- transform / crop / letterbox ---------------------------------------------
s = seg_of(BLOCKS)
s.transform.scale = 0.5
p = project_with((1, s))
r = Renderer(p)
img = r.frame(0.5)
check(near(px(img, 160, 90), "red") and near(px(img, 10, 10), "black"), "scale 0.5: centered, black around it")
s.transform.x = 0.25
img = r.frame(0.5)
check(near(px(img, 240, 90), "red") and near(px(img, 100, 90), "black"), "x offset moves the picture right")
s.transform.scale, s.transform.x, s.transform.rotation = 1.0, 0.0, 90
img = r.frame(0.5)
check(near(px(img, 10, 90), "black") and near(px(img, 160, 90), "red"), "rotation 90: tall picture, sides black")
r.close()

sq = seg_of(SQUARE)
p = project_with((1, sq))
r = Renderer(p)
img = r.frame(1.0)
check(near(px(img, 160, 90), "white") and near(px(img, 20, 90), "black") and near(px(img, 300, 90), "black"),
      "4:3 source in 16:9 canvas is letterboxed (black sides)")
sq.transform.crop_left, sq.transform.crop_right = 0.25, 0.25
img = r.frame(1.0)
check(near(px(img, 160, 90), "white") and near(px(img, 90, 90), "black") and near(px(img, 230, 90), "black")
      and near(px(img, 105, 90), "white") and near(px(img, 215, 90), "white"),
      "cropping 25% off each side: picture is exactly 120px wide (100-220)")
r.close()

# ---- zoom filter / keyframes ---------------------------------------------------
s = seg_of(SQUARE)
s.zoom_amount, s.zoom_in, s.zoom_out = 2.0, 1.0, 1.0
p = project_with((1, s))
r = Renderer(p)
check(near(px(r.frame(0.01), 20, 90), "black") and near(px(r.frame(2.0), 20, 90), "white")
      and near(px(r.frame(3.99), 20, 90), "black"), "zoom filter: zooms in, holds, zooms back out")
r.close()
s = seg_of(SQUARE)
p = project_with((1, s))
ops.set_keyframe(p, s.id, "opacity", 0.0, 0.0)
ops.set_keyframe(p, s.id, "opacity", 2.0, 1.0)
r = Renderer(p)
v1 = px(r.frame(1.0), 160, 90)[0]
check(110 < v1 < 145 and px(r.frame(3.0), 160, 90)[0] > 245, f"opacity keyframes interpolate (t=1 -> {v1})")
r.close()

# ---- text element --------------------------------------------------------------
txt = Segment(start=0, parts=[Part(kind=KIND_TEXT, src_in=0, src_out=3, has_audio=False,
                                    text=TextStyle(text="HELLO", font_size=0.4, color="#00ff00"))])
p = project_with((1, txt))
r = Renderer(p)
img = r.frame(1.0)
greens = sum(1 for x in range(0, 320, 4) for y in range(0, 180, 4) if near(px(img, x, y), "lime", 60))
check(greens > 20, f"text element renders ({greens} green samples)")
r.close()

# ---- audio -------------------------------------------------------------------------
a = seg_of(TONE)
p = project_with((2, a))
r = Renderer(p)
full = r.audio(1.0, RATE)
check(abs(rms(full) - 0.5 / math.sqrt(2)) < 0.02, f"tone at 100% has the right level (rms {rms(full):.3f})")
check(abs(zero_crossings_per_sec(full) - 440) < 5, f"tone pitch 440 Hz ({zero_crossings_per_sec(full):.0f})")
check(np.allclose(full[:, 0], full[:, 1]), "mono source plays at full level on BOTH channels")
a.volume = 0.5
check(abs(rms(r.audio(1.0, RATE)) - 0.25 / math.sqrt(2)) < 0.02, "volume 50% halves the level")
a.volume = 2.0
check(abs(rms(r.audio(1.0, RATE)) - 1.0 / math.sqrt(2)) < 0.03, "volume 200% doubles the level")
a.volume = 1.0
a.muted = True
check(rms(r.audio(1.0, RATE)) == 0.0, "muted (M) is silent")
a.muted = False
a.fade_in = 2.0
check(rms(r.audio(0.0, RATE // 10)) < 0.05 and rms(r.audio(3.0, RATE // 10)) > 0.3, "audio fade-in ramps up")
a.fade_in = 0.0
ops.set_speed(p, a.id, 2.0)
check(abs(zero_crossings_per_sec(r.audio(1.0, RATE)) - 880) < 10, "2x speed doubles pitch (tape-style, for now)")
check(rms(r.audio(6.0, RATE)) == 0.0, "silence after the sped-up segment ends (10s -> 5s)")
r.close()

# ---- inclusive merge --------------------------------------------------------------
p = default_project(320, 180, 30)
music = ops.add_media(p, media.probe(TONE), start=0)
clip = ops.add_media(p, media.probe(BLOCKS), start=0, track_index=1)
ops.toggle(p, [clip.id], "muted")
merged = ops.combine(p, [music.id, clip.id])
r = Renderer(p)
check(near(px(r.frame(2.5), 160, 90), "blue") and near(px(r.frame(8.0), 160, 90), "black"),
      "merged long audio + short video: video, then black")
check(abs(rms(r.audio(8.0, RATE // 2)) - 0.354) < 0.03, "... and the audio keeps playing after the video ends")
check(abs(rms(r.audio(2.0, RATE // 2)) - 0.354) < 0.03,
      "the muted video's audio stays silent inside the merge (only the music plays)")
r.close()

# ---- export ---------------------------------------------------------------------------
p = default_project(320, 180, 30)
s1 = ops.add_media(p, media.probe(BLOCKS), start=0, track_index=1)
ops.split_at(p, 2.0)
second = [s for s in p.all_segments() if s.id != s1.id][0]
ops.move_segments(p, [second.id], 1.0)       # leave a 1s black gap at 2-3
out = os.path.join(TMP, "out.mp4")
progress = []
t0 = time.perf_counter()
export(p, out, progress=progress.append)
elapsed = time.perf_counter() - t0
e = media.probe(out)
check(abs(e["duration"] - 7.0) < 0.1 and e["has_video"] and e["has_audio"], f"export: 7s with video+audio ({e['duration']:.2f}s)")
check(progress and abs(progress[-1] - 1.0) < 1e-9 and progress == sorted(progress), "progress goes 0 -> 1")
check(not os.path.exists(out + ".render_tmp.mp4"), "temp file cleaned up")
er = Renderer(default_project(320, 180, 30))
ep = project_with((1, seg_of(out)))
er = Renderer(ep)
check(near(px(er.frame(0.5), 160, 90), "red") and near(px(er.frame(2.5), 160, 90), "black")
      and near(px(er.frame(3.5), 160, 90), "blue"), "exported file: red, then the black gap, then the moved part (blue)")
er.close()
ea = media.load_audio(out)
check(abs(rms(ea[int(0.5 * RATE):int(1.5 * RATE)]) - 0.354) < 0.03 and rms(ea[int(2.2 * RATE):int(2.8 * RATE)]) < 0.01,
      "exported audio: tone, then silence in the gap")
print(f"     (export of 7s @ 320x180 took {elapsed:.2f}s)")

import threading
ev = threading.Event(); ev.set()
from afterglow.nle.render import ExportCancelled
cancelled_out = os.path.join(TMP, "cancelled.mp4")
try:
    export(p, cancelled_out, cancel=ev); ok = False
except ExportCancelled:
    ok = True
check(ok and not os.path.exists(cancelled_out) and not os.path.exists(cancelled_out + ".render_tmp.mp4"),
      "cancelled export leaves no files behind")


# ---- transitions ---------------------------------------------------------------------
from afterglow.nle.model import Transition
def pair(kind, target="both", direction="left"):
    a_ = seg_of(BLOCKS, start=0.0, src_in=0.0, src_out=2.0)          # red, green
    b_ = seg_of(BLOCKS, start=2.0, src_in=4.0, src_out=6.0)          # magenta, cyan
    b_.transition_in = Transition(kind=kind, duration=1.0, target=target, direction=direction)
    return project_with((1, a_), (1, b_))
p = pair("crossfade"); r = Renderer(p)
mid = px(r.frame(2.5), 160, 90)
check(abs(mid[0] - 128) < 40 and mid[1] < 40 and mid[2] > 200,
      f"crossfade midway: outgoing keeps playing (blue, from its handle) mixed 50/50 with magenta {mid}")
check(near(px(r.frame(1.5), 160, 90), "green") and near(px(r.frame(3.2), 160, 90), "cyan"),
      "crossfade: before = outgoing, after = incoming")
r.close()
p = pair("slide", "destination", "left"); r = Renderer(p)
f = r.frame(2.5)
check(near(px(f, 60, 90), "magenta") and near(px(f, 260, 90), "blue"),
      "slide destination from left: incoming covers the left half at 50%")
r.close()
p = pair("slide", "both", "right"); r = Renderer(p)
f = r.frame(2.5)
check(near(px(f, 260, 90), "magenta") and near(px(f, 60, 90), "blue"), "slide both (push) from right")
r.close()
p = pair("fade", "destination", "top"); r = Renderer(p)
f = r.frame(2.5)
top_, bot_ = px(f, 160, 5), px(f, 160, 175)
check(near(top_, "magenta") and near(bot_, "blue"), f"fade (wipe) destination from top: top revealed first {top_} {bot_}")
r.close()
p = pair("blur"); r = Renderer(p)
f0 = r.frame(1.99, 160, 90); f1 = r.frame(2.45, 160, 90)
check(f1 != f0, "blur transition renders a distinct blended frame")
r.close()
ta = seg_of(TONE, start=0.0, src_in=0.0, src_out=3.0)
tb = seg_of(TONE, start=3.0, src_in=3.0, src_out=6.0)
tb.transition_in = Transition(kind="crossfade", duration=1.0)
p = project_with((2, ta), (2, tb)); r = Renderer(p)
lv = [rms(r.audio(3.0 + i * 0.2, RATE // 20)) for i in range(5)]
check(all(abs(v - 0.354) < 0.03 for v in lv), f"audio crossfade between continuous tone halves keeps a steady level {['%.3f' % v for v in lv]}")
r.close()

# ---- GIF looping ---------------------------------------------------------------------
GIF = os.path.join(TMP, "loop.gif")
ff("-f", "lavfi", "-i", "color=c=red:s=64x64:r=10:d=0.5", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=10:d=0.5",
   "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", GIF)
gi = media.probe(GIF)
p = default_project(320, 180, 30)
g = ops.add_media(p, gi, start=0.0, track_index=1)
check(g.parts[0].kind == "gif" and not g.has_audio, "GIF added as a gif part without audio")
ops.trim_end(p, g.id, 3.0)
r = Renderer(p)
check(abs(g.end - 3.0) < 1e-6 and near(px(r.frame(2.2), 160, 90), "red") and near(px(r.frame(2.7), 160, 90), "blue"),
      "GIF stretched past its length loops (2.2s red, 2.7s blue)")
r.close()

# ---- export size / quality -----------------------------------------------------------
p = project_with((1, seg_of(BLOCKS, src_out=1.0)))
small = os.path.join(TMP, "small.mp4")
export(p, small, width=160, height=90, crf=28)
si = media.probe(small)
check((si["width"], si["height"]) == (160, 90), f"export at a chosen resolution ({si['width']}x{si['height']})")

# ---- performance (informational) ----------------------------------------------------
BIG = os.path.join(TMP, "big.mp4")
ff("-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=60:d=5", "-f", "lavfi", "-i", "sine=d=5",
   "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-c:a", "aac", BIG)
pb = default_project(1920, 1080, 60)
ops.add_media(pb, media.probe(BIG), start=0, track_index=1)
rb = Renderer(pb)
rb.frame(0.0, 960, 540)
t0 = time.perf_counter()
for i in range(120):
    rb.frame(i / 60, 960, 540)
prev_ms = (time.perf_counter() - t0) / 120 * 1000
t0 = time.perf_counter()
for i in range(60):
    rb.frame(2 + i / 60, 1920, 1080)
full_ms = (time.perf_counter() - t0) / 60 * 1000
rb.close()
print(f"     preview 960x540 sequential: {prev_ms:.1f} ms/frame; full 1080p: {full_ms:.1f} ms/frame")
check(prev_ms < 16.7, f"1080p60 source previews at 960x540 faster than real time ({prev_ms:.1f} ms/frame < 16.7)")

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
