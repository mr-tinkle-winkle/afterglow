"""
afterglow-side glue around the vendored Puppetry module (input_overlay.py).

input_overlay.py is kept byte-identical to the Puppetry side's
afterglow_input_overlay.py so future Puppetry-side versions drop in. What
afterglow needs on top of it lives here:

  * the "rotation" key every placement gets (Puppetry's own schema is
    {x, y, w, visible}; unknown keys are ignored by Puppetry's tools)
  * sidecar file operations -- every file operation on a clip must carry its
    `<clip>.input/` folder along (move/rename, copy, delete, quick trim,
    edit-backup / undo)
  * the previewer's mpv graph with rotation (preview_overlay_args() has none)

A missing or broken overlay must NEVER cost a clip, so everything here that
the capture pipeline calls either succeeds or raises OverlayError.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import input_overlay as aio
from .input_overlay import OverlayError, PIECES, SIDECAR_SUFFIX

__all__ = [
    "aio", "OverlayError", "PIECES", "resolve_placements", "placements_of",
    "save_placements", "sidecar_dir", "load_sidecar", "has_sidecar",
    "move_sidecar", "copy_sidecar", "delete_sidecar", "trim_sidecar", "cut_piece",
    "backup_sidecar_dir", "backup_sidecar", "restore_sidecar", "clear_backup_sidecar",
    "preview_graph", "video_color", "PIECE_LABELS", "piece_label", "all_pieces", "layout_elements", "available", "freeze_input", "capture_clip",
]

MANIFEST = "manifest.json"


# ---------------------------------------------------------------- placements
def _norm(p: dict) -> dict:
    out = dict(p)
    out.setdefault("visible", True)
    out["rotation"] = float(out.get("rotation", 0.0) or 0.0)
    return out


def resolve_placements(global_defaults: dict | None = None, clip_type_defaults: dict | None = None,
                       element_types: dict | None = None) -> dict:
    """aio.resolve_placements, plus `rotation` (degrees) on every piece
    (including "el:<id>" layout elements named in either layer).
    Per piece: clip type > global > built-in."""
    out = aio.resolve_placements(global_defaults, clip_type_defaults, element_types)
    for piece in out:
        rot = 0.0
        for layer in (global_defaults or {}, clip_type_defaults or {}):
            if layer.get(piece) and "rotation" in layer[piece]:
                rot = float(layer[piece]["rotation"] or 0.0)
        out[piece]["rotation"] = rot
    return out


def placements_of(sidecar: dict) -> dict:
    """aio.placements_of with `rotation` (0 when the manifest has none)."""
    out = aio.placements_of(sidecar)
    for piece, info in sidecar["pieces"].items():
        rot = (info.get("placement") or {}).get("rotation", 0.0)
        out[piece]["rotation"] = float(rot or 0.0)
    return {k: _norm(v) for k, v in out.items()}


def save_placements(sidecar: dict, placements: dict) -> None:
    """aio.save_placements, but keeps `rotation` too."""
    d = Path(sidecar["dir"])
    m = json.loads((d / MANIFEST).read_text())
    for piece, p in placements.items():
        if piece in m["pieces"]:
            m["pieces"][piece]["placement"] = {k: p[k] for k in ("x", "y", "w", "rotation", "visible") if k in p}
    (d / MANIFEST).write_text(json.dumps(m, indent=2))
    for piece, p in placements.items():
        if piece in sidecar["pieces"]:
            sidecar["pieces"][piece]["placement"] = dict(m["pieces"][piece]["placement"])


# ---------------------------------------------------------------- Puppetry's layout
PIECE_LABELS = {
    "full": "Whole layout (one picture)",
    "keyboard": "Keyboard",
    "mouse": "Mouse + movement",
    "controller": "Controller",
    "comet": "Movement: comet",
    "mousepad": "Movement: mousepad",
    "joystick": "Movement: joystick",
    "simple": "Held inputs (text)",
    "movement": "Movement page view",
}
_elements_cache: "tuple[float, list] | None" = None


def layout_elements(max_age: float = 5.0) -> list:
    """The elements of the user's Puppetry input-visualizer layout
    ([{"id", "type"}], each renderable as the piece "el:<id>"); [] when
    Puppetry isn't running. Cached for a few seconds (it's a subprocess)."""
    import time
    global _elements_cache
    now = time.time()
    if _elements_cache is not None and now - _elements_cache[0] < max_age:
        return list(_elements_cache[1])
    try:
        els = [e for e in aio.elements() if isinstance(e, dict) and e.get("id")]
    except Exception:  # noqa: BLE001 -- not running, old Puppetry...
        els = []
    _elements_cache = (now, els)
    return list(els)


def element_types() -> dict:
    return {e["id"]: e.get("type", "") for e in layout_elements()}


def element_type(piece: str) -> str:
    return element_types().get(piece[3:], "") if piece.startswith("el:") else ""


def piece_label(piece: str) -> str:
    """Human name of a piece: "Keyboard", "Layout: Left pad (controller)"..."""
    if piece in PIECE_LABELS:
        return PIECE_LABELS[piece]
    if piece.startswith("el:"):
        t = element_type(piece)
        return f"Layout: {piece[3:]}" + (f" ({t})" if t and t != piece[3:] else "")
    return piece


def all_pieces(extra=()) -> list:
    """Every piece a user can pick: the standard ones, then each element of
    Puppetry's current layout as "el:<id>", then any other "el:" piece named
    in `extra` (e.g. stored in a clip type but no longer in the layout)."""
    out = list(PIECES) + [f"el:{e['id']}" for e in layout_elements()]
    for p in extra:
        if p not in out and aio.is_piece(p):
            out.append(p)
    return out


# ---------------------------------------------------------------- capture
def freeze_input(dest) -> Path:
    """Copy Puppetry's live input buffer right now (the moment the save is sent)."""
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    return aio.freeze_input(dest)


def probe_clip(path) -> tuple:
    """(duration, fps) of the clip's video."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=r_frame_rate:format=duration", "-of", "json", str(path)], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout or "{}")
        num, den = (d["streams"][0].get("r_frame_rate") or "60/1").split("/")
        fps = float(num) / float(den or 1)
        return float(d["format"]["duration"]), (fps if 1 <= fps <= 1000 else 60.0)
    except (KeyError, IndexError, ValueError, ZeroDivisionError):
        raise OverlayError(f"can't read {Path(path).name}'s length") from None


def capture_clip(buffer_copy, clip, clip_end: float, pieces: list, placements: dict, offset_ms: float = 0.0,
                 visible_by_default: bool = True, timeout: float | None = None,
                 clip_info: "tuple | None" = None) -> dict:
    """Make `<clip>.input/` for a FINISHED clip that ends at `clip_end` (Unix
    time), from an input buffer frozen when the save was sent.

    Unlike aio.start_clip/finish_clip (which render the WHOLE replay length
    -- 20 minutes of frames for a 1200 s buffer -- then cut it down), this
    renders only the clip's own span, at the clip's own fps, straight into
    place: no align step, no keyframe-snapped stream-copy cut. Pieces render
    in parallel. Same sidecar format. Raises OverlayError if no piece could
    be made (pieces that failed are listed under "errors")."""
    import threading
    clip = Path(clip)
    duration, fps = clip_info or probe_clip(clip)      # (duration, fps) -- probe up front if the clip may move
    start = clip_end - duration
    dest = sidecar_dir(clip)
    tmp = dest.with_name(dest.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    rendered, errors = {}, {}

    def work(piece):
        out = tmp / aio.piece_file(piece)
        try:
            aio._run(["render", "--buffer", buffer_copy, "--mode", piece, "--offset-ms", f"{offset_ms:g}",
                      "--fps", f"{fps:g}", "--start", f"{start:.6f}", "--end", f"{clip_end:.6f}", out],
                     timeout=timeout)
            rendered[piece] = out
        except OverlayError as e:
            errors[piece] = str(e)
    threads = [threading.Thread(target=work, args=(p,), daemon=True) for p in dict.fromkeys(pieces) if aio.is_piece(p)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        shutil.copyfile(buffer_copy, tmp / "inputs.jsonl")
        manifest = {"format": "puppetry-overlay-sidecar", "version": 1, "clip": clip.name, "clip_end": clip_end,
                    "offset_ms": offset_ms, "visible_by_default": visible_by_default, "inputs": "inputs.jsonl",
                    "pieces": {}, "errors": errors}
        for piece in aio._order(rendered):
            w, h = aio._probe_size(rendered[piece])
            pl = dict((placements or {}).get(piece) or aio.default_placement(piece, element_type(piece)))
            pl.setdefault("visible", True)
            pl["rotation"] = float(pl.get("rotation", 0.0) or 0.0)
            manifest["pieces"][piece] = {"file": rendered[piece].name, "width": w, "height": h, "placement": pl}
        if not manifest["pieces"]:
            raise OverlayError("no overlay piece could be made: " +
                               "; ".join(f"{k}: {v}" for k, v in errors.items()))
        (tmp / MANIFEST).write_text(json.dumps(manifest, indent=2))
        shutil.rmtree(dest, ignore_errors=True)
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    manifest["dir"] = str(dest)
    return manifest


# ---------------------------------------------------------------- queries
def available() -> tuple:
    return aio.available()


def sidecar_dir(clip) -> Path:
    return aio.sidecar_dir(clip)


def load_sidecar(clip) -> "dict | None":
    return aio.load_sidecar(clip)


def has_sidecar(clip) -> bool:
    return sidecar_dir(clip).is_dir()


# ---------------------------------------------------------------- file operations
def _rewrite_manifest(d: Path, **changes) -> None:
    mf = d / MANIFEST
    try:
        m = json.loads(mf.read_text())
    except (OSError, ValueError):
        return
    m.update(changes)
    mf.write_text(json.dumps(m, indent=2))


def move_sidecar(src_clip, dst_clip) -> None:
    """Carry `<src>.input/` to `<dst>.input/` (clip renamed/moved). No-op
    when the clip has no sidecar. An existing destination sidecar is replaced."""
    src, dst = sidecar_dir(src_clip), sidecar_dir(dst_clip)
    if not src.is_dir() or src == dst:
        return
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    _rewrite_manifest(dst, clip=Path(dst_clip).name)


def copy_sidecar(src_clip, dst_clip) -> None:
    src, dst = sidecar_dir(src_clip), sidecar_dir(dst_clip)
    if not src.is_dir():
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    _rewrite_manifest(dst, clip=Path(dst_clip).name)


def delete_sidecar(clip) -> None:
    shutil.rmtree(sidecar_dir(clip), ignore_errors=True)


def _probe_duration(path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    try:
        return float(json.loads(r.stdout or "{}")["format"]["duration"])
    except (KeyError, ValueError):
        return 0.0


def _all_intra(path) -> bool:
    """True when every packet of the piece's video stream is a keyframe
    (then a stream copy cuts exactly on any frame)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=flags",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    flags = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    return bool(flags) and all(f.startswith("K") for f in flags)


def cut_piece(src, dst, start_sec: float, duration_sec: float) -> None:
    """Cut one overlay piece to [start, start+duration), frame-exact.
    Puppetry's pieces are meant to be all-intra qtrle, where a stream copy
    is exact; but qtrle only is when encoded with -g 1 (ffmpeg's default has
    a keyframe every 12 frames), so when a piece ISN'T all-intra it is
    re-encoded losslessly (qtrle, argb, -g 1) with an exact decode-side
    seek instead -- slower, still lossless, and the result is all-intra."""
    src, dst = Path(src), Path(dst)
    if _all_intra(src):
        cmd = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start_sec:.6f}", "-i", str(src),
               "-t", f"{duration_sec:.6f}", "-c", "copy", "-avoid_negative_ts", "make_zero", str(dst)]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ss", f"{start_sec:.6f}",
               "-t", f"{duration_sec:.6f}", "-c:v", "qtrle", "-g", "1", "-pix_fmt", "argb", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.exists():
        raise OverlayError(f"cutting overlay piece {src.name!r} failed: {r.stderr[-300:]}")


def trim_sidecar(src_dir, dst_dir, start_sec: float, duration_sec: float, orig_duration: float | None = None) -> None:
    """Cut every piece of the sidecar `src_dir` with the SAME in/out as the
    clip's trim and write the result to `dst_dir` (must differ from
    src_dir). See cut_piece() for exactness. The manifest's `clip_end` moves
    back by however much was cut off the end, so `rerender` still lines up."""
    src_dir, dst_dir = Path(src_dir), Path(dst_dir)
    m = json.loads((src_dir / MANIFEST).read_text())
    dst_dir.mkdir(parents=True, exist_ok=True)
    for piece, info in m.get("pieces", {}).items():
        f = src_dir / info["file"]
        if f.exists():
            cut_piece(f, dst_dir / info["file"], start_sec, duration_sec)
    inputs = src_dir / m.get("inputs", "inputs.jsonl")
    if inputs.exists():
        shutil.copyfile(inputs, dst_dir / inputs.name)
    if orig_duration is not None and "clip_end" in m:
        m["clip_end"] = float(m["clip_end"]) - max(0.0, orig_duration - (start_sec + duration_sec))
    (dst_dir / MANIFEST).write_text(json.dumps(m, indent=2))


# ---------------------------------------------------------------- edit backups
def backup_sidecar_dir(backup_clip) -> Path:
    """Where a clip's sidecar is kept beside its Edit Backup (`<backup>.input`)."""
    return sidecar_dir(backup_clip)


def backup_sidecar(clip, backup_clip) -> None:
    """Copy the clip's current sidecar next to its Edit Backup (first edit
    only -- the caller decides). No-op without a sidecar."""
    src, dst = sidecar_dir(clip), backup_sidecar_dir(backup_clip)
    if not src.is_dir():
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def restore_sidecar(clip, backup_clip) -> None:
    """Undo Edits: the clip's sidecar becomes whatever was backed up (or
    none, when the original had none -- e.g. an Editor Replace gave a clip
    that never had one a derived sidecar)."""
    cur, bak = sidecar_dir(clip), backup_sidecar_dir(backup_clip)
    if cur.exists():
        shutil.rmtree(cur)
    if bak.is_dir():
        shutil.move(str(bak), str(cur))
        _rewrite_manifest(cur, clip=Path(clip).name)


def clear_backup_sidecar(backup_clip) -> None:
    shutil.rmtree(backup_sidecar_dir(backup_clip), ignore_errors=True)


# ---------------------------------------------------------------- previewer graph
def _rot_step(i: int, p: dict, w: int, h: int) -> tuple:
    """(filter text, extra_w, extra_h) rotating piece scaled to w x h."""
    import math
    rad = math.radians(p.get("rotation", 0.0))
    return f"rotate=a={rad:.6f}:c=none:ow=rotw({rad:.6f}):oh=roth({rad:.6f})"


_OVERLAY_FORMATS = {        # source pix_fmt family -> (overlay format=, piece pix_fmt before blending)
    "420": ("yuv420", "yuva420p"), "420p10": ("yuv420p10", "yuva420p10"),
    "422": ("yuv422", "yuva422p"), "422p10": ("yuv422p10", "yuva422p10"),
    "444": ("yuv444", "yuva444p"), "444p10": ("yuv444p10", "yuva444p10"),
    "rgb": ("gbrp", "gbrap"),
}
_MATRICES = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "smpte170m", "bt2020nc": "bt2020",
             "bt2020c": "bt2020", "fcc": "fcc", "smpte240m": "smpte240m"}


def video_color(path) -> dict:
    """The clip's pixel format family, YUV matrix and range -- what the
    previewer's overlay graph must keep so that switching the overlay on/off
    doesn't change how the VIDEO looks (format=auto converted it to RGB with
    swscale's own matrix: a visible brightness/saturation shift)."""
    out = {"family": "420", "matrix": "bt709", "range": "tv"}
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=pix_fmt,color_space,color_range", "-of", "json", str(path)],
                           capture_output=True, text=True, timeout=10)
        st = json.loads(r.stdout or "{}").get("streams", [{}])[0]
    except Exception:  # noqa: BLE001
        return out
    pf = st.get("pix_fmt") or ""
    deep = any(t in pf for t in ("p10", "p12", "p16", "p010", "p016"))
    if pf.startswith(("gbr", "rgb", "bgr", "argb", "abgr", "0rgb", "0bgr")):
        out["family"] = "rgb"
    elif "444" in pf:
        out["family"] = "444p10" if deep else "444"
    elif "422" in pf or pf in ("yuyv422", "uyvy422", "nv16"):
        out["family"] = "422p10" if deep else "422"
    else:
        out["family"] = "420p10" if (deep or pf == "p010le") else "420"
    out["matrix"] = _MATRICES.get(st.get("color_space") or "", "bt709")
    out["range"] = "pc" if (st.get("color_range") == "pc" or pf.startswith("yuvj")) else "tv"
    return out


def preview_graph(sidecar: dict, video_w: int, video_h: int, enabled: bool, color: "dict | None" = None) -> tuple:
    """Previewer: (external_files, lavfi_complex) -- the whole overlay on/off
    at the clip's saved placements, WITH rotation (decision 6). A rotated
    piece is scaled, rotated about its centre (the canvas grows to hold the
    corners) and overlaid offset by half the growth so it stays centred.
    `color` = video_color(clip): the blend happens in the clip's own pixel
    format (never format=auto, which turned the whole video into RGB)."""
    import math
    placements = placements_of(sidecar)
    d = Path(sidecar["dir"])
    order = aio._order(sidecar["pieces"])
    files = [str(d / sidecar["pieces"][p]["file"]) for p in order]
    shown = [(i, p) for i, p in enumerate(order) if enabled and placements.get(p, {}).get("visible", True)]
    if not shown:
        return files, "[vid1] null [vo]"
    color = color or {"family": "420", "matrix": "bt709", "range": "tv"}
    ov_fmt, piece_fmt = _OVERLAY_FORMATS.get(color.get("family"), _OVERLAY_FORMATS["420"])
    if ov_fmt == "gbrp":
        to_main = f"format={piece_fmt}"
    else:   # the pieces are RGB: convert them with the VIDEO's own matrix/range, so their colors are right too
        to_main = (f"scale=out_color_matrix={color.get('matrix', 'bt709')}:out_range={color.get('range', 'tv')},"
                   f"format={piece_fmt}")
    chain, last = [], "vid1"
    for n, (i, p) in enumerate(shown, start=1):
        pl = placements[p]
        x, y, w, h = aio.piece_rect(sidecar, p, pl, video_w, video_h)
        out = "vo" if n == len(shown) else f"o{n}"
        rot = pl.get("rotation", 0.0)
        if abs(rot) < 1e-6:
            chain.append(f"[vid{i + 2}] scale={w}:{h},{to_main} [s{n}]; [{last}][s{n}] overlay=x={x}:y={y}:"
                         f"format={ov_fmt}:eof_action=pass [{out}]")
        else:
            rad = math.radians(rot)
            rw = abs(w * math.cos(rad)) + abs(h * math.sin(rad))
            rh = abs(w * math.sin(rad)) + abs(h * math.cos(rad))
            ox = x - (rw - w) / 2
            oy = y - (rh - h) / 2
            chain.append(f"[vid{i + 2}] scale={w}:{h},format=argb,{_rot_step(i, pl, w, h)},{to_main} [s{n}]; "
                         f"[{last}][s{n}] overlay=x={ox:.2f}:y={oy:.2f}:format={ov_fmt}:eof_action=pass [{out}]")
        last = out
    return files, "; ".join(chain)
