"""
Speed work: smart cutting and smart rendering (afterglow/smartcut.py,
afterglow/nle/smart_export.py).

* the capture / Save Trim frame-exact trim re-encodes only the partial GOPs
  at its ends and copies the rest bit for bit, with the audio copied and
  exactly in sync; anything it can't handle falls back to a full re-encode;
* the Editor's export copies untouched source GOPs, reuses chunks that
  didn't change since the previous export of the same file (picture AND
  audio), renders only the rest, and keeps colours identical between
  rendered and copied frames.

    QT_QPA_PLATFORM=offscreen python3 tests/test_smartcut.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="smartcut_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import av
import numpy as np
from PySide6.QtWidgets import QApplication

app = QApplication([])
from afterglow import clips, db, editor, smartcut
from afterglow.nle import ops, render as R, save, smart_export
from afterglow.nle.model import KIND_TEXT, Part, Segment, TextStyle

FAILS = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)


W, H, FPS = 640, 360, 60
work = Path(HOME) / "work"
work.mkdir()


def make_src(path, seconds, x264="keyint=60:bframes=3:b-pyramid=normal", extra=(), audio=True):
    """testsrc2 (every frame different) + an audio click train (sync check)."""
    a = ["-f", "lavfi", "-i", f"aevalsrc='if(lt(mod(t\\,0.37)\\,0.004)\\,0.9\\,0.05*sin(2*PI*220*t))':s=48000:d={seconds}"] \
        if audio else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d={seconds}", *a,
                    "-c:v", "libx264", "-preset", "ultrafast", "-x264-params", x264, *extra,
                    *(["-c:a", "aac", "-b:a", "128k"] if audio else []), str(path)], check=True)


def frames(path, t0=None, n=None):
    with av.open(str(path)) as c:
        vs = c.streams.video[0]
        if t0 is not None:
            c.seek(max(0, int((t0 - 2) / vs.time_base)), stream=vs, backward=True)
        k = 0
        for f in c.decode(vs):
            t = float(f.pts * vs.time_base)
            if t0 is not None and t < t0 - 0.004:
                continue
            yield t, f.to_ndarray(format="yuv420p")
            k += 1
            if n is not None and k >= n:
                return


def compare(out, src, src_t0):
    """(frames, bit-exact frames, worst PSNR of the rest)."""
    n = exact = 0
    worst = 99.0
    for (_to, a), (_ts, b) in zip(frames(out), frames(src, src_t0)):
        n += 1
        if np.array_equal(a, b):
            exact += 1
        else:
            mse = float(np.mean((a.astype(np.float32) - b) ** 2))
            worst = min(worst, 10 * np.log10(255 ** 2 / max(mse, 1e-9)))
    return n, exact, worst


def audio_full(path):
    """(time of the first decoded sample, mono samples) -- edit lists applied by the demuxer."""
    with av.open(str(path)) as c:
        st = c.streams.audio[0]
        out, first = [], None
        for f in c.decode(st):
            if first is None and f.pts is not None:
                first = float(f.pts * f.time_base)
            out.append(f.to_ndarray()[0])
    return first or 0.0, np.concatenate(out)


def click_lag(out, src, src_t0):
    """Audio offset (samples) of the output against the source at src_t0
    (cross-correlation of the click train; 0 = in sync)."""
    fo, a = audio_full(out)
    fs, b = audio_full(src)
    off = int(round((src_t0 - fs) * 48000))
    end = min(40000, len(a) - 400)
    x = a[400:end]
    best = max(range(-400, 401), key=lambda L: float(np.dot(x, b[off + 400 + L:off + end + L]))
               if off + 400 + L >= 0 and len(b[off + 400 + L:off + end + L]) == len(x) else -1e18)
    return best


def first_frame_at(path, t):
    """Time of the first video frame shown at or after t (the cut lines up audio with it)."""
    for ft, _f in frames(path, t, 1):
        return ft
    return t


def decodes_clean(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, text=True)
    return not r.stderr.strip()


def dur(path):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                          "csv=p=0", str(path)]))


# =========================================================================
# smart trim
# =========================================================================
src = work / "raw.mp4"
make_src(src, 24)
info = smartcut.video_info(src)
check(smartcut.eligible(info) is None and info.avcc.nal_len == 4, "an x264 MP4 is smart-cuttable")

for label, s0, s1 in (("to the end", 9.37, 24.0), ("middle", 5.13, 13.52), ("inside one GOP", 3.1, 3.6)):
    out = work / f"trim_{label.replace(' ', '_')}.mp4"
    stats = smartcut.smart_trim(src, out, s0, s1)
    n, exact, worst = compare(out, src, s0)
    want = int(round((min(s1, 24.0) - s0) * FPS))
    check(abs(n - want) <= 1, f"smart trim ({label}): {n} frames (want {want})")
    if label != "inside one GOP":
        check(stats["copied"] > 0 and stats["encoded"] <= 2 * 60 and exact == stats["copied"],
              f"…only the partial GOPs are re-encoded ({stats}); every copied frame is bit-exact ({exact})")
    else:
        check(stats["copied"] == 0 and stats["encoded"] == n, f"…a range inside one GOP is simply re-encoded ({stats})")
    check(worst > 38, f"…re-encoded frames look the same (worst PSNR {worst:.1f} dB)")
    lag = click_lag(out, src, first_frame_at(src, s0))
    check(lag is not None and abs(lag) <= 48, f"…audio copied in sync with the first frame (offset {lag} samples)")
    check(decodes_clean(out) and abs(dur(out) - (min(s1, 24.0) - s0)) < 0.05, "…decodes cleanly at the right length")

# MKV in and out (OBS's default container), full range colour kept
mkv = work / "raw.mkv"
make_src(mkv, 16, extra=["-color_range", "pc"])
out = work / "trim.mkv"
stats = smartcut.smart_trim(mkv, out, 4.42, 16.0)
n, exact, worst = compare(out, mkv, 4.42)
check(stats["copied"] > 0 and exact == stats["copied"] and worst > 38 and decodes_clean(out),
      f"MKV -> MKV smart trim ({stats}, PSNR {worst:.1f})")
check(smartcut.video_info(out).color["color_range"] == 2, "…full-range video stays full range")

# open GOPs can't be cut into: smart_trim re-encodes it all (still exact)
opg = work / "open.mp4"
make_src(opg, 10, x264="keyint=60:open-gop=1:bframes=3")
out = work / "trim_open.mp4"
stats = smartcut.smart_trim(opg, out, 2.3, 8.0)
n, exact, worst = compare(out, opg, 2.3)
check(stats["copied"] == 0 and n == stats["encoded"] and worst > 38, f"open-GOP source: fully re-encoded instead ({stats})")

# not H.264: commit_trim falls back to its full re-encode
vp = work / "vp9.mkv"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d=6", "-c:v", "libvpx-vp9",
                "-deadline", "realtime", "-cpu-used", "8", str(vp)], check=True)
check(smartcut.eligible(smartcut.video_info(vp)) is not None, "VP9 isn't smart-cut")
cp = work / "vp9_trim.mkv"
shutil.copy(vp, cp)
editor.commit_trim(editor.TrimRequest(video_path=cp, start_sec=1.5, end_sec=4.5, frame_perfect=True), False, None,
                   skip_backup=True)
check(abs(dur(cp) - 3.0) < 0.1 and decodes_clean(cp), "…commit_trim still trims it exactly (full re-encode)")

# commit_trim (the Save Trim / capture path) uses the smart cut
ct = work / "commit.mp4"
shutil.copy(src, ct)
t = time.time()
editor.commit_trim(editor.TrimRequest(video_path=ct, start_sec=6.21, end_sec=20.0, frame_perfect=True), False, None,
                   skip_backup=True)
n, exact, worst = compare(ct, src, 6.21)
check(exact > n * 0.75 and worst > 38, f"commit_trim frame-perfect is a smart cut ({exact}/{n} frames copied bit-exact)")
out2 = work / "direct_out.mp4"
editor.commit_trim(editor.TrimRequest(video_path=src, start_sec=1.0, end_sec=3.0, frame_perfect=True), False, None,
                   skip_backup=True, output_path=out2)
check(out2.exists() and src.exists() and abs(dur(out2) - 2.0) < 0.05, "commit_trim can write straight to another path")
check(editor._find_keyframe_at_or_before(src, 5.5) == 5.0 and editor._find_keyframe_at_or_before(src, 0.2) == 0.0,
      "keyframe lookup from the index")

# =========================================================================
# clip capture: OBS file -> trimmed clip in the library
# =========================================================================
db.init_db()


class FakeOBS:
    def __init__(self, s):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_fps(self):
        return 60.0

    def save_replay_buffer(self, on_sent=None):
        raw = work / f"obs_{time.time_ns()}.mp4"
        shutil.copy(src, raw)
        FakeOBS.raw = raw
        return raw


clips.OBSClient = FakeOBS
clips.play_sound = lambda p: None
cfg = clips.create_clip_config(name="Speed", length_seconds=10, hotkey="ctrl+f7")
t = time.time()
video = clips.trigger_clip(cfg.id)
took = time.time() - t
n, exact, worst = compare(video.path, src, 14.0)
check(abs(video.duration_sec - 10.0) < 0.05 and exact > n * 0.75,
      f"capture: the clip is a smart cut of the replay buffer ({exact}/{n} copied, {took:.2f}s)")
check(not FakeOBS.raw.exists() and not list(Path(video.path).parent.glob("*.trim_tmp*")),
      "capture: raw file removed, no temp files left in the clips folder")

# =========================================================================
# smart export
# =========================================================================
clip = work / "clip.mp4"
make_src(clip, 12, x264="keyint=60:bframes=3")
p = save.new_project_for_file(str(clip))
seg = next(iter(p.all_segments()))
out = str(work / "export.mp4")


def export(label):
    t0 = time.time()
    R.export(p, out)
    st = dict(R.LAST_EXPORT_STATS)
    print(f"   {label}: {time.time() - t0:.2f}s {st}")
    return st


st = export("plain clip")
n, exact, worst = compare(out, clip, 0.0)
check(st.get("mode") == "smart" and st["copied"] >= 11 * FPS and st.get("audio") == "copied",
      "export of an untouched clip copies its video GOPs and its audio")
check(exact >= 11 * FPS and decodes_clean(out), f"…copied frames are bit-exact ({exact}/{n})")
lag = click_lag(out, clip, 0.0)
check(lag is not None and abs(lag) <= 48, f"…audio in sync ({lag})")

st = export("again, unchanged")
check(st["rendered"] == 0 and st.get("audio") == "reused", "re-export with no changes renders nothing")

text = Segment(parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=1.5, has_video=True, has_audio=False,
                           text=TextStyle(text="HELLO", font_size=0.12, color="#ff0000"))])
ops.place(p, text, 0, 4.0, prefer=-1)
st = export("caption added")
check(0 < st["rendered"] <= 4 * FPS and st.get("audio") == "reused",
      "adding a 1.5 s caption renders only the GOPs around it; the audio is reused")
cap_frame = next(f for k, (_t, f) in enumerate(frames(out)) if k == int(4.7 * FPS))
plain_frame = next(f for k, (_t, f) in enumerate(frames(clip)) if k == int(4.7 * FPS))
check(not np.array_equal(cap_frame, plain_frame), "…and the caption is in the picture")
n, exact, worst = compare(out, clip, 0.0)
check(exact >= n - st["rendered"], f"…every frame outside the re-rendered GOPs is still bit-exact ({exact}/{n})")

text.parts[0].text.text = "HELLO!!"
st = export("caption edited")
check(st["rendered"] <= 2 * FPS and st["reused"] >= st["rendered"] * 0 and st["reused"] > 0,
      "editing the caption re-renders only its own chunk; its neighbours are reused from the last export")

# rendered frames have the same colours as copied ones (BT.709 conversion, not swscale's BT.601)
inv = Segment(parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=1.0, has_video=True, has_audio=False,
                          text=TextStyle(text=".", font_size=0.01, color="#00000000"))])
ops.place(p, inv, 0, 8.0, prefer=-1)
st = export("invisible element (forces re-rendering 8-9 s)")
k = int(8.5 * FPS)
a = next(f for i, (_t, f) in enumerate(frames(out)) if i == k)
b = next(f for i, (_t, f) in enumerate(frames(clip)) if i == k)
mse = float(np.mean((a.astype(np.float32) - b) ** 2))
psnr = 10 * np.log10(255 ** 2 / max(mse, 1e-9))
check(psnr > 34, f"a re-rendered frame matches the source's colours (PSNR {psnr:.1f} dB)")

# a volume change: the picture is reused, the audio re-rendered
seg.volume = 0.5
st = export("volume changed")
check(st["rendered"] == 0 and st.get("audio") == "rendered", "a volume change re-renders only the audio")

# the file changed some other way: nothing is reused from it
Path(out).write_bytes(Path(out).read_bytes())
os.utime(out, ns=(time.time_ns(), time.time_ns() + 10_000_000))
st = export("file touched from outside")
check(st["reused"] == 0, "a file changed outside the Editor is never reused from")

# smart=False keeps the plain full render available
R.export(p, out, smart=False)
check(R.LAST_EXPORT_STATS.get("mode") == "full" and decodes_clean(out), "smart=False: the full render still works")

# speed: an export of an untouched clip is much faster than a full render
t0 = time.time()
R.export(p, str(work / "full.mp4"), smart=False)
t_full = time.time() - t0
seg.volume = 1.0
for s_ in list(p.all_segments()):
    if s_.parts[0].kind == KIND_TEXT:
        ops.delete(p, [s_.id])
t0 = time.time()
R.export(p, str(work / "fast.mp4"))
t_smart = time.time() - t0
check(t_smart < t_full * 0.5, f"untouched clip: smart export {t_smart:.2f}s vs full render {t_full:.2f}s")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
