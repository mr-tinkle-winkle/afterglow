"""
Smart rendering for the Editor's export (render.export): only the frames
that have to be drawn are encoded.

The timeline is planned frame by frame into three kinds of pieces:

* COPY -- runs where the output is one untouched source clip, shown at
  normal speed at the canvas size and frame rate (render.Renderer
  .passthrough_part). Every whole, cleanly decodable GOP of the source
  inside such a run is copied bit for bit (smartcut.py): no rendering, no
  encoding, no quality loss. A plain trim of a clip copies almost all of it.
* REUSE -- a rendered chunk whose picture is exactly what the previous
  export of the same output file contained (same elements, same timing
  relative to the chunk, same source files, same settings, same renderer
  code): its packets are copied straight from that previous file. Tweaking a
  caption at 0:40 re-renders only the couple of seconds around it. The
  fingerprints live in CONFIG_DIR/render_cache/<hash of the output path>.json
  (with the output file's size+mtime, so a file changed some other way is
  never trusted).
* RENDER -- everything else, drawn by the Renderer and encoded by x264 in
  chunks of about two seconds on a fixed grid (so chunk boundaries -- and
  with them the reuse fingerprints -- don't move when something elsewhere on
  the timeline changes). Chunks are encoded in parallel, one renderer per
  worker.

Rendered pictures are converted to YUV with the SAME matrix/range as the
source video (smartcut.to_output_yuv) and the stream is tagged like the
source, so re-rendered, passed-through and copied frames all match.

Anything unexpected raises smartcut.Unsupported and render.export falls back
to its plain full render.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

import av

from .. import smartcut
from ..config import CONFIG_DIR
from .model import EPS, KIND_AV, KIND_TEXT, Project

CACHE_DIR = CONFIG_DIR / "render_cache"
CHUNK_SECONDS = 2.0
MIN_COPY_FRAMES = 2        # copying fewer frames than this isn't worth a GOP boundary


def _code_version() -> str:
    """Changes whenever the drawing code changes, so reused chunks are only
    ever ones the current code would draw identically."""
    h = hashlib.sha1()
    here = Path(__file__).parent
    for name in ("render.py", "comic.py", "media.py", "model.py", "smart_export.py"):
        try:
            h.update((here / name).read_bytes())
        except OSError:
            pass
    h.update(av.__version__.encode())
    h.update(smartcut.ENCODER_VERSION.encode())
    return h.hexdigest()


_CODE_VERSION = None


def code_version() -> str:
    global _CODE_VERSION
    if _CODE_VERSION is None:
        _CODE_VERSION = _code_version()
    return _CODE_VERSION


# =========================================================================
# planning
# =========================================================================
def output_color(project: Project) -> dict:
    """Colour of the output: that of the first video source on the timeline
    (all of a user's OBS clips share it), else BT.709 limited."""
    for seg in sorted(project.all_segments(), key=lambda s: s.start):
        for p in seg.parts:
            if p.kind == KIND_AV and p.has_video and p.source and os.path.exists(p.source):
                try:
                    return dict(smartcut.video_info(p.source).color)
                except Exception:  # noqa: BLE001
                    continue
    return {"color_range": 1, "colorspace": 1, "color_primaries": 1, "color_trc": 1}


def _passthrough_runs(renderer, n_frames: int, rate: Fraction, width: int, height: int) -> list:
    """[(f0, f1, source path, source time of f0)] -- maximal runs of output
    frames showing consecutive frames of one untouched source clip."""
    runs = []
    cur = None
    fd = 1.0 / float(rate)
    for i in range(n_frames):
        t = float(i / rate) + 1e-5
        hit = renderer.passthrough_part(t, width, height)
        if hit is not None:
            part, local = hit
            if abs(part.speed - 1.0) > 1e-9:
                hit = None
        if hit is None:
            if cur is not None:
                runs.append(cur)
                cur = None
            continue
        part, local = hit
        st = part.source_time(local)
        if cur is not None and cur[2] == part.source and abs((cur[3] + (i - cur[0]) * fd) - st) < fd * 0.25:
            cur[1] = i + 1
            continue
        if cur is not None:
            runs.append(cur)
        cur = [i, i + 1, part.source, st]
    if cur is not None:
        runs.append(cur)
    return [tuple(r) for r in runs]


def _copy_piece(run, rate: Fraction, color: dict, width: int, height: int):
    """The CopyChunk covering the whole clean GOPs inside a passthrough run,
    or None."""
    f0, f1, path, st0 = run
    try:
        info = smartcut.video_info(path)
    except smartcut.Unsupported:
        return None
    if smartcut.eligible(info) or (info.width, info.height) != (width, height):
        return None
    if abs(float(info.rate) - float(rate)) > 1e-6 * float(rate):
        return None
    if info.color != color:
        return None
    fd = 1.0 / float(rate)
    # source pts are absolute (start offset included); part source times are from 0
    s0 = info.start + st0
    s1 = s0 + (f1 - f0) * fd
    last_frame = info.start + info.duration - fd
    to_eof = s1 >= last_frame + fd * 0.5 - 1e-6
    first, last = smartcut.cut_points(path, s0, None if to_eof else s1, fd)
    if first is None or first.pts > s1 - fd * 0.5:
        return None
    if to_eof:
        end = None
        at0 = f0 + int(round((first.pts - s0) / fd))
        end_frame = min(f1, at0 + int(round((last_frame - first.pts) / fd)) + 1)    # frames the file really has
    else:
        if last is None:
            return None
        end = last.pts
        end_frame = f0 + int(round((end - s0) / fd))
    at = f0 + int(round((first.pts - s0) / fd))
    n = end_frame - at
    if n < MIN_COPY_FRAMES:
        return None
    return smartcut.CopyChunk(path, first.pts, end, at, n)


def plan(project: Project, renderer, n_frames: int, rate: Fraction, width: int, height: int, color: dict) -> list:
    """Ordered pieces covering frames [0, n_frames): CopyChunk objects and
    ("render", f0, f1) tuples (render ranges cut on the fixed chunk grid)."""
    copies = []
    for run in _passthrough_runs(renderer, n_frames, rate, width, height):
        piece = _copy_piece(run, rate, color, width, height)
        if piece is not None:
            copies.append(piece)
    grid = max(1, int(round(CHUNK_SECONDS * float(rate))))
    out = []
    pos = 0

    def render_range(a, b):
        while a < b:
            nxt = min(b, (a // grid + 1) * grid)
            out.append(("render", a, nxt))
            a = nxt

    for c in sorted(copies, key=lambda c: c.at):
        render_range(pos, c.at)
        out.append(c)
        pos = c.at + c.n
    render_range(pos, n_frames)
    return out


# =========================================================================
# reuse fingerprints
# =========================================================================
def _file_id(path: str) -> list:
    try:
        st = os.stat(path)
        return [path, st.st_size, st.st_mtime_ns]
    except OSError:
        return [path, None, None]


AUDIO_ONLY_FIELDS = ("volume", "muted")


def _seg_view(seg, t0: float, picture: bool = False) -> dict:
    d = seg.to_dict() if hasattr(seg, "to_dict") else __import__("dataclasses").asdict(seg)
    d.pop("id", None)
    if picture:                       # what only changes the sound doesn't change the picture
        for k in AUDIO_ONLY_FIELDS:
            d.pop(k, None)
        kf = dict(d.get("keyframes") or {})
        kf.pop("volume", None)
        d["keyframes"] = kf
        for pd in d.get("parts", []):
            pd.pop("gain", None)
            pd.pop("has_audio", None)
    d["start"] = round(seg.start - t0, 9)
    files = []
    for p in seg.parts:
        if p.source:
            files.append(_file_id(p.source))
        if p.kind == KIND_TEXT and p.text is not None and getattr(p.text, "image_path", ""):
            files.append(_file_id(p.text.image_path))
    d["_files"] = files
    return d


def fingerprint(project: Project, f0: int, f1: int, rate: Fraction, ctx: dict) -> str:
    """Everything that decides how output frames [f0, f1) look."""
    from .render import previous_on_track
    t0, t1 = float(f0 / rate), float(f1 / rate)
    layers = []
    for track in project.tracks:
        if track.hidden:
            continue
        segs = []
        for seg in track.segments:
            if not seg.has_video or not seg.visible or seg.overlay_piece:
                continue
            if seg.end <= t0 + EPS or seg.start >= t1 - EPS:
                continue
            segs.append(_seg_view(seg, t0, picture=True))
            tr = seg.transition_in
            if tr is not None and tr.duration > EPS and seg.start < t1 and seg.start + tr.duration > t0:
                prev = previous_on_track(track, seg)
                if prev is not None:
                    segs.append({"_transition_from": _seg_view(prev, t0, picture=True)})
        if segs:
            layers.append(segs)
    head = {k: v for k, v in project.to_dict().items()
            if k not in ("tracks", "output_path", "library_video_id", "unsaved_changes", "export_pending",
                         "overlay_visible_by_default")}
    blob = json.dumps({"ctx": ctx, "n": f1 - f0, "project": head, "layers": layers}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def audio_fingerprint(project: Project, n_frames: int, rate: Fraction, ctx: dict) -> str:
    """Everything that decides the exported audio."""
    tracks = []
    for track in project.tracks:
        if track.hidden:
            continue
        segs = []
        for seg in track.segments:
            if not seg.has_audio:
                continue
            d = _seg_view(seg, 0.0)
            segs.append(d)
            if seg.transition_in is not None:
                from .render import previous_on_track
                prev = previous_on_track(track, seg)
                if prev is not None:
                    segs.append({"_transition_from": _seg_view(prev, 0.0)})
        tracks.extend(json.dumps(x, sort_keys=True, default=str) for x in segs)
    tracks.sort()                                 # mixing is a sum: layer order doesn't matter
    blob = json.dumps({"code": ctx.get("code"), "n": n_frames, "rate": str(rate), "tracks": tracks},
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def source_audio(project: Project, n_frames: int, rate: Fraction) -> "smartcut.AudioCut | None":
    """When the timeline's sound is exactly one source clip's sound, untouched
    (from the start of the timeline, normal speed, full volume, no fades /
    keyframes / transitions), copy it instead of decoding + mixing +
    re-encoding it."""
    cands = []
    for track in project.tracks:
        if track.hidden:
            continue
        for seg in track.segments:
            if seg.has_audio and not seg.muted and any(p.has_audio and p.gain > 0 for p in seg.parts):
                cands.append(seg)
    if len(cands) != 1:
        return None
    seg = cands[0]
    parts = [p for p in seg.parts if p.has_audio and p.gain > 0]
    if len(parts) != 1:
        return None
    part = parts[0]
    dur = n_frames / float(rate)
    if (abs(seg.start) > EPS or abs(part.offset) > EPS or seg.transition_in is not None or "volume" in seg.keyframes
            or abs(seg.volume - 1.0) > 1e-9 or abs(part.gain - 1.0) > 1e-9 or seg.fade_in > EPS or seg.fade_out > EPS
            or abs(part.speed - 1.0) > 1e-9 or part.kind != KIND_AV or seg.duration < dur - 1.5 / float(rate)):
        return None
    try:
        info = smartcut.video_info(part.source)
    except smartcut.Unsupported:
        return None
    if not info.has_audio:
        return None
    return smartcut.AudioCut(part.source, info.start + part.src_in, info.start + part.src_in + dur, first_only=True)


def cache_path(out_path: str) -> Path:
    return CACHE_DIR / (hashlib.sha1(os.path.abspath(out_path).encode()).hexdigest()[:20] + ".json")


def load_cache(out_path: str, ctx: dict) -> "dict | None":
    """{fingerprint: [at, n]} of the chunks in the current file at out_path,
    when it is the file our last export wrote (unchanged since)."""
    try:
        data = json.loads(cache_path(out_path).read_text())
        st = os.stat(out_path)
    except (OSError, ValueError):
        return None
    if data.get("size") != st.st_size or data.get("mtime_ns") != st.st_mtime_ns:
        return None
    if data.get("ctx") != ctx or data.get("sps") is None:
        return None
    return data


def save_cache(out_path: str, ctx: dict, chunks: dict, sps: int, audio_fp: "str | None" = None) -> None:
    try:
        st = os.stat(out_path)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache_path(out_path).with_suffix(".tmp")
        tmp.write_text(json.dumps({"size": st.st_size, "mtime_ns": st.st_mtime_ns, "ctx": ctx, "sps": sps,
                                   "chunks": chunks, "audio": audio_fp}))
        os.replace(tmp, cache_path(out_path))
        files = sorted(CACHE_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
        for f in files[:-300]:                      # keep the 300 most recent outputs' fingerprints
            f.unlink()
    except OSError:
        pass


def forget_cache(out_path: str) -> None:
    try:
        cache_path(out_path).unlink()
    except OSError:
        pass


# =========================================================================
# the export
# =========================================================================
def smart_export(project: Project, out_path: str, rate: Fraction, width: int, height: int, n_frames: int,
                 crf: int, preset: str, jobs: int, progress, cancel, audio_job, make_renderer,
                 frame_for) -> dict:
    """Plan, render/copy/reuse, mux. `audio_job(path)` renders the audio
    (render.export's), `make_renderer()` a Renderer(overlays=False),
    `frame_for(renderer, i, last)` -> (frame, last) for output frame i.
    Returns stats {"copied", "reused", "rendered"} (frames)."""
    from .render import ExportCancelled
    color = output_color(project)
    ctx = {"w": width, "h": height, "rate": str(rate), "crf": crf, "preset": preset, "color": color,
           "code": code_version()}
    planner = make_renderer()
    try:
        pieces = plan(project, planner, n_frames, rate, width, height, color)
    finally:
        planner.close()
    # SPS/PPS id for our chunks: not used by any copied source
    used = set()
    for p in pieces:
        if isinstance(p, smartcut.CopyChunk):
            a = smartcut.video_info(p.path).avcc
            used |= a.sps_ids() | a.pps_ids()
    old = load_cache(out_path, ctx) if os.path.exists(out_path) else None
    sps = old["sps"] if old is not None and old["sps"] not in used else smartcut.pick_sps_id(used)
    if old is not None and old["sps"] != sps:
        old = None

    tmpdir = tempfile.mkdtemp(prefix=".afterglow_render_", dir=str(Path(out_path).parent))
    stats = {"copied": 0, "reused": 0, "rendered": 0}
    try:
        # keep a stable view of the previous output to reuse from (out_path is replaced at the end)
        old_file = None
        if old is not None:
            old_file = os.path.join(tmpdir, "previous" + Path(out_path).suffix)
            try:
                os.link(out_path, old_file)
            except OSError:
                old_file = out_path
        chunks: list = []
        to_render = []
        new_cache: dict = {}
        for p in pieces:
            if isinstance(p, smartcut.CopyChunk):
                chunks.append(p)
                stats["copied"] += p.n
                continue
            _k, f0, f1 = p
            fp = fingerprint(project, f0, f1, rate, ctx)
            hit = old["chunks"].get(fp) if old is not None else None
            if hit is not None and hit[1] == f1 - f0:
                chunks.append(smartcut.FileChunk(old_file, f0, f1 - f0, offset=hit[0], only_sps=sps))
                stats["reused"] += f1 - f0
            else:
                path = os.path.join(tmpdir, f"chunk{f0}.mp4")
                fc = smartcut.FileChunk(path, f0, f1 - f0)
                chunks.append(fc)
                to_render.append((fc, f0, f1))
                stats["rendered"] += f1 - f0
            new_cache[fp] = [f0, f1 - f0]

        total = max(1, stats["rendered"])
        done = [0]
        lock = threading.Lock()

        def on_frame():
            with lock:
                done[0] += 1

        local = threading.local()
        renderers = []
        cpus = os.cpu_count() or 2
        workers = max(1, min(jobs, len(to_render)))
        threads_each = max(1, cpus // workers)

        def render_chunk(job):
            fc, f0, f1 = job
            if cancel is not None and cancel.is_set():
                raise ExportCancelled()
            r = getattr(local, "r", None)
            if r is None:
                r = local.r = make_renderer()
                with lock:
                    renderers.append(r)

            def frames():
                last = None
                for i in range(f0, f1):
                    vf, last = frame_for(r, i, last)
                    yield vf
            n = smartcut.encode_frames(frames(), fc.path, rate, width, height, crf, preset, sps, color,
                                       threads=threads_each, cancel=cancel, on_frame=on_frame)
            if n != f1 - f0:
                raise smartcut.Unsupported("rendered frame count mismatch")

        audio_path = os.path.join(tmpdir, "audio.m4a")
        audio_err: list = []
        audio_done = threading.Event()
        has_audio = audio_job is not None
        audio_fp = audio_fingerprint(project, n_frames, rate, ctx) if has_audio else None
        audio_src = None                     # copy instead of render: the old output's, or the source's own
        if has_audio:
            if old is not None and old.get("audio") == audio_fp and old_file is not None:
                audio_src = smartcut.AudioCut(old_file, 0.0, None)
                stats["audio"] = "reused"
            else:
                audio_src = source_audio(project, n_frames, rate)
                if audio_src is not None:
                    stats["audio"] = "copied"

        def run_audio():
            try:
                audio_job(audio_path)
            except BaseException as e:  # noqa: BLE001
                audio_err.append(e)
                if cancel is not None:
                    cancel.set()
            finally:
                audio_done.set()
        if has_audio and audio_src is None:
            stats["audio"] = "rendered"
            threading.Thread(target=run_audio, daemon=True, name="export-audio").start()
        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="export-chunk") as pool:
                futs = [pool.submit(render_chunk, j) for j in to_render]
                for f in futs:
                    while True:
                        try:
                            f.result(timeout=0.05)
                            break
                        except (TimeoutError, __import__("concurrent.futures").futures.TimeoutError):
                            pass
                        except Exception:
                            if cancel is not None:
                                cancel.set()
                            raise
                        finally:
                            if progress is not None:
                                progress(min(0.95, 0.95 * done[0] / total))
                    if progress is not None:
                        progress(min(0.95, 0.95 * done[0] / total))
        finally:
            for r in renderers:
                r.close()
        if has_audio and audio_src is None:
            while not audio_done.wait(0.05):
                pass
            if audio_err:
                raise audio_err[0]
        if cancel is not None and cancel.is_set():
            raise ExportCancelled()
        template = smartcut.video_info(next(c.path for c in chunks))
        tmp_out = os.path.join(tmpdir, "out" + Path(out_path).suffix)
        fmt = smartcut._fmt_for(out_path) or "mp4"
        try:
            smartcut.mux_chunks(tmp_out, chunks, rate, template,
                                ([audio_src] if audio_src is not None else [audio_path]) if has_audio else [], fmt=fmt)
        except smartcut.Unsupported:
            raise
        except Exception:  # noqa: BLE001 -- the copied audio wouldn't go into this container: render it
            if audio_src is None:
                raise
            audio_job(audio_path)
            stats["audio"] = "rendered"
            smartcut.mux_chunks(tmp_out, chunks, rate, template, [audio_path], fmt=fmt)
        if progress is not None:
            progress(0.99)
        os.replace(tmp_out, out_path)
        save_cache(out_path, ctx, new_cache, sps, audio_fp)
        return stats
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
