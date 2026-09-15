"""
Central place for reading current UI colors -- either sampled from the
live KDE/Qt palette, or Afterglow Theme's fixed hex overrides when that
setting is on. Everything that paints a themed color (custom buttons,
card backgrounds, the library pages) should go through this rather than
reading QApplication.palette() or a hardcoded QColor directly at each
call site -- Max has a subtle-gradient pass planned for later, and
routing every themed fill through here means that only touches this
file's own accessors, not every place a color gets used.

"Custom Buttons" and "Afterglow Theme" are separate, independent
settings: Custom Buttons controls WHETHER things are custom-painted at
all (vs. left as native Qt/KDE-styled widgets); Afterglow Theme
controls WHICH colors a custom-painted element uses once it IS being
custom-painted (the live KDE palette, or these fixed hex codes). With
Custom Buttons off, Afterglow Theme has nothing left to recolor.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .. import config as config_module


class Theme:
    """Cheap to construct -- mirrors this codebase's existing
    "config_module.load() fresh wherever needed" pattern rather than a
    long-lived singleton, so a settings change is picked up immediately
    without needing an explicit invalidation/refresh call anywhere."""

    def __init__(self, appearance: "config_module.AppearanceSettings | None" = None):
        self._appearance = appearance or config_module.load().appearance

    @property
    def custom_buttons_enabled(self) -> bool:
        return self._appearance.custom_buttons_enabled

    @property
    def afterglow_enabled(self) -> bool:
        return self._appearance.afterglow_theme_enabled

    @property
    def rounded_corners_enabled(self) -> bool:
        return self._appearance.rounded_corners_enabled

    @property
    def corner_radius(self) -> int:
        return self._appearance.rounded_corner_radius

    def accent(self) -> QColor:
        """Most buttons (Filters, Sort By, Info) and the card info
        portrusion (the box holding filters/info/title)."""
        if self.afterglow_enabled:
            return QColor(self._appearance.afterglow_color_accent)
        return QApplication.palette().color(QPalette.Highlight)

    def card_background(self) -> QColor:
        """The background portrusion behind a video (the outer card
        box, visible as a margin around the video player + info box)."""
        if self.afterglow_enabled:
            return QColor(self._appearance.afterglow_color_card_background)
        return QApplication.palette().color(QPalette.AlternateBase)

    def app_background(self) -> QColor:
        """The app window's own background."""
        if self.afterglow_enabled:
            return QColor(self._appearance.afterglow_color_app_background)
        return QApplication.palette().color(QPalette.Window)

    def library_background(self) -> QColor:
        """The Local/Uploaded library pages' own background."""
        if self.afterglow_enabled:
            return QColor(self._appearance.afterglow_color_library)
        return QApplication.palette().color(QPalette.Base)

    def turquoise(self) -> QColor:
        """The Local/Uploaded TAB ICONS' own background specifically
        (not the library page background above, despite the similar
        name -- turquoise was reassigned to this narrower role once the
        library page background itself became the dark blue). Not
        currently used anywhere else, though Max mentioned filters as a
        possible future use."""
        if self.afterglow_enabled:
            return QColor(self._appearance.afterglow_color_turquoise)
        return QApplication.palette().color(QPalette.Mid)

    def button_color(self) -> QColor:
        """A custom-painted button's fill color -- reads the same
        accent color as accent() above (both map to "most buttons" in
        the original spec), kept as its own accessor since a button's
        fill and, say, the info-portrusion's fill are conceptually
        different things that happen to share a color today and might
        not always."""
        return self.accent()

    def button_text_color(self) -> QColor:
        """Text/icon color for a custom-painted button -- always
        computed for contrast against button_color() rather than fixed,
        since Afterglow's accent and a live KDE theme's highlight color
        can each be light or dark."""
        bg = self.button_color()
        # Standard perceptual luminance -- readable on both light and
        # dark accent colors without needing a separate configurable
        # text color.
        luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
        return QColor(20, 20, 20) if luminance > 140 else QColor(240, 240, 240)
