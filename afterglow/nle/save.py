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

from .. import library
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
    ops.add_media(project, info, start=0.0)
    return project


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
    return library.record_advanced_edit(video.id, backup)


def save_import(project: Project, source_path: str, progress=None, cancel=None, **export_opts) -> Path:
    out = Path(project.output_path or store.edited_copy_path(source_path))
    export(project, str(out), progress=progress, cancel=cancel, **export_opts)
    store.save_project(project, store.project_path_for_import(source_path))
    return out
