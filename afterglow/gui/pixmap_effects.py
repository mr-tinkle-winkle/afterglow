"""
Shared pixmap helpers for border rendering (sidebar nav buttons AND the
unedited-video-clip highlight): loading a user-chosen custom image in
place of the built-in gradient, and applying a hue shift to whichever
pixmap ends up being used.

Both are meant to be called ONCE per widget construction / settings
reload, never per-frame or from inside a paintEvent -- hue_shift_pixmap
in particular loops pixel-by-pixel in pure Python (no numpy dependency
is currently declared for this project's flake, so this deliberately
doesn't reach for it), which is a fine one-time cost (roughly half a
second on a 512x512 image, measured directly) but would be a real
per-frame performance problem if it were ever called from paintEvent.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QPixmap, QImage, QColor


def resolve_border_pixmap(custom_image_path: str, default_pixmap: QPixmap) -> QPixmap:
    """A user-chosen image file, if one is set and actually exists, in
    place of the built-in default -- silently falls back to the default
    if the path is empty, the file's gone missing (deleted/moved since
    it was set), or fails to load as an image, rather than erroring or
    showing nothing."""
    if custom_image_path:
        path = Path(custom_image_path)
        if path.exists():
            custom = QPixmap(str(path))
            if not custom.isNull():
                return custom
    return default_pixmap


def hue_shift_pixmap(pixmap: QPixmap, degrees: int) -> QPixmap:
    """Rotate the hue of every pixel by `degrees` (wrapping around 360),
    preserving each pixel's own saturation/value/alpha. Fully transparent
    pixels and fully achromatic ones (pure gray/white/black -- Qt reports
    no defined hue for these, getHsvF()'s hue component comes back -1)
    are left alone rather than getting an arbitrary hue assigned to them.
    degrees == 0 (mod 360) returns the input pixmap as-is with no copy
    at all -- the common case, since this is opt-in and defaults to 0."""
    if degrees % 360 == 0:
        return pixmap
    image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
    shift = (degrees % 360) / 360.0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 0:
                continue
            h, s, v, a = color.getHsvF()
            if h < 0:
                continue  # achromatic -- no hue to shift
            image.setPixelColor(x, y, QColor.fromHsvF((h + shift) % 1.0, s, v, a))
    return QPixmap.fromImage(image)


_hue_shift_cache: dict[tuple[str, int], QPixmap] = {}


def hue_shift_pixmap_cached(cache_key: str, pixmap: QPixmap, degrees: int) -> QPixmap:
    """Same as hue_shift_pixmap(), but memoized process-wide by
    (cache_key, degrees). Matters most for VideoCard's unedited-clip
    highlight, which is rebuilt fresh in every single card's __init__ --
    one per video shown in the grid -- so without this, a hue shift's
    one-time ~half-second cost (measured directly on a 512x512 image)
    would multiply by however many cards are in the library instead of
    genuinely happening once. cache_key should identify the SOURCE
    pixmap (e.g. the resource name or custom image path used), since
    the actual QPixmap object's identity isn't a reliable/stable cache
    key across separately-loaded copies of the same underlying image."""
    key = (cache_key, degrees % 360)
    if key not in _hue_shift_cache:
        _hue_shift_cache[key] = hue_shift_pixmap(pixmap, degrees)
    return _hue_shift_cache[key]
