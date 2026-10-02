"""
Copying big video files fast.

clone_or_copy(src, dst): on copy-on-write filesystems (btrfs, XFS with
reflink, bcachefs, ZFS 2.2+) the copy is a reflink -- instant, no extra
disk space until one side changes, and still a fully independent file.
Anywhere else it's an ordinary copy (shutil.copy2). Used for the edit
backups the Editor and Save Trim make before the first edit.
"""
from __future__ import annotations

import fcntl
import os
import shutil

FICLONE = 0x40049409


def clone_or_copy(src, dst) -> str:
    """Returns "clone" or "copy" (how it was done)."""
    src, dst = os.fspath(src), os.fspath(dst)
    try:
        with open(src, "rb") as fs, open(dst, "wb") as fd:
            fcntl.ioctl(fd.fileno(), FICLONE, fs.fileno())
        shutil.copystat(src, dst)
        return "clone"
    except OSError:
        try:
            os.remove(dst)
        except OSError:
            pass
    shutil.copy2(src, dst)
    return "copy"
