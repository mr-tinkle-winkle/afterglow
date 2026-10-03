"""
Moving files to the system trash (the freedesktop.org Trash spec), never erasing them.

``gio trash`` first (GLib's implementation: handles per-volume ``.Trash-$UID`` directories and is
what Dolphin / Nautilus read).  Without gio, the spec directly: the home trash
(``$XDG_DATA_HOME/Trash``) with a ``.trashinfo`` record, so the file manager's trash can still
restore it to where it was.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import quote


class TrashError(OSError):
    pass


def home_trash() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "Trash"


def _spec_trash(path: Path) -> Path:
    trash = home_trash()
    files, info = trash / "files", trash / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    name = path.name
    stem, suffix = (name, "") if path.is_dir() else (path.stem, path.suffix)
    n = 1
    while True:
        cand = name if n == 1 else f"{stem}.{n}{suffix}"
        # O_EXCL on the .trashinfo reserves the name atomically, as the spec asks
        try:
            fd = os.open(info / f"{cand}.trashinfo", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            n += 1
            continue
        if (files / cand).exists():
            os.close(fd)
            os.unlink(info / f"{cand}.trashinfo")
            n += 1
            continue
        break
    record = ("[Trash Info]\n"
              f"Path={quote(str(path.resolve()))}\n"
              f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
    with os.fdopen(fd, "w") as f:
        f.write(record)
    dest = files / cand
    try:
        shutil.move(str(path), str(dest))
    except OSError:
        try:
            os.unlink(info / f"{cand}.trashinfo")
        except OSError:
            pass
        raise
    return dest


def trash(path: "Path | str", use_gio: "bool | None" = None) -> None:
    """Move ``path`` (file or directory) to the trash.  Raises TrashError if it couldn't be."""
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    if use_gio is None:
        use_gio = shutil.which("gio") is not None and not os.environ.get("AFTERGLOW_TRASH_NO_GIO")
    if use_gio:
        r = subprocess.run(["gio", "trash", "--", str(path)], capture_output=True, text=True)
        if r.returncode == 0 and not path.exists():
            return
    try:
        _spec_trash(path)
    except OSError as e:
        raise TrashError(f"could not move {path} to the trash: {e}") from e
