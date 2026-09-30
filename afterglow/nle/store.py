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
        shutil.copy2(live, backup)
        stable_for_live = backup     # unedited clip: the backup IS the current content
    for s in project.all_segments():
        for p in s.parts:
            if p.source and os.path.abspath(p.source) == os.path.abspath(live_str):
                if stable_for_live is None:
                    stable_for_live = _snapshot_path(live)
                    shutil.copy2(live, stable_for_live)
                p.source = str(stable_for_live)
    return str(backup)


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
    for seg in project.all_segments():
        for part in seg.parts:
            if part.source == old_s:
                part.source = new_s
                changed = True
    if project.output_path == old_s:
        project.output_path = new_s
        changed = True
    if changed:
        save_project(project, path)
