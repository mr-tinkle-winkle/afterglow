"""
The card's Copy quick action and its right-click menu: four ways to put a clip on the clipboard.

  plain        the clip's own file, as it is
  mp4          the same bytes under a .mp4 name (a rename via a temp copy, no re-encode) -- the
               default (Settings "Auto Copy as MP4", on by default), what a left click does
  overlay      the clip with its input overlay burned in (rendered with the previewer's own
               overlay graph, overlay_support.burn_overlay), same container as the clip
  overlay_mp4  the same, rendered straight into a real .mp4 (H.264 + AAC when needed)

The overlay variants render in the background (a few seconds; the card shows the progress on its
badge) and are cached under ~/.cache/afterglow/copies/<video id>/, re-rendered only when the clip or
its overlay placements changed since.  The clipboard gets the file the same way Copy always has
(a file URL), so pasting into a chat app or file manager attaches it.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QUrl, Signal
from PySide6.QtWidgets import QApplication

from .. import config as config_module, overlay_support

MODES = (
    ("plain", "Copy"),
    ("mp4", "Copy as .mp4"),
    ("overlay", "Copy with input overlay"),
    ("overlay_mp4", "Copy with input overlay as .mp4"),
)
CACHE_DIR = Path.home() / ".cache" / "afterglow" / "copies"


def default_mode() -> str:
    return "mp4" if config_module.load_readonly().auto_copy_as_mp4 else "plain"


def needs_overlay(mode: str) -> bool:
    return mode in ("overlay", "overlay_mp4")


def set_clipboard_files(paths) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
    QApplication.clipboard().setMimeData(mime)


def renamed_mp4(path: Path) -> Path:
    """A .mp4-named copy of the same bytes (no remux), in the temp dir -- the original is untouched."""
    if path.suffix.lower() == ".mp4":
        return path
    temp_path = Path(tempfile.gettempdir()) / (path.stem + ".mp4")
    shutil.copyfile(path, temp_path)
    return temp_path


def overlay_copy_path(video, mp4: bool) -> Path:
    src = Path(video.path)
    return CACHE_DIR / str(video.id) / (src.stem + (".mp4" if mp4 else src.suffix))


def _cache_fresh(dst: Path, src: Path) -> bool:
    if not dst.exists():
        return False
    newest = src.stat().st_mtime
    manifest = overlay_support.sidecar_dir(src) / "manifest.json"
    if manifest.exists():
        newest = max(newest, manifest.stat().st_mtime)
    return dst.stat().st_mtime >= newest


class _Bridge(QObject):
    progress = Signal(int, float)          # video id, 0..1
    done = Signal(int, str, str)           # video id, path ("" on failure), error


_bridge: "_Bridge | None" = None
_running: "dict[int, threading.Event]" = {}


def bridge() -> _Bridge:
    global _bridge
    if _bridge is None:
        _bridge = _Bridge()
        _bridge.done.connect(_on_done)      # first: the clipboard, before any card reacts
    return _bridge


def is_rendering(video_id: int) -> bool:
    return video_id in _running


def copy_video(video, mode: str) -> "Path | None":
    """Put the clip on the clipboard in the given mode.  Plain / mp4: done now, returns the path.
    Overlay modes: starts (or reuses) a background render and returns None; bridge().done fires when
    the file is on the clipboard (or failed).  Raises FileNotFoundError / OverlayError up front."""
    src = Path(video.path)
    if not src.exists():
        raise FileNotFoundError(str(src))
    if mode == "plain":
        set_clipboard_files([src])
        return src
    if mode == "mp4":
        out = renamed_mp4(src)
        set_clipboard_files([out])
        return out
    if not overlay_support.has_sidecar(src):
        raise overlay_support.OverlayError("this clip has no input overlay")
    mp4 = mode == "overlay_mp4"
    dst = overlay_copy_path(video, mp4)
    b = bridge()
    if _cache_fresh(dst, src):
        set_clipboard_files([dst])
        b.done.emit(video.id, str(dst), "")
        return None
    if video.id in _running:
        return None                         # already rendering: its result lands on the clipboard
    cancel = threading.Event()
    _running[video.id] = cancel
    vid = video.id

    def work():
        try:
            overlay_support.burn_overlay(src, dst, mp4=mp4, progress=lambda f: b.progress.emit(vid, f),
                                         cancel=cancel)
            b.done.emit(vid, str(dst), "")
        except Exception as e:  # noqa: BLE001 -- reported on the card, never raised in a thread
            b.done.emit(vid, "", str(e))

    threading.Thread(target=work, name="afterglow-copy-overlay", daemon=True).start()
    return None


def _on_done(video_id: int, path: str, error: str) -> None:
    _running.pop(video_id, None)
    if path:
        set_clipboard_files([path])
