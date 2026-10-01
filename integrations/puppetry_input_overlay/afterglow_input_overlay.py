"""
afterglow <-> Puppetry input overlay: a drop-in module for afterglow.

Puppetry (with "Layered Replay Buffer" on) keeps a rolling record of every
keyboard / mouse / controller input, the same length as OBS's replay
buffer, and renders its overlay as TRANSPARENT video, one file per piece
(keyboard, mouse, controller, ... -- see PIECES). This module is what
afterglow needs to use it:

  * at save time      start_clip()   freeze the input, render the pieces in the background
  * clip written      finish_clip()  store them NEXT TO the clip (a sidecar folder; the clip itself
                                     is never touched)
  * previewer         load_sidecar(), preview_overlay_args()   -> toggle it on / off
  * editor            placements_of(), mpv_overlay_args(), save_placements()
                                     -> toggle, show / hide / move / resize pieces
  * export            export()       burn the visible pieces in at their placements

Standard library only. Every call runs `puppetry-overlay`, which prints one
JSON object; failures raise OverlayError with Puppetry's message.

    import afterglow_input_overlay as aio

    t_save = time.time()                                  # when SaveReplayBuffer is sent
    job = aio.start_clip(t_save, aio.OverlaySettings(
        pieces=["keyboard", "mouse", "controller"],
        placements=aio.resolve_placements(global_defaults, clip_type.overlay_placements)))
    ...                                                   # OBS writes the clip
    side = aio.finish_clip(job, clip_path)                # -> <clip>.input/ with manifest.json
    ...
    sc = aio.load_sidecar(clip_path)                      # None when the clip has none -> no toggle
    files, graph = aio.preview_overlay_args(sc, w, h, enabled)      # previewer: on/off only
    files, graph = aio.mpv_overlay_args(sc, placements, w, h)       # editor: after a move/resize
    ...
    aio.export(clip_path, sc, placements, out_path)       # burn in what's visible
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# The pieces Puppetry can render. "full" is keyboard + mouse in one picture;
# "keyboard" / "mouse" are the same two apart; "controller" a gamepad;
# "simple" one line of text (what's held); "movement" the mouse-movement arrow.
PIECES = ("full", "keyboard", "mouse", "controller", "simple", "movement")

# Where each piece starts out, as fractions of the video: top-left x, y and
# width (height follows the piece's aspect ratio). These are the built-in
# fallbacks; afterglow's own defaults sit on top (see resolve_placements):
#   a clip's saved placement  >  its clip type's default  >  the global default  >  this table
# The editor changes a clip's own placement; the previewer only toggles.
DEFAULT_PLACEMENT = {
    "full": {"x": 0.63, "y": 0.72, "w": 0.35},
    "keyboard": {"x": 0.02, "y": 0.76, "w": 0.34},
    "mouse": {"x": 0.88, "y": 0.66, "w": 0.1},
    "controller": {"x": 0.39, "y": 0.74, "w": 0.22},
    "simple": {"x": 0.02, "y": 0.02, "w": 0.5},
    "movement": {"x": 0.88, "y": 0.02, "w": 0.1},
}
SIDECAR_SUFFIX = ".input"
_FALLBACK_PATHS = ("/run/current-system/sw/bin/puppetry-overlay",
                   "/etc/profiles/per-user/{user}/bin/puppetry-overlay")


class OverlayError(Exception):
    pass


def find_tool() -> "str | None":
    """Path of `puppetry-overlay`: $PUPPETRY_OVERLAY, then PATH, then the
    NixOS system/user profiles (a systemd user service's PATH is minimal)."""
    env = os.environ.get("PUPPETRY_OVERLAY")
    if env and os.access(env, os.X_OK):
        return env
    found = shutil.which("puppetry-overlay")
    if found:
        return found
    user = os.environ.get("USER", "")
    for p in _FALLBACK_PATHS:
        p = p.format(user=user)
        if os.access(p, os.X_OK):
            return p
    return None


def _run(args: list, timeout: float | None = None) -> dict:
    tool = find_tool()
    if not tool:
        raise OverlayError("puppetry-overlay not found (is Puppetry installed?)")
    try:
        r = subprocess.run([tool, *[str(a) for a in args]], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise OverlayError(f"puppetry-overlay {args[0]} timed out") from None
    line = (r.stdout.strip().splitlines() or ["{}"])[-1]
    try:
        out = json.loads(line)
    except ValueError:
        raise OverlayError(f"puppetry-overlay {args[0]}: unexpected output: {r.stdout[-300:]} {r.stderr[-300:]}") from None
    if r.returncode != 0 or "error" in out:
        raise OverlayError(out.get("error") or r.stderr.strip() or f"exit {r.returncode}")
    return out


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
def status() -> dict:
    """Puppetry's overlay helper status (FORMAT.md, "overlay_status.json").
    Raises OverlayError when the helper isn't running."""
    st = _run(["status"], timeout=10)
    if time.time() - st.get("updated", 0) > 10:
        raise OverlayError("Puppetry's overlay helper isn't running (status is stale)")
    return st


def available() -> tuple:
    """(ok, reason). ok = the layered replay buffer is on and recording."""
    try:
        st = status()
    except OverlayError as e:
        return False, str(e)
    rp = st.get("replay", {})
    if not rp.get("enabled"):
        return False, "\"Layered Replay Buffer\" is off in Puppetry (Input Visualizer page)"
    if not st.get("daemon_connected"):
        return False, "Puppetry's daemon isn't running, so no input is being recorded"
    return True, f"recording {rp.get('length_s', 0):g} s of input ({rp.get('length_source')})"


# ---------------------------------------------------------------------------
# Capture: one clip
# ---------------------------------------------------------------------------
def resolve_placements(global_defaults: dict | None = None, clip_type_defaults: dict | None = None) -> dict:
    """The starting placement of every piece for a new clip:
    {piece: {"x", "y", "w", "visible"}}. Per piece, the clip type's default
    wins when it has one for that piece, else the global default, else the
    built-in DEFAULT_PLACEMENT. (A clip type can override just one piece --
    e.g. move only the controller -- and inherit the rest.)"""
    out = {}
    for piece in PIECES:
        base = dict(DEFAULT_PLACEMENT[piece])
        base["visible"] = True
        for layer in (global_defaults or {}, clip_type_defaults or {}):
            if piece in layer and layer[piece]:
                base.update({k: v for k, v in layer[piece].items() if k in ("x", "y", "w", "visible")})
        out[piece] = base
    return out


@dataclass
class OverlaySettings:
    """What afterglow would store per clip type."""
    pieces: list = field(default_factory=lambda: ["full"])   # which of PIECES to make
    # starting placements for this clip type's clips -- pass
    # resolve_placements(global_defaults, clip_type_defaults); None = built-in
    placements: dict | None = None
    offset_ms: float = 0.0             # + shows inputs later, - earlier (calibration)
    fps: float = 60.0                  # match the recording's fps
    visible_by_default: bool = True    # the overlay starts shown in the previewer/editor


@dataclass
class ClipJob:
    t_save: float
    settings: OverlaySettings
    buffer_copy: Path = Path()
    overlay_start: float | None = None
    rendered: dict = field(default_factory=dict)      # piece -> Path
    errors: dict = field(default_factory=dict)        # piece -> message
    workdir: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="afterglow_input_")))
    threads: list = field(default_factory=list)

    def wait(self, timeout: float | None = None) -> None:
        for t in self.threads:
            t.join(timeout)

    def cleanup(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)


def freeze_input(dest) -> Path:
    """Copy the live input buffer right now (it keeps rolling forward)."""
    _run(["snapshot", dest], timeout=10)
    return Path(dest)


def start_clip(t_save: float, settings: OverlaySettings | None = None, replay_length_s: float | None = None,
               lead_s: float = 2.0) -> ClipJob:
    """Call the moment OBS is told to save. Freezes the input and starts
    rendering every requested piece in the background, covering the replay
    length plus `lead_s` before it (so it certainly reaches the clip's first
    frame); finish_clip() cuts each one to the clip exactly."""
    settings = settings or OverlaySettings()
    for p in settings.pieces:
        if p not in PIECES:
            raise OverlayError(f"unknown piece {p!r} (one of {', '.join(PIECES)})")
    job = ClipJob(t_save, settings)
    job.buffer_copy = freeze_input(job.workdir / "inputs.jsonl")
    if replay_length_s is None:
        try:
            replay_length_s = float(status()["replay"]["length_s"])
        except (OverlayError, KeyError, ValueError):
            replay_length_s = 120.0            # unknown: cover generously
    job.overlay_start = t_save - replay_length_s - lead_s

    def work(piece):
        out = job.workdir / f"{piece}.mov"
        try:
            _run(["render", "--buffer", job.buffer_copy, "--mode", piece, "--offset-ms", settings.offset_ms,
                  "--fps", f"{settings.fps:g}", "--start", f"{job.overlay_start:.6f}",
                  "--end", f"{t_save + 0.5:.6f}", out])
            job.rendered[piece] = out
        except OverlayError as e:
            job.errors[piece] = str(e)
    for piece in settings.pieces:
        t = threading.Thread(target=work, args=(piece,), daemon=True)
        t.start()
        job.threads.append(t)
    return job


def sidecar_dir(clip) -> Path:
    clip = Path(clip)
    return clip.with_name(clip.name + SIDECAR_SUFFIX)


def finish_clip(job: ClipJob, clip, clip_end: float | None = None, dest=None) -> dict:
    """Once OBS has written `clip`: cut each rendered piece to line up with
    it frame for frame (a stream copy -- fast) and store everything in the
    sidecar folder `<clip>.input/` (or `dest`): the pieces as transparent
    .mov files, the frozen input (inputs.jsonl, so pieces can be re-rendered
    later with other settings), and manifest.json. Returns the manifest.
    The clip itself is not modified. Raises OverlayError if nothing could be
    made; pieces that failed are listed under "errors"."""
    job.wait()
    clip = Path(clip)
    clip_end = job.t_save if clip_end is None else clip_end
    dest = Path(dest) if dest else sidecar_dir(clip)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(job.buffer_copy, dest / "inputs.jsonl")
    manifest = {"format": "puppetry-overlay-sidecar", "version": 1, "clip": clip.name, "clip_end": clip_end,
                "offset_ms": job.settings.offset_ms, "visible_by_default": job.settings.visible_by_default,
                "inputs": "inputs.jsonl", "pieces": {}, "errors": dict(job.errors)}
    for piece, path in job.rendered.items():
        out = dest / f"{piece}.mov"
        try:
            _run(["align", "--overlay", path, "--overlay-start", f"{job.overlay_start:.6f}", "--clip", clip,
                  "--clip-end", f"{clip_end:.6f}", out])
        except OverlayError as e:
            manifest["errors"][piece] = str(e)
            continue
        w, h = _probe_size(out)
        start = (job.settings.placements or {}).get(piece) or dict(DEFAULT_PLACEMENT[piece], visible=True)
        manifest["pieces"][piece] = {"file": out.name, "width": w, "height": h, "placement": dict(start)}
    if not manifest["pieces"]:
        raise OverlayError("no overlay piece could be made: " + "; ".join(f"{k}: {v}" for k, v in
                                                                          manifest["errors"].items()))
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2))
    manifest["dir"] = str(dest)
    return manifest


def _probe_size(path) -> tuple:
    exe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([exe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of",
                        "json", str(path)], capture_output=True, text=True)
    s = json.loads(r.stdout or "{}").get("streams", [{}])[0]
    return int(s.get("width", 0)), int(s.get("height", 0))


# ---------------------------------------------------------------------------
# Previewer / editor
# ---------------------------------------------------------------------------
def load_sidecar(clip) -> "dict | None":
    """The clip's overlay manifest (with "dir" added), or None when the clip
    has no overlay -- the previewer shows its toggle only when this isn't None."""
    d = sidecar_dir(clip)
    try:
        m = json.loads((d / "manifest.json").read_text())
    except (OSError, ValueError):
        return None
    m["dir"] = str(d)
    m["pieces"] = {k: v for k, v in m.get("pieces", {}).items() if (d / v["file"]).exists()}
    return m if m["pieces"] else None


def placements_of(sidecar: dict) -> dict:
    """Editable copy of the clip's placements: {piece: {"x", "y", "w", "visible"}}.
    Used by the editor (which may change and save them) and the previewer
    (which only reads them -- it just toggles the whole overlay)."""
    out = {}
    for piece, info in sidecar["pieces"].items():
        p = dict(info.get("placement") or DEFAULT_PLACEMENT[piece])
        p.setdefault("visible", True)
        out[piece] = p
    return out


def save_placements(sidecar: dict, placements: dict) -> None:
    """Editor only: store this clip's placements in its manifest."""
    d = Path(sidecar["dir"])
    m = json.loads((d / "manifest.json").read_text())
    for piece, p in placements.items():
        if piece in m["pieces"]:
            m["pieces"][piece]["placement"] = {k: p[k] for k in ("x", "y", "w", "visible") if k in p}
    (d / "manifest.json").write_text(json.dumps(m, indent=2))


def piece_rect(sidecar: dict, piece: str, placement: dict, video_w: int, video_h: int) -> tuple:
    """(x, y, w, h) in video pixels -- for drawing drag/resize handles."""
    info = sidecar["pieces"][piece]
    w = max(2, int(video_w * placement["w"]) // 2 * 2)
    h = max(2, int(w * info["height"] / max(1, info["width"])) // 2 * 2)
    return int(video_w * placement["x"]), int(video_h * placement["y"]), w, h


def preview_overlay_args(sidecar: dict, video_w: int, video_h: int, enabled: bool) -> tuple:
    """Previewer: the whole overlay on/off, at the clip's saved placements
    (the previewer never moves pieces). Same return as mpv_overlay_args."""
    return mpv_overlay_args(sidecar, placements_of(sidecar), video_w, video_h, enabled)


def mpv_overlay_args(sidecar: dict, placements: dict, video_w: int, video_h: int, enabled: bool = True) -> tuple:
    """(external_files, lavfi_complex) for mpv: set `external-files` to the
    list (once, when the clip loads) and `lavfi-complex` to the string --
    again whenever a piece moves, resizes or is toggled ("" = no overlay:
    set lavfi-complex to "[vid1] null [vo]"). Pieces are transparent qtrle,
    which ffmpeg/mpv decode with their alpha."""
    d = Path(sidecar["dir"])
    order = [p for p in PIECES if p in sidecar["pieces"]]
    files = [str(d / sidecar["pieces"][p]["file"]) for p in order]
    shown = [(i, p) for i, p in enumerate(order) if enabled and placements.get(p, {}).get("visible", True)]
    if not shown:
        return files, "[vid1] null [vo]"
    chain, last = [], "vid1"
    for n, (i, p) in enumerate(shown, start=1):
        x, y, w, h = piece_rect(sidecar, p, placements[p], video_w, video_h)
        out = "vo" if n == len(shown) else f"o{n}"
        chain.append(f"[vid{i + 2}] scale={w}:{h} [s{n}]; [{last}][s{n}] overlay=x={x}:y={y}:format=auto:eof_action=pass [{out}]")
        last = out
    return files, "; ".join(chain)


def export(clip, sidecar: dict, placements: dict, out, crf: int = 18) -> dict:
    """Burn the visible pieces onto a copy of the clip at their placements."""
    d = Path(sidecar["dir"])
    args = ["layer", "--clip", clip, "--crf", crf]
    for piece in PIECES:
        p = placements.get(piece)
        if piece in sidecar["pieces"] and p and p.get("visible", True):
            args += ["--piece", f"{d / sidecar['pieces'][piece]['file']}:{p['x']}:{p['y']}:{p['w']}"]
    if "--piece" not in args:
        shutil.copyfile(clip, out)
        return {"out": str(out), "pieces": 0}
    return _run(args + [out])


def rerender(clip, sidecar: dict, piece: str, fps: float = 60.0) -> dict:
    """Re-make one piece from the stored inputs (after changing Puppetry's
    look, or adding a piece that wasn't made at save time)."""
    if piece not in PIECES:
        raise OverlayError(f"unknown piece {piece!r}")
    d = Path(sidecar["dir"])
    clip_end = float(sidecar["clip_end"])
    dur = _probe_duration(clip)
    out = d / f"{piece}.mov"
    tmp = d / f".{piece}.tmp.mov"
    _run(["render", "--buffer", d / sidecar.get("inputs", "inputs.jsonl"), "--mode", piece,
          "--offset-ms", sidecar.get("offset_ms", 0), "--fps", f"{fps:g}",
          "--start", f"{clip_end - dur:.6f}", "--end", f"{clip_end:.6f}", tmp])
    tmp.replace(out)
    m = json.loads((d / "manifest.json").read_text())
    w, h = _probe_size(out)
    old = m["pieces"].get(piece, {})
    m["pieces"][piece] = {"file": out.name, "width": w, "height": h,
                          "placement": old.get("placement", dict(DEFAULT_PLACEMENT[piece]))}
    (d / "manifest.json").write_text(json.dumps(m, indent=2))
    return m["pieces"][piece]


def _probe_duration(path) -> float:
    exe = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    return float(json.loads(r.stdout or "{}").get("format", {}).get("duration", 0))


# ---------------------------------------------------------------------------
# One pass, no sidecar (the simplest possible use)
# ---------------------------------------------------------------------------
def composite(clip, out, *, clip_end: float, buffer=None, mode: str = "full", position: str = "bottom-right",
              scale_frac: float = 0.35, margin: int = 24, offset_ms: float = 0.0, crf: int = 18) -> dict:
    args = ["composite", "--clip", clip, "--clip-end", f"{clip_end:.6f}", "--mode", mode, "--position", position,
            "--scale-frac", f"{scale_frac:g}", "--margin", margin, "--offset-ms", offset_ms, "--crf", crf, out]
    if buffer:
        args[1:1] = ["--buffer", buffer]
    return _run(args)
