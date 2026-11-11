"""
Startup benchmark: N clips in a throwaway HOME, then times the same steps
`afterglow.gui.main.main()` runs (imports, QApplication, MainWindow(), show,
first full Library fill).

    Xvfb :99 -screen 0 1920x1080x24 +extension GLX &  DISPLAY=:99 openbox &
    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tools/bench_startup.py --clips 93
    ... --keep-home /tmp/bench_home   reuse the HOME (2nd run = warm thumbnail cache)
    ... --profile /tmp/startup.prof   cProfile the whole thing (pstats / snakeviz)

Prints one line per stage and "first paint" (window shown and painted once)
separately from "library filled" (every card built, every thumbnail in).
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

ap = argparse.ArgumentParser()
ap.add_argument("--clips", type=int, default=93)
ap.add_argument("--keep-home", default="")
ap.add_argument("--profile", default="")
ap.add_argument("--size", default="1280x720")
args = ap.parse_args()

HOME = args.keep_home or tempfile.mkdtemp(prefix="bench_home_")
os.makedirs(HOME, exist_ok=True)
os.environ["HOME"] = HOME
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.mkdtemp(prefix="bench_run_"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

T0 = time.perf_counter()
marks = []


def mark(label):
    marks.append((label, time.perf_counter()))


def setup_clips():
    from afterglow import config, db, library
    db.init_db()
    clips = config.load().clips_path()
    clips.mkdir(parents=True, exist_ok=True)
    have = len([p for p in clips.iterdir() if p.suffix == ".mp4"])
    if have >= args.clips:
        return
    src = os.path.join(HOME, "_src.mp4")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={args.size}:rate=60", "-f", "lavfi", "-i", "sine", "-t", "6",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-c:a", "aac", src], check=True)
    import shutil
    for i in range(have, args.clips):
        dst = clips / f"Clip {i:03d} some longer title here.mp4"
        shutil.copy(src, dst)
        os.utime(dst, (time.time() - i * 3600, time.time() - i * 3600))
    library.scan_and_ingest_new_videos()
    for i, v in enumerate(library.list_videos()):
        if i % 3 == 0:
            library.add_tag_to_video(v.id, "funny")
        if i % 5 == 0:
            library.add_tag_to_video(v.id, "clutch")


# Setup runs in a child so this process starts as cold as a real launch.
if os.environ.get("_BENCH_SETUP") == "1":
    setup_clips()
    sys.exit(0)
subprocess.run([sys.executable, os.path.abspath(__file__), *sys.argv[1:], "--keep-home", HOME],
               env={**os.environ, "_BENCH_SETUP": "1"}, check=True)


def run():
    global T0
    T0 = time.perf_counter()
    import locale
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication
    from afterglow import db
    from afterglow.gui.main_window import MainWindow
    mark("imports")
    db.init_db()
    fmt = QSurfaceFormat()
    fmt.setSwapInterval(1)
    fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv[:1])
    locale.setlocale(locale.LC_NUMERIC, "C")
    mark("QApplication")
    w = MainWindow()
    mark("MainWindow()")
    w.resize(1600, 900)
    w.show()
    deadline = time.perf_counter() + 60
    while not (w.windowHandle() and w.windowHandle().isExposed()) and time.perf_counter() < deadline:
        app.processEvents()
    app.processEvents()   # the paint that follows the expose
    w.grab()              # force anything still unpainted through once
    marks.append(("first paint (window usable)", time.perf_counter()))

    lp = w.library_page

    def busy():
        if getattr(lp, "_loading_tabs", None):
            return True
        if any(getattr(t, "_refresh_in_progress", False) for t in (lp.local_tab, lp.uploaded_tab)):
            return True
        try:
            from afterglow.gui import thumb_loader
            return thumb_loader.loader().busy()
        except ImportError:
            return False
    deadline = time.perf_counter() + 120
    idle_since = None
    while time.perf_counter() < deadline:
        app.processEvents()
        if busy():
            idle_since = None
        elif idle_since is None:
            idle_since = time.perf_counter()
        elif time.perf_counter() - idle_since > 0.4:
            break
        time.sleep(0.002)
    marks.append(("library filled (cards + thumbnails)", idle_since or time.perf_counter()))
    cards = len(lp.local_tab._cards)
    prev = T0
    print(f"clips={args.clips} cards={cards} home={HOME}")
    for label, t in marks:
        print(f"  {label:38s} +{t - prev:6.3f}s   (at {t - T0:6.3f}s)")
        prev = t
    w.close()


if args.profile:
    import cProfile
    cProfile.run("run()", args.profile)
else:
    run()
