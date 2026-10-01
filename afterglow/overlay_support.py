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
    "preview_graph", "available",
]

MANIFEST = "manifest.json"


# ---------------------------------------------------------------- placements
def _norm(p: dict) -> dict:
    out = dict(p)
    out.setdefault("visible", True)
    out["rotation"] = float(out.get("rotation", 0.0) or 0.0)
    return out


def resolve_placements(global_defaults: dict | None = None, clip_type_defaults: dict | None = None) -> dict:
    """aio.resolve_placements, plus `rotation` (degrees) on every piece.
    Per piece: clip type > global > built-in."""
    out = aio.resolve_placements(global_defaults, clip_type_defaults)
    for piece in PIECES:
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


def preview_graph(sidecar: dict, video_w: int, video_h: int, enabled: bool) -> tuple:
    """Previewer: (external_files, lavfi_complex) -- the whole overlay on/off
    at the clip's saved placements, WITH rotation (decision 6). A rotated
    piece is scaled, rotated about its centre (the canvas grows to hold the
    corners) and overlaid offset by half the growth so it stays centred."""
    import math
    placements = placements_of(sidecar)
    d = Path(sidecar["dir"])
    order = [p for p in PIECES if p in sidecar["pieces"]]
    files = [str(d / sidecar["pieces"][p]["file"]) for p in order]
    shown = [(i, p) for i, p in enumerate(order) if enabled and placements.get(p, {}).get("visible", True)]
    if not shown:
        return files, "[vid1] null [vo]"
    chain, last = [], "vid1"
    for n, (i, p) in enumerate(shown, start=1):
        pl = placements[p]
        x, y, w, h = aio.piece_rect(sidecar, p, pl, video_w, video_h)
        out = "vo" if n == len(shown) else f"o{n}"
        rot = pl.get("rotation", 0.0)
        if abs(rot) < 1e-6:
            chain.append(f"[vid{i + 2}] scale={w}:{h} [s{n}]; [{last}][s{n}] overlay=x={x}:y={y}:"
                         f"format=auto:eof_action=pass [{out}]")
        else:
            rad = math.radians(rot)
            rw = abs(w * math.cos(rad)) + abs(h * math.sin(rad))
            rh = abs(w * math.sin(rad)) + abs(h * math.cos(rad))
            ox = x - (rw - w) / 2
            oy = y - (rh - h) / 2
            chain.append(f"[vid{i + 2}] scale={w}:{h},format=argb,{_rot_step(i, pl, w, h)} [s{n}]; "
                         f"[{last}][s{n}] overlay=x={ox:.2f}:y={oy:.2f}:format=auto:eof_action=pass [{out}]")
        last = out
    return files, "; ".join(chain)
