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
    """Convenience: load a bundled asset directly as a QIcon."""
    from PySide6.QtGui import QIcon

    with resource_path(name) as p:
        return QIcon(str(p))


def resource_qpixmap(name: str):
    """Convenience: load a bundled asset directly as a QPixmap."""
    from PySide6.QtGui import QPixmap

    with resource_path(name) as p:
        return QPixmap(str(p))
