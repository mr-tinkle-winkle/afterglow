"""
Saving from the Advanced Editor.

- Library clip: overwrite the clip with the render, keeping the original
  in "Edit Backups" so Undo Edits works (same backup the quick trim
  uses). The project is stored so the edit can be reopened later; it
  renders from the stable sources (see store.stabilize_sources), never
  from the file being overwritten.
- Imported file: write "<name>-edited.<ext>" next to it; the original is
  never touched.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import logging
import shutil

from .. import library
from .. import overlay_support
from . import store
from .model import Project
from .render import export


def open_for_library_video(video) -> Project:
    """The clip's saved project, or a new one containing just the clip."""
    project = store.load_project(store.project_path_for_video(video.id))
    if project is not None and not store.missing_sources(project):
        return project
    return new_project_for_file(str(video.path), library_video_id=video.id, output_path=str(video.path))


def open_for_import(path: str) -> Project:
    project = store.load_project(store.project_path_for_import(path))
    if project is not None and not store.missing_sources(project):
        return project
    return new_project_for_file(path, output_path=str(store.edited_copy_path(path)))


def new_project_for_file(path: str, library_video_id: "int | None" = None, output_path: str = "") -> Project:
    from . import media, ops
    from .model import default_project
    info = media.probe(path)
    width = info["width"] or 1920
    height = info["height"] or 1080
    project = default_project(width - width % 2, height - height % 2, info["fps"] or 60.0)
    project.library_video_id = library_video_id
    project.output_path = output_path
    seg = ops.add_media(project, info, start=0.0)
    attach_overlay(project, seg, path)
    return project


def attach_overlay(project: Project, seg, clip_path: str) -> bool:
    """Input overlay: attach the clip's `<clip>.input/` pieces to its video
    part (they follow the clip like its audio does). No-op without a sidecar."""
    from .model import OverlayPiece
    sidecar = overlay_support.load_sidecar(clip_path)
    if sidecar is None:
        return False
    placements = overlay_support.placements_of(sidecar)
    part = next((p for p in seg.parts if p.has_video), None)
    if part is None:
        return False
    d = Path(sidecar["dir"])
    for name in overlay_support.aio._order(sidecar["pieces"]):
        info = sidecar["pieces"].get(name)
        if info is None:
            continue
        pl = placements[name]
        part.overlays.append(OverlayPiece(
            name=name, source=str(d / info["file"]), x=pl["x"], y=pl["y"], w=pl["w"],
            rotation=pl.get("rotation", 0.0), visible=bool(pl.get("visible", True)),
            width=int(info.get("width", 0)), height=int(info.get("height", 0))))
    project.overlay_visible_by_default = bool(sidecar.get("visible_by_default", True))
    return True


def save_library_clip(project: Project, progress: "Callable[[float], None] | None" = None,
                      cancel: "threading.Event | None" = None, **export_opts):
    """Render over the library clip. Returns the updated library.Video."""
    video = library.get_video(project.library_video_id)
    backup = store.stabilize_sources(project, video.path, video.has_edit, video.backup_path)
    # Persist the stabilized project BEFORE rendering: if the render fails,
    # the project already points at the stable sources (the live file is
    # untouched on failure because export writes a temp file first).
    store.save_project(project, store.project_path_for_video(video.id))
    export(project, str(video.path), progress=progress, cancel=cancel, **export_opts)
    # The video was rewritten, so the clip's old overlay sidecar no longer
    # matches it: its pieces are safe (stabilize_sources copied them beside
    # the backup, and the project points there), so replace it with the new
    # output's own sidecar. Never burned in -- see overlay_export.py.
    _write_output_sidecar(project, video.path)
    return library.record_advanced_edit(video.id, backup)


def _write_output_sidecar(project: Project, out_path) -> None:
    """Write `<out_path>.input/` (or remove a stale one). A failure costs the
    overlay, never the export: the video is already written."""
    from .overlay_export import write_sidecar
    try:
        write_sidecar(project, out_path)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("afterglow").warning(f"could not write the overlay sidecar for {out_path}: {e}")
        overlay_support.delete_sidecar(out_path)


def save_import(project: Project, source_path: str, progress=None, cancel=None, **export_opts) -> Path:
    out = Path(project.output_path or store.edited_copy_path(source_path))
    export(project, str(out), progress=progress, cancel=cancel, **export_opts)
    _write_output_sidecar(project, out)
    store.save_project(project, store.project_path_for_import(source_path))
    return out


def save_library_separately(project: Project, progress=None, cancel=None, **export_opts):
    """"Save Separately": render into a NEW clip next to the original
    ("<title> (edited).mp4"), added to the library as its own video (same
    filters and favorite as the original). The original clip, its file and
    its edit backups are untouched. Returns the new library.Video."""
    video = library.get_video(project.library_video_id)
    src = Path(video.path)
    out = library._unique_path(src.parent, library._sanitize_filename_stem(f"{video.title} (edited)"), ".mp4")
    export(project, str(out), progress=progress, cancel=cancel, **export_opts)
    _write_output_sidecar(project, out)
    new = library.add_video(out, title=f"{video.title} (edited)")
    for tag in video.tags:
        library.add_tag_to_video(new.id, tag)
    if video.favorite:
        library.set_favorite(new.id, True)
    library.mark_as_edited(new.id)
    return library.get_video(new.id)
