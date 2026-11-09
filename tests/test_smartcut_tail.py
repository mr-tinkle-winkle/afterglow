"""Smart cut: the end of a trim is copied, not re-encoded, when the video has
no frame reordering (OBS records without B-frames); with B-frames it still
re-encodes the last partial GOP. Runs anywhere (no display needed):

    python3 tests/test_smartcut_tail.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import av
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from afterglow import smartcut  # noqa: E402

FAILS = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def make(path, bframes):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=10",
                    "-f", "lavfi", "-i", "sine=f=440:d=10", "-c:v", "libx264", "-preset", "ultrafast",
                    "-bf", str(bframes), "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def frames(path):
    with av.open(str(path)) as c:
        return [f.to_ndarray(format="gray").astype(np.int16) for f in c.decode(video=0)]


tmp = Path(tempfile.mkdtemp(prefix="smartcut_tail_"))
for bf, name in ((0, "no B-frames (like OBS)"), (2, "with B-frames")):
    src = tmp / f"src_bf{bf}.mkv"
    make(src, bf)
    out = tmp / f"out_bf{bf}.mp4"
    a, b = 1.5, 7.3                      # keyframes every 2 s: head 1.5->2.0, tail 6.0->7.3
    st = smartcut.smart_trim(src, out, a, b)
    n = round((b - a) * 30)
    src_f = frames(src)
    out_f = frames(out)
    i0 = round(a * 30)
    diffs = [float(np.abs(x - y).mean()) for x, y in zip(out_f, src_f[i0:i0 + len(out_f)])]
    check(len(out_f) == n, f"{name}: exactly {n} frames ({len(out_f)})")
    check(max(diffs) < 2.0, f"{name}: every frame matches the source (max mean diff {max(diffs):.2f})")
    if bf == 0:
        check(st["encoded"] == 15 and st["copied"] == n - 15,
              f"{name}: only the 15 head frames re-encoded, the tail copied ({st})")
        check(diffs[-1] == 0.0 and diffs[-40] == 0.0, f"{name}: the last frames are the source's own, bit for bit")
        check(smartcut.in_display_order(src, 6.0, 7.3), f"{name}: detected as in display order")
    else:
        check(st["encoded"] == 15 + round((b - 6.0) * 30),
              f"{name}: head and the last partial GOP re-encoded, as before ({st})")
        check(not smartcut.in_display_order(src, 6.0, 7.3), f"{name}: reordering detected")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)
