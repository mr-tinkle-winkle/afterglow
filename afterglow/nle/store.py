"""
Saving/loading Advanced Editor projects, and keeping them consistent
with the library's edit backups.

Where projects live: CONFIG_DIR/projects/
- video_<id>.json         for a library clip
- import_<sha1>.json      for a file opened with the Import button

Why the project must never reference the clip's own file: saving
overwrites that file with the render. So before the first overwrite:
- the clip's original goes to "Edit Backups/<stem>.orig.<ext>" (the same
  backup the quick trim uses, so Undo Edits works for both), and
- every part that pointed at the live clip is re-pointed at a stable
  copy: the .orig backup if the clip was unedited (identical content),
  or a snapshot "Edit Backups/<stem>.src<N>.<ext>" if the clip had
  already been trimmed (so the project starts from what was on screen).
Re-saving later re-renders from those stable sources, so quality never
degrades across repeated edits.

A project stops describing the file when the file changes some other
way. Undo Edits (restores the original), Clear Edit Backup (removes the
source the project depends on) and a quick trim (edits the file
directly) all call discard_for_video().
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from .. import overlay_support
from ..fileutil import clone_or_copy
from ..config import CONFIG_DIR
from ..editor import EDIT_BACKUPS_DIRNAME, backup_path_for
from .model import Project

PROJECTS_DIR = CONFIG_DIR / "projects"


def project_path_for_video(video_id: int) -> Path:
    return PROJECTS_DIR / f"video_{video_id}.json"


def project_path_for_import(source_path: "str | Path") -> Path:
    digest = hashlib.sha1(str(Path(source_path).resolve()).encode()).hexdigest()[:16]
    return PROJECTS_DIR / f"import_{digest}.json"


def edited_copy_path(source_path: "str | Path") -> Path:
    """Import button: saving writes "<name>-edited.<ext>" next to the original."""
    p = Path(source_path)
    return p.with_name(f"{p.stem}-edited{p.suffix}")


def saved_state_path(path: Path) -> Path:
    """Where the project as of its last Save (render) is kept, next to the
    autosaved project -- what "Discard Changes" goes back to."""
    path = Path(path)
    return path.with_name(path.stem + ".saved.json")


def save_project(project: Project, path: Path) -> None:
    """Atomic write: a crash mid-save never leaves a half-written project."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(project.to_dict(), indent=1))
    os.replace(tmp, path)


def load_project(path: Path) -> "Project | None":
    if not path.exists():
        return None
    return Project.from_dict(json.loads(path.read_text()))


def missing_sources(project: Project) -> list[str]:
    """Source files the project needs that no longer exist."""
    out = []
    for s in project.all_segments():
        for p in s.parts:
            if p.source and not os.path.exists(p.source) and p.source not in out:
                out.append(p.source)
    return out


def _snapshot_path(live: Path) -> Path:
    backups = live.parent / EDIT_BACKUPS_DIRNAME
    backups.mkdir(exist_ok=True)
    n = 1
    while True:
        cand = backups / f"{live.stem}.src{n}{live.suffix}"
        if not cand.exists():
            return cand
        n += 1


def stabilize_sources(project: Project, live_path: "str | Path", has_edit: bool,
                      backup_path: "str | None") -> "str":
    """Call before overwriting live_path. Ensures the original backup
    exists and no part references live_path. Returns the backup path to
    store in the DB (unchanged if one already existed)."""
    live = Path(live_path)
    live_str = str(live)
    if has_edit and backup_path and Path(backup_path).exists():
        backup = Path(backup_path)
        stable_for_live = None       # created lazily: only if a part actually needs it
    else:
        backup = backup_path_for(live)
        clone_or_copy(live, backup)
        stable_for_live = backup     # unedited clip: the backup IS the current content
    for s in project.all_segments():
        for p in s.parts:
            if p.source and os.path.abspath(p.source) == os.path.abspath(live_str):
                if stable_for_live is None:
                    stable_for_live = _snapshot_path(live)
                    clone_or_copy(live, stable_for_live)
                p.source = str(stable_for_live)
    _stabilize_overlays(project, live, stable_for_live)
    return str(backup)


def _stabilize_overlays(project: Project, live: Path, stable_clip: "Path | None") -> None:
    """The overlay pieces of the clip live in `<live>.input/`, which the
    save replaces with the output's own sidecar. Copy that folder beside the
    stable source the parts now point at (`<stable>.input/`) and re-point the
    attached pieces there. (For an unedited clip that IS the Edit Backup's
    sidecar -- the one Undo Edits restores.)"""
    live_dir = overlay_support.sidecar_dir(live)
    if not live_dir.is_dir():
        return
    users = [o for s in project.all_segments() for p in s.parts for o in p.overlays
             if o.source and Path(o.source).parent == live_dir]
    detached = [p for s in project.all_segments() if s.overlay_piece for p in s.parts
                if p.source and Path(p.source).parent == live_dir]
    if not users and not detached:
        return
    if stable_clip is None:
        stable_clip = _snapshot_path(live)           # no part used the live clip itself: still needs a home
    target = overlay_support.sidecar_dir(stable_clip)
    if not target.exists():
        shutil.copytree(live_dir, target)
    for o in users:
        o.source = str(target / Path(o.source).name)
    for p in detached:
        p.source = str(target / Path(p.source).name)


def discard_for_video(video_id: int, live_path: "str | Path | None" = None) -> None:
    """Forget a clip's project (and its .src snapshots) -- see module
    docstring for when."""
    path = project_path_for_video(video_id)
    project = None
    try:
        project = load_project(path)
    except Exception:
        project = None
    if path.exists():
        path.unlink()
    saved = saved_state_path(path)
    if saved.exists():
        saved.unlink()
    if project is not None and live_path is not None:
        backups = Path(live_path).parent / EDIT_BACKUPS_DIRNAME
        for s in project.all_segments():
            for p in s.parts:
                src = Path(p.source) if p.source else None
                if src and src.parent == backups and ".src" in src.stem and src.exists():
                    src.unlink()
                    overlay_support.clear_backup_sidecar(src)      # its `.srcN.input` snapshot


def repoint_video_sources(video_id: int, old_path: "str | Path", new_path: "str | Path") -> None:
    """The clip's file was renamed (renaming a video renames its file):
    point the stored project at the new name, so unsaved/unrendered
    edits that still read from the live file aren't lost as "missing"."""
    path = project_path_for_video(video_id)
    saved = saved_state_path(path)
    if saved.exists():
        _repoint_file(saved, str(old_path), str(new_path))
    _repoint_file(path, str(old_path), str(new_path))


def _repoint_file(path: Path, old_s: str, new_s: str) -> None:
    project = load_project(path)
    if project is None:
        return
    changed = False
    old_dir = str(overlay_support.sidecar_dir(old_s))
    new_dir = str(overlay_support.sidecar_dir(new_s))
    for seg in project.all_segments():
        for part in seg.parts:
            if part.source == old_s:
                part.source = new_s
                changed = True
            elif part.source and os.path.dirname(part.source) == old_dir:      # a detached overlay element
                part.source = os.path.join(new_dir, os.path.basename(part.source))
                changed = True
            for o in part.overlays:
                if o.source and os.path.dirname(o.source) == old_dir:        # an attached piece
                    o.source = os.path.join(new_dir, os.path.basename(o.source))
                    changed = True
    if project.output_path == old_s:
        project.output_path = new_s
        changed = True
    if changed:
        save_project(project, path)
