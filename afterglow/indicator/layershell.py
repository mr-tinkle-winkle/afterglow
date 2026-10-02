"""
Layer-shell for the overlay window (Wayland).

A normal Qt window can't place itself on Wayland and won't sit above a fullscreen game.
The indicator window has to be a *layer-shell surface* on the `overlay` layer: no keyboard
interactivity, exclusive zone 0 (doesn't push windows around), empty input region (clicks
pass through).  KDE's implementation is LayerShellQt, which has a C++ API only, so
`native/afterglow_layershell.cpp` is a ~40-line shim (built in the flake) exposing a plain C
function, loaded here with ctypes:

    int afterglow_layershell_configure(void *qwindow, int anchors,
                                       int top, int right, int bottom, int left)

Rules (see HANDOFF.md):
  * the shim and PySide6 must use the SAME Qt (the plugin only loads into the Qt it was
    built against) -- the flake builds both from one nixpkgs;
  * ``configure`` must run after the QWindow exists (``widget.winId()``) but before the first
    ``show()``;
  * ``QT_WAYLAND_SHELL_INTEGRATION=layer-shell`` makes EVERY window of a process a layer
    surface, so it is set only inside the helper process (`__main__.py`), never in the daemon
    or the GUI -- ``enable_in_this_process()`` does that, and only when the shim is loadable.

The library is found through ``AFTERGLOW_LAYERSHELL_LIB`` (set by the flake's wrapper) or
next to the package (a developer build).  No library -> ``available()`` is False and the
helper falls back to a plain window (X11) or skips the visual (Wayland; sounds still play).
"""
from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

logger = logging.getLogger("afterglow.indicator.layershell")

ANCHOR_TOP, ANCHOR_BOTTOM, ANCHOR_LEFT, ANCHOR_RIGHT = 1, 2, 4, 8    # LayerShellQt::Window::Anchor
_BITS = {"top": ANCHOR_TOP, "bottom": ANCHOR_BOTTOM, "left": ANCHOR_LEFT, "right": ANCHOR_RIGHT}
LIB_NAME = "libafterglow_layershell.so"

_lib = None
_tried = False


def anchor_bits(edges) -> int:
    n = 0
    for e in edges:
        n |= _BITS[e]
    return n


def _candidates() -> "list[Path]":
    out = []
    env = os.environ.get("AFTERGLOW_LAYERSHELL_LIB")
    if env:
        out.append(Path(env))
    here = Path(__file__).resolve().parent
    out += [here / LIB_NAME, here.parent.parent / "native" / "build" / LIB_NAME, here.parent.parent / "native" / LIB_NAME]
    return out


def _load():
    global _lib, _tried
    if _tried:
        return _lib
    _tried = True
    for path in _candidates():
        if path.is_file():
            try:
                lib = ctypes.CDLL(str(path))
                lib.afterglow_layershell_configure.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                                               ctypes.c_int, ctypes.c_int, ctypes.c_int]
                lib.afterglow_layershell_configure.restype = ctypes.c_int
                _lib = lib
                return _lib
            except (OSError, AttributeError) as e:
                logger.warning("layer-shell shim %s could not be loaded: %s", path, e)
    return None


def available() -> bool:
    return _load() is not None


def enable_in_this_process() -> bool:
    """Make Qt's Wayland platform plugin use the layer-shell integration -- helper process only,
    before the QApplication is created."""
    if not available():
        return False
    os.environ["QT_WAYLAND_SHELL_INTEGRATION"] = "layer-shell"
    return True


def configure(window, edges, margins=(0, 0, 0, 0)) -> bool:
    """Turn the (already created, not yet shown) QWindow into an overlay-layer surface anchored
    to ``edges``.  margins = (top, right, bottom, left).  Returns False if it couldn't."""
    lib = _load()
    if lib is None or window is None:
        return False
    try:
        import shiboken6
        ptr = shiboken6.getCppPointer(window)[0]
    except Exception as e:  # noqa: BLE001
        logger.warning("layer-shell: no native pointer for the window: %s", e)
        return False
    t, r, b, l = margins
    rc = lib.afterglow_layershell_configure(ctypes.c_void_p(ptr), anchor_bits(edges), int(t), int(r), int(b), int(l))
    if rc != 0:
        logger.warning("layer-shell configure failed (rc=%s)", rc)
    return rc == 0
