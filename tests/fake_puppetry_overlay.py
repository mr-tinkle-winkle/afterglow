#!/usr/bin/env python3
"""
A stand-in for Puppetry's `puppetry-overlay` CLI so the input-overlay code
can be tested without Puppetry (FORMAT.md describes the real one). It only
implements what afterglow calls: status, snapshot, render, align. Every
piece it renders is transparent qtrle, all-intra (-g 1), and TIME-CODED:
the red channel of a frame at piece-time t is 255*(t mod 16)/16, so a test can
read a pixel and know exactly which moment of the timeline a frame shows.

Env: FAKE_PUPPETRY_OFF=1 -> status reports the replay buffer disabled;
     FAKE_PUPPETRY_FAIL=<piece> -> rendering that piece fails;
     FAKE_PUPPETRY_DELAY=<s> -> every render takes that much longer;
     FAKE_PUPPETRY_LOG=<file> -> each call's arguments are appended (JSON lines).
"""
import json
import os
import subprocess
import sys
import time

SIZES = {"full": (640, 200), "keyboard": (540, 160), "mouse": (96, 160), "controller": (248, 160),
         "simple": (400, 40), "movement": (96, 96)}


def out(obj, code=0):
    print(json.dumps(obj))
    sys.exit(code)


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def main():
    a = sys.argv[1:]
    cmd = a[0]
    if os.environ.get("FAKE_PUPPETRY_LOG"):
        with open(os.environ["FAKE_PUPPETRY_LOG"], "a") as f:
            f.write(json.dumps(a) + "\n")
    if cmd == "status":
        out({"pid": 1, "updated": time.time(), "daemon_connected": True,
             "replay": {"enabled": not os.environ.get("FAKE_PUPPETRY_OFF"), "length_s": 60.0,
                        "length_source": "obs", "extra_s": 5.0}})
    if cmd == "snapshot":
        open(a[1], "w").write(json.dumps({"format": "puppetry-input-buffer", "version": 1}) + "\n")
        out({"out": a[1]})
    if cmd == "render":
        mode = opt(a, "--mode", "full")
        if os.environ.get("FAKE_PUPPETRY_FAIL") == mode:
            out({"error": f"fake render failure for {mode}"}, 1)
        time.sleep(float(os.environ.get("FAKE_PUPPETRY_DELAY", 0)))
        start, end = float(opt(a, "--start")), float(opt(a, "--end"))
        fps = float(opt(a, "--fps", 60))
        w, h = SIZES[mode]
        length = max(0.1, end - start)
        dest = a[-1]
        # red = 255 * (t mod 16 s) / 16 (wraps; compare DIFFERENCES mod 256), alpha 128
        vf = "format=rgba,geq=r='255*mod(T,16)/16':g=0:b=0:a=128,format=argb"
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            f"color=c=red:s={w}x{h}:r={fps:g}:d={length:.6f}", "-vf", vf,
                            "-c:v", "qtrle", "-g", "1", dest], capture_output=True, text=True)
        if r.returncode:
            out({"error": r.stderr[-200:]}, 1)
        out({"out": dest, "width": w, "height": h, "frames": int(length * fps), "seconds": length,
             "coverage": "full"})
    if cmd == "align":
        ov, ov_start = opt(a, "--overlay"), float(opt(a, "--overlay-start"))
        clip, clip_end = opt(a, "--clip"), float(opt(a, "--clip-end"))
        d = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                                       clip], capture_output=True, text=True).stdout)["format"]["duration"]
        d = float(d)
        skip = (clip_end - d) - ov_start
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{skip:.6f}", "-i", ov, "-t", f"{d:.6f}",
                            "-c", "copy", a[-1]], capture_output=True, text=True)
        if r.returncode:
            out({"error": r.stderr[-200:]}, 1)
        out({"out": a[-1], "skip_s": skip})
    out({"error": f"fake: unsupported command {cmd}"}, 1)


main()
