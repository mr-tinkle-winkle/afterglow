"""
Input overlay (Puppetry) -- writing the OUTPUT's overlay sidecar.

An Editor export never burns the overlay into the video (every overlay
element is rendered as if hidden, see render.Renderer(overlays=False)).
Instead the exported file gets its own `<output>.input/` sidecar -- the same
format a captured clip has -- so the overlay stays toggleable in the
previewer and editable in the Editor for the NEW file too.

What goes in it is every overlay piece with VISIBLE content on the timeline:
the pieces attached to clips and the detached overlay elements (with their
transforms, keyframes and effects), exactly as they previewed in the Editor.

Two ways to build a piece's track, chosen per piece name:

* STATIC layout -- every contribution is an attached piece with the same
  placement on a plain (untransformed, unfaded, canvas-sized) clip: the
  piece is rebuilt at its NATIVE size from the source pieces, frame for
  frame (a stream copy when the timeline is exactly one such clip), and the
  manifest keeps the piece's REAL placement (tight keyboard/mouse boxes, not
  canvas-sized layers).
* CANVAS -- anything animated, transformed, detached or placed differently
  per clip: the Renderer draws only that piece's content, frame by frame,
  at the canvas size and fps, and the manifest places it full-frame
  (x=0, y=0, w=1, rotation=0).

Pieces are transparent qtrle (ARGB), all-intra, `.mov`. The manifest is
marked `"derived": true` and has no `inputs` (a timeline-built overlay can't
be re-rendered from raw input; the Editor project keeps the original
per-clip pieces for that).
"""
from __future__ import annotations

import json
import math
import os
import shutil
import threading
from pathlib import Path
from typing import Callable

import av
import numpy as np
from PySide6.QtGui import QImage

from .. import overlay_support
from . import media
from .model import EPS, KIND_AV, Project, Segment
from .render import Renderer, ExportCancelled, zoom_factor

_PLACEMENT_EPS = 1e-6


# ---------------------------------------------------------------- what is in the timeline
class Contribution:
    """One visible piece of overlay content: an attached piece on a clip
    (`piece` + `part` set) or a detached overlay element (`part` None)."""

    def __init__(self, seg: Segment, part=None, piece=None):
        self.seg, self.part, self.piece = seg, part, piece


def contributions(project: Project) -> "dict[str, list[Contribution]]":
    out: "dict[str, list[Contribution]]" = {}
    for track in project.tracks:        # top track first
        if track.hidden:
            continue
        for seg in track.segments:
            if not seg.visible or not seg.has_video:
                continue
            if seg.overlay_piece:
                out.setdefault(seg.overlay_piece, []).append(Contribution(seg))
                continue
            for part in seg.parts:
                if part.kind != KIND_AV or not part.has_video or not part.visible:
                    continue
                for o in part.overlays:
                    if o.visible and o.source:
                        out.setdefault(o.name, []).append(Contribution(seg, part, o))
    return out


def overlay_names(project: Project) -> list[str]:
    """Names of the overlay pieces with visible content (what a sidecar would hold)."""
    return [n for n in overlay_support.PIECES if n in contributions(project)] + \
           [n for n in contributions(project) if n not in overlay_support.PIECES]


def has_overlay_content(project: Project) -> bool:
    return bool(contributions(project))


def _plain_clip(project: Project, seg: Segment) -> bool:
    """A clip drawn exactly as its source: nothing that would move/scale/fade the picture."""
    return (not seg.keyframes and seg.transform.is_identity() and zoom_factor(seg, 0.0) == 1.0
            and seg.zoom_amount == 1.0 and seg.fade_in <= EPS and seg.fade_out <= EPS
            and seg.transition_in is None and not seg.shadow)


def _static_plan(project: Project, name: str, items: "list[Contribution]", sizes: dict) -> "dict | None":
    """The common placement + piece size when `name` has a static layout, else None."""
    if any(c.part is None for c in items):
        return None
    first = items[0].piece
    ref = (first.x, first.y, first.w, first.rotation, first.width, first.height)
    for c in items:
        o = c.piece
        cur = (o.x, o.y, o.w, o.rotation, o.width, o.height)
        if any(abs(a - b) > _PLACEMENT_EPS for a, b in zip(cur, ref)):
            return None
        if not _plain_clip(project, c.seg):
            return None
        sz = sizes.get(c.part.source)
        if sz is None or sz != (project.width, project.height):
            return None
        if not os.path.exists(c.piece.source):
            return None
    width, height = first.width, first.height
    if not (width and height):
        width, height = aio_size(first.source)
    return {"x": first.x, "y": first.y, "w": first.w, "rotation": first.rotation,
            "width": width, "height": height}


def _source_sizes(items: "list[Contribution]") -> dict:
    sizes = {}
    for c in items:
        if c.part is not None and c.part.source not in sizes:
            try:
                info = media.probe(c.part.source)
                sizes[c.part.source] = (info["width"], info["height"])
            except Exception:
                sizes[c.part.source] = None
    return sizes


# ---------------------------------------------------------------- encoding
def _open_writer(path: str, width: int, height: int, fps):
    out = av.open(path, "w", format="mov")
    stream = out.add_stream("qtrle", rate=fps)
    stream.width, stream.height = width, height
    stream.pix_fmt = "argb"
    stream.codec_context.gop_size = 1            # all-intra: stream-copy cuts are frame-exact
    return out, stream


def _write_frame(out, stream, frame: av.VideoFrame, index: int) -> None:
    frame = frame.reformat(format="argb")
    frame.pts = index
    for pkt in stream.encode(frame):
        out.mux(pkt)


def _transparent_frame(width: int, height: int) -> av.VideoFrame:
    return av.VideoFrame.from_ndarray(np.zeros((height, width, 4), np.uint8), format="bgra")


def _qimage_to_frame(img: QImage) -> av.VideoFrame:
    img = img.convertToFormat(QImage.Format_ARGB32)       # non-premultiplied BGRA bytes on little-endian
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine() // 4, 4)[:, :w, :].copy()
    return av.VideoFrame.from_ndarray(arr, format="bgra")


def _finish_writer(out, stream) -> None:
    for pkt in stream.encode(None):
        out.mux(pkt)
    out.close()


def _frame_count(project: Project) -> "tuple[object, int]":
    rate = media.fps_fraction(project.fps)
    n = max(1, int(math.ceil(project.video_duration * rate - 1e-6)))
    return rate, n


def _render_canvas_piece(project: Project, name: str, dst: str, progress, cancel) -> None:
    rate, n = _frame_count(project)
    w, h = project.width - project.width % 2, project.height - project.height % 2
    out, stream = _open_writer(dst, w, h, rate)
    renderer = Renderer(project, overlays=True)
    try:
        for i in range(n):
            if cancel is not None and cancel.is_set():
                raise ExportCancelled()
            t = float(i / rate)
            img = renderer.frame_piece(t, name, w, h)
            _write_frame(out, stream, _qimage_to_frame(img), i)
            if progress is not None and i % 15 == 0:
                progress(i / n)
        _finish_writer(out, stream)
    finally:
        renderer.close()


def _render_static_piece(project: Project, items: "list[Contribution]", plan: dict, dst: str, progress, cancel) -> None:
    """The piece at its native size, frame for frame from the source pieces."""
    rate, n = _frame_count(project)
    pw, ph = plan["width"], plan["height"]
    out, stream = _open_writer(dst, pw, ph, rate)
    sources: "dict[str, media.VideoSource]" = {}
    blank = _transparent_frame(pw, ph)
    try:
        for i in range(n):
            if cancel is not None and cancel.is_set():
                raise ExportCancelled()
            t = float(i / rate)
            frame = blank
            for c in items:                                  # top track first
                seg, part = c.seg, c.part
                if not seg.covers(t):
                    continue
                local = t - seg.start
                if not part.active_at(local):
                    continue
                src = sources.get(c.piece.source)
                if src is None:
                    src = sources[c.piece.source] = media.VideoSource(c.piece.source)
                f = src.frame_at(part.source_time(local))
                if f is not None:
                    frame = f.reformat(width=pw, height=ph, format="bgra") if (f.width, f.height) != (pw, ph) \
                        else f.reformat(format="bgra")
                    break
            _write_frame(out, stream, frame, i)
            if progress is not None and i % 15 == 0:
                progress(i / n)
        _finish_writer(out, stream)
    finally:
        for s in sources.values():
            s.close()


def _single_plain_clip(project: Project, items: "list[Contribution]") -> bool:
    """The timeline is exactly this one clip, from t=0 to the end at normal
    speed, with nothing else on it that draws or moves anything -> the piece
    is a stream-copy cut of the source piece."""
    if len(items) != 1:
        return False
    c = items[0]
    segs = [s for s in project.all_segments() if not s.overlay_piece]
    if len(segs) != 1 or segs[0] is not c.seg or len(c.seg.parts) != 1:
        return False
    return (abs(c.seg.start) < EPS and abs(c.part.offset) < EPS and abs(c.part.speed - 1.0) < 1e-9
            and abs(c.seg.duration - project.video_duration) < EPS)


# ---------------------------------------------------------------- the sidecar
def write_sidecar(project: Project, out_path: "str | Path", progress: "Callable[[float], None] | None" = None,
                  cancel: "threading.Event | None" = None) -> "dict | None":
    """Write `<out_path>.input/` for the exported video `out_path`.
    Returns the manifest, or None when the timeline has no visible overlay
    content (any stale sidecar of `out_path` is removed then -- it wouldn't
    match the new video). Raises ExportCancelled / OverlayError."""
    out_path = Path(out_path)
    dest = overlay_support.sidecar_dir(out_path)
    items_by_name = contributions(project)
    if not items_by_name:
        shutil.rmtree(dest, ignore_errors=True)
        return None
    tmp = dest.with_name(dest.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    pieces = {}
    names = overlay_names(project)
    try:
        for idx, name in enumerate(names):
            items = items_by_name[name]
            sizes = _source_sizes(items)
            plan = _static_plan(project, name, items, sizes)
            dst = tmp / f"{name}.mov"

            def sub(frac, i=idx):
                if progress is not None:
                    progress((i + frac) / len(names))
            if plan is not None and _single_plain_clip(project, items):
                c = items[0]
                overlay_support.cut_piece(c.piece.source, dst, c.part.src_in, c.part.src_out - c.part.src_in)
            elif plan is not None:
                _render_static_piece(project, items, plan, str(dst), sub, cancel)
            else:
                _render_canvas_piece(project, name, str(dst), sub, cancel)
            if plan is not None:
                w, h = aio_size(dst)
                placement = {"x": plan["x"], "y": plan["y"], "w": plan["w"], "rotation": plan["rotation"],
                             "visible": True}
            else:
                w, h = aio_size(dst)
                placement = {"x": 0.0, "y": 0.0, "w": 1.0, "rotation": 0.0, "visible": True}
            pieces[name] = {"file": dst.name, "width": w, "height": h, "placement": placement}
        manifest = {"format": "puppetry-overlay-sidecar", "version": 1, "clip": out_path.name,
                    "offset_ms": 0, "visible_by_default": bool(project.overlay_visible_by_default),
                    "derived": True, "pieces": pieces, "errors": {}}
        (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2))
        shutil.rmtree(dest, ignore_errors=True)
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    manifest["dir"] = str(dest)
    if progress is not None:
        progress(1.0)
    return manifest


def aio_size(path) -> "tuple[int, int]":
    return overlay_support.aio._probe_size(path)
