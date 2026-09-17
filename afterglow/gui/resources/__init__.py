"""
Resolves the icon/texture assets in this directory (afterglow/gui/resources/)
regardless of how afterglow is running -- installed as a wheel (Nix store,
pip install, etc, where these are shipped as package data per pyproject.toml's
[tool.setuptools.package-data]) or run in-place from a source checkout.

importlib.resources.files() handles both cases correctly (including from
inside a zip/wheel, where a plain filesystem Path wouldn't work), so it's
used here rather than a Path(__file__).parent lookup.
"""
from __future__ import annotations

from importlib.resources import as_file, files


def resource_path(name: str):
    """Return a real filesystem path to the named asset (e.g. "library.png").

    Returned as a context manager via as_file() since importlib.resources
    can't always guarantee a real on-disk path (e.g. zipped installs) --
    callers that just need a path for QIcon/QPixmap should use:

        with resource_path("library.png") as p:
            icon = QIcon(str(p))
    """
    return as_file(files(__package__).joinpath(name))


def resource_qicon(name: str):
    """Convenience: load a bundled asset directly as a QIcon. Cached --
    see resource_qpixmap's own comment for why."""
    from PySide6.QtGui import QIcon

    if name not in _icon_cache:
        with resource_path(name) as p:
            _icon_cache[name] = QIcon(str(p))
    return _icon_cache[name]


def resource_qpixmap(name: str):
    """Convenience: load a bundled asset directly as a QPixmap.

    CACHED by filename, module-level, for the lifetime of the process --
    this used to re-read the file from disk AND re-decode the full-
    resolution PNG on EVERY call, with no caching at all. Several call
    sites (a filter icon or a CustomCheckBox's checkmark, for example)
    load the SAME file fresh for every single VideoCard/checkbox
    instance -- for a Library with many videos, that's potentially
    hundreds of redundant full-resolution decodes of the exact same
    bytes on every refresh, which is real, entirely avoidable work.
    Safe to share one QPixmap across every caller here since nothing in
    this codebase mutates a pixmap it got back from this function --
    everything downstream (set_icon_pixmap, hue_shift_pixmap_cached,
    tint_pixmap_cached, etc.) already treats these as read-only source
    images and returns a NEW pixmap/image for anything that needs to
    look different."""
    from PySide6.QtGui import QPixmap

    if name not in _pixmap_cache:
        with resource_path(name) as p:
            _pixmap_cache[name] = QPixmap(str(p))
    return _pixmap_cache[name]


_pixmap_cache: dict[str, "QPixmap"] = {}
_icon_cache: dict[str, "QIcon"] = {}
