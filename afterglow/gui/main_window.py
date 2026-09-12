"""
Main window: left sidebar nav across the 3 pages -- Settings, Library,
Editor. All three are live now (no more "coming soon" placeholders).

Each page is constructed once here and lives inside the QStackedWidget for
the app's whole lifetime; switching nav only changes which one is visible.
This is also what makes the Editor page's "remember what I was last
editing" requirement work for free -- see editor_page.py's docstring.

The sidebar is icon-only: Library and Editor are the two primary nav
buttons (stretched to fill most of the sidebar's height, so they scale
with the window), with Settings demoted to a small gear button pinned to
the bottom rather than a third nav-list entry -- Library is the page
you land on and return to, Settings is something you dip into
occasionally.

The sidebar's width and each nav icon's size both scale with the window
rather than staying fixed -- a fixed 72px sidebar with fixed 32px icons
looked proportionally too small once the window was large/fullscreen.

Most of the sidebar's visual tuning (border widths, border darkening,
icon size, whether Settings gets a border at all) is configurable now
via Settings > General (see config.AppearanceSettings) -- read fresh at
construction time; changing it takes effect on next launch, not live,
since these buttons are only ever built once.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QRegion, QColor, QIcon, QPixmap, QImage
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QToolButton,
    QButtonGroup, QStackedWidget, QSizePolicy,
)

from .. import config as config_module
from .settings_page import SettingsPage
from .library_page import LibraryPage
from .editor_page import EditorPage
from .resources import resource_qicon, resource_qpixmap
from .scaling import compute_scale
from .pixmap_effects import resolve_border_pixmap, hue_shift_pixmap_cached
from .pulse_animation import PulseAnimator

# Indices into self.stack -- fixed at construction time (see __init__).
_SETTINGS_INDEX = 0
_LIBRARY_INDEX = 1
_EDITOR_INDEX = 2

SIDEBAR_WIDTH_FRACTION = 0.07  # of the whole window's width
SIDEBAR_MIN_WIDTH = 64
SIDEBAR_MAX_WIDTH = 140

# Fixed, not user-configurable (unlike the border brightness settings) --
# darkens the icon ITSELF (not the gradient border behind it) while a
# nav button isn't the current page.
INACTIVE_ICON_DARKEN_FACTOR = 0.45

# Settings' own gradient asset doesn't exist yet -- reusing Library's
# rather than shipping no border at all for "Always"/"only when on
# Settings" modes. Swap this for a dedicated asset if one gets made.
_SETTINGS_GRADIENT_IMAGE = "library_bg_gradient.png"


def _darken_pixmap(pixmap: QPixmap, darken_factor: float) -> QPixmap:
    """Multiply-blend darken that respects the source's own alpha
    channel (so an icon's transparent background stays transparent,
    only its opaque pixels actually darken) -- same technique already
    used for the border gradient darkening below, just applied to an
    icon pixmap instead of a rectangular background image.

    Built via QImage in an explicit alpha format rather than a plain
    QPixmap: a bare `QPixmap(size)` + `.fill(Qt.transparent)` isn't
    guaranteed to carry an alpha channel on every platform/format, so
    the "transparent" fill can silently render as opaque white
    instead (seen as a white box behind inactive sidebar icons).
    QImage.Format_ARGB32_Premultiplied always has one.

    Filling the whole canvas under CompositionMode_Multiply also
    necessarily makes every pixel fully opaque on its own -- Qt's
    compositing math gives `alpha_out = alpha_src + alpha_dst*(1 -
    alpha_src)`, which is 1 wherever the opaque gray fill (alpha_src=1)
    lands, regardless of blend mode or the destination's own alpha.
    Left alone that reintroduces the same solid-box problem one layer
    down (gray instead of white). The final DestinationIn pass re-clips
    the darkened result back down to the source icon's own alpha shape,
    so only pixels the icon actually covers stay visible."""
    source_image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
    result_image = QImage(source_image.size(), QImage.Format_ARGB32_Premultiplied)
    result_image.fill(Qt.transparent)
    painter = QPainter(result_image)
    painter.drawImage(0, 0, source_image)
    gray = round(255 * (1 - darken_factor))
    painter.setCompositionMode(QPainter.CompositionMode_Multiply)
    painter.fillRect(result_image.rect(), QColor(gray, gray, gray))
    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, source_image)
    painter.end()
    return QPixmap.fromImage(result_image)


class _ScalingIconButton(QToolButton):
    """A QToolButton whose icon is resized to fill the button's own
    footprint (minus a small padding) instead of staying at a fixed
    pixel size regardless of how big the button itself gets."""

    ICON_PADDING = 14

    def __init__(self, icon_name: str, tooltip: str, appearance: "config_module.AppearanceSettings",
                 icon_size_percent: int, parent=None, size_basis: str = "min",
                 gradient_image_name: str | None = None, border_mode: str = "always",
                 border_brightness_multiplier: int = 100):
        super().__init__(parent)
        self.setToolTip(tooltip)
        self.setCheckable(True)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setAutoRaise(True)
        # Fully flat/transparent regardless of checked state -- without
        # this, the native style's own "checked" background fill (which
        # varies by theme/style) was what made the active button's
        # overall painted footprint look bigger than the inactive one,
        # even though neither button's actual widget geometry ever
        # changes. Only this class's own paintEvent below should ever
        # draw anything but the icon itself.
        self.setStyleSheet(
            "QToolButton { border: none; background: transparent; }"
            "QToolButton:checked { border: none; background: transparent; }"
            "QToolButton:pressed { border: none; background: transparent; }"
        )

        # Explicit, deterministic border widths by checked state -- Qt's
        # native "checked" styling for a flat/autoRaise button can itself
        # visually encroach into the border area (a fill/inset that
        # varies by theme), which was making the ACTIVE button's
        # gradient border look smaller than the inactive one even though
        # the same width was being drawn underneath for both.
        self._active_border_width = appearance.active_border_width
        self._inactive_border_width = appearance.inactive_border_width
        # This button's own multiplier applied on top of the shared
        # active/inactive brightness settings -- clamped to [0, 100]
        # since the multiply-blend darkening technique below can only
        # ever darken a color, never brighten one past "no darkening at
        # all" (darken_factor == 0), so a multiplier over 100% just caps
        # there instead of doing anything further.
        mult = border_brightness_multiplier / 100.0
        effective_active_brightness = max(0.0, min(100.0, appearance.active_border_brightness * mult))
        effective_inactive_brightness = max(0.0, min(100.0, appearance.inactive_border_brightness * mult))
        self._active_darken_factor = 1 - (effective_active_brightness / 100.0)
        self._inactive_darken_factor = 1 - (effective_inactive_brightness / 100.0)
        self._border_mode = border_mode  # "always" | "only_active" | "disabled"
        self._icon_scale = icon_size_percent / 100.0

        normal_pixmap = resource_qpixmap(icon_name)
        self._normal_icon = QIcon(normal_pixmap)
        self._dark_icon = QIcon(_darken_pixmap(normal_pixmap, INACTIVE_ICON_DARKEN_FACTOR))
        self.toggled.connect(lambda _checked: self._apply_icon_for_state())

        # "min": size to whichever of width/height is smaller (used by
        # Library/Editor, which are tall and narrow -- width is always
        # the limiting dimension there). "width": size purely off width,
        # ignoring this button's own height -- used by Settings, whose
        # height is a small fixed value by design (see MainWindow), so
        # min() would size its icon off that instead of matching the
        # other two buttons' actual (width-driven) icon size.
        self._size_basis = size_basis
        self._gradient_pixmap = None
        if gradient_image_name:
            base_pixmap = resource_qpixmap(gradient_image_name)
            # A custom image (Settings > General) takes the place of the
            # built-in gradient entirely for ALL sidebar borders alike --
            # this is one shared setting per border TYPE, not per button
            # (see AppearanceSettings.sidebar_border_image_path's own
            # comment for why). Same stretch-to-fill-then-clip-to-ring
            # rendering as the built-in gradients get in paintEvent below
            # either way -- an unusual aspect ratio in the chosen image
            # will just stretch like the built-in ones already do.
            base_pixmap = resolve_border_pixmap(appearance.sidebar_border_image_path, base_pixmap)
            self._gradient_pixmap = hue_shift_pixmap_cached(
                appearance.sidebar_border_image_path or gradient_image_name,
                base_pixmap, appearance.sidebar_border_hue_shift,
            )
        if self._gradient_pixmap is not None:
            # Border width/visibility depends on checked state (see
            # paintEvent) -- repaint immediately when that changes, not
            # just whenever something else happens to trigger one.
            self.toggled.connect(lambda _checked: self.update())

        self._natural_icon_size = 8  # replaced by _update_icon_size() below
        self._pulse = PulseAnimator(
            get_base_size=lambda: self._natural_icon_size,
            apply_size=lambda size: self.setIconSize(QSize(size, size)),
        )
        self._update_icon_size()
        self._apply_icon_for_state()

    def _apply_icon_for_state(self) -> None:
        self.setIcon(self._normal_icon if self.isChecked() else self._dark_icon)

    def _border_visible(self) -> bool:
        if self._border_mode == "disabled":
            return False
        if self._border_mode == "only_active":
            return self.isChecked()
        return True  # "always"

    def paintEvent(self, event) -> None:
        if self._gradient_pixmap is not None and self._border_visible():
            # Same effect as VideoCard's unedited-clip highlight (a
            # gradient image behind the content, only visible as a
            # border ring around it) but via a CLIP REGION instead of
            # painting a solid inset fill on top to mask the center --
            # a QToolButton's normal resting appearance is flat/
            # transparent (no opaque background of its own the way a
            # VideoCard has), so filling the center with a guessed
            # "normal" background color would show as a mismatched solid
            # box rather than blending in. Clipping the gradient draw to
            # just the border ring sidesteps needing to know or replicate
            # what the button's own background actually looks like --
            # its real paintEvent (called normally afterward, unclipped)
            # draws the icon/hover/checked state exactly as it always
            # would, just with the gradient sitting behind it in the
            # margin.
            painter = QPainter(self)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            border = self._active_border_width if self.isChecked() else self._inactive_border_width
            inner_rect = self.rect().adjusted(border, border, -border, -border)
            clip_region = QRegion(self.rect()) - QRegion(inner_rect)
            painter.setClipRegion(clip_region)
            painter.drawPixmap(self.rect(), self._gradient_pixmap)
            darken_factor = self._active_darken_factor if self.isChecked() else self._inactive_darken_factor
            if darken_factor > 0:
                # Multiply-blend gray fill darkens any underlying color
                # proportionally, rather than a flat alpha-black overlay
                # which would wash out darker parts of the gradient less
                # evenly than lighter ones.
                gray = round(255 * (1 - darken_factor))
                painter.setCompositionMode(QPainter.CompositionMode_Multiply)
                painter.fillRect(self.rect(), QColor(gray, gray, gray))
            painter.end()
        super().paintEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_icon_size()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._pulse.press()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._pulse.release(is_hovered=self.underMouse())
        super().mouseReleaseEvent(event)

    def enterEvent(self, event) -> None:
        self._pulse.hover_enter()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._pulse.hover_leave()
        super().leaveEvent(event)

    def icon_size_for_width(self, width: int) -> int:
        """The icon pixel size this button would use at the given width,
        with no dependency on its actual current height -- exposed so
        MainWindow can compute Settings' target height (icon size +
        padding) from the sidebar width alone, without a circular
        dependency on this button's own not-yet-updated height."""
        return max(round((width - self.ICON_PADDING) * self._icon_scale), 8)

    def _update_icon_size(self) -> None:
        # min(width, height) rather than stretching to each dimension
        # independently -- these are square source icons, and stretching
        # them non-uniformly would distort them.
        if self._size_basis == "width":
            size = self.icon_size_for_width(self.width())
        else:
            size = max(round((min(self.width(), self.height()) - self.ICON_PADDING) * self._icon_scale), 8)
        self._natural_icon_size = size
        self.setIconSize(QSize(size, size))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("afterglow")
        # 16:9-ish and reasonably large by default, matching the
        # 1920x1080 reference-scale baseline in scaling.py -- the
        # previous 1000x700 (a boxier ~10:7 ratio) was what read as
        # "opens vertically maximized and horizontally somewhat slim"
        # on a widescreen display, since a non-widescreen-ish window
        # size looks comparatively narrow next to a fullscreen-shaped
        # taskbar/monitor.
        self.resize(1600, 900)

        appearance = config_module.load().appearance

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)

        # ---- sidebar: Library + Editor (icon-only, fill the height),
        # gear (Settings) pinned to the bottom ----
        self.sidebar = QWidget()
        self.sidebar.setFixedWidth(round(self.width() * SIDEBAR_WIDTH_FRACTION))
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        sidebar_layout.setSpacing(4)
        layout.addWidget(self.sidebar)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        self.library_nav_btn = _ScalingIconButton(
            "library.png", "Library", appearance, appearance.library_icon_size,
            gradient_image_name="library_bg_gradient.png",
            border_brightness_multiplier=appearance.library_border_brightness_multiplier,
        )
        self.editor_nav_btn = _ScalingIconButton(
            "editor.png", "Editor", appearance, appearance.editor_icon_size,
            gradient_image_name="editor_bg_gradient.png",
            border_brightness_multiplier=appearance.editor_border_brightness_multiplier,
        )
        # size_basis="width": Settings has a small FIXED height (below),
        # so sizing its icon off min(width, height) like the other two
        # would size it off that small height instead, making it much
        # smaller than Library/Editor's icons even at the same scale --
        # basing it on width alone (matching the sidebar's own width,
        # same as the other two effectively use) keeps all three the
        # same size. settings_border_mode maps directly to this button's
        # border_mode ("only_settings" -> "only_active").
        settings_border_mode = {
            "disabled": "disabled", "always": "always", "only_settings": "only_active",
        }.get(appearance.settings_border_mode, "only_active")
        self.settings_nav_btn = _ScalingIconButton(
            "settings.png", "Settings", appearance, appearance.settings_icon_size,
            size_basis="width",
            gradient_image_name=_SETTINGS_GRADIENT_IMAGE, border_mode=settings_border_mode,
            border_brightness_multiplier=appearance.settings_border_brightness_multiplier,
        )

        # Library and Editor stretch to fill most of the sidebar's
        # vertical space; the gear stays a fixed small size at the bottom
        # -- just tall enough for its icon (same size as the other two)
        # plus a little padding, set alongside the sidebar width below.
        sidebar_layout.addWidget(self.library_nav_btn, stretch=1)
        sidebar_layout.addWidget(self.editor_nav_btn, stretch=1)
        sidebar_layout.addStretch(0)
        self.settings_nav_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        sidebar_layout.addWidget(self.settings_nav_btn)

        self.nav_group.addButton(self.library_nav_btn, _LIBRARY_INDEX)
        self.nav_group.addButton(self.editor_nav_btn, _EDITOR_INDEX)
        self.nav_group.addButton(self.settings_nav_btn, _SETTINGS_INDEX)
        self.nav_group.idClicked.connect(self._on_nav_clicked)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        self.settings_page = SettingsPage()
        self.library_page = LibraryPage()
        self.editor_page = EditorPage()

        # Insertion order must match _SETTINGS_INDEX / _LIBRARY_INDEX /
        # _EDITOR_INDEX above.
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(self.library_page)
        self.stack.addWidget(self.editor_page)

        # Double-click / context-menu "Edit" in the Library routes here to
        # the Editor page (and loads that video into it).
        self.library_page.edit_requested.connect(self._open_in_editor)
        # Prev/Next arrows in the Editor -- see EditorPage.set_neighbor_provider
        # and LibraryPage.neighbors_for's own docstrings for how this stays
        # live rather than being a one-time snapshot of the video list.
        self.editor_page.set_neighbor_provider(self.library_page.neighbors_for)

        self.library_nav_btn.setChecked(True)
        self.stack.setCurrentIndex(_LIBRARY_INDEX)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = round(self.width() * SIDEBAR_WIDTH_FRACTION)
        width = max(SIDEBAR_MIN_WIDTH, min(SIDEBAR_MAX_WIDTH, width))
        self.sidebar.setFixedWidth(width)

        # Settings' fixed height is derived from the SAME width the icon
        # itself will be sized from (icon_size_for_width uses only
        # width, not this button's actual current height -- see its
        # docstring), plus a little padding -- "just enough padding"
        # rather than the fixed 48px box it used to sit in regardless of
        # how big its icon actually was.
        settings_icon_size = self.settings_nav_btn.icon_size_for_width(width)
        self.settings_nav_btn.setFixedHeight(settings_icon_size + 12)

        # "The current scale is great for fullscreen -- scale everything
        # based on the window size." 1920x1080 is treated as the 1.0x
        # baseline (see scaling.py) that the various hardcoded sizes
        # above were tuned against, so this scales the Editor's
        # trim-timeline/volume-bar sizing and the Library cards' title
        # font up or down together as the window resizes, rather than
        # them staying fixed while just the sidebar/its icons scaled.
        scale = compute_scale(self.width(), self.height())
        self.editor_page.apply_scale(scale)
        self.library_page.apply_scale(scale)

    def _on_nav_clicked(self, index: int) -> None:
        if index == _LIBRARY_INDEX:
            # Direct click on Library (not the Editor-redirect case
            # below, which sets its own message right after this) --
            # clear any "Select a video." prompt left over from an
            # earlier redirect, since it no longer applies once the
            # user has actively chosen to look at the Library.
            self.library_page.clear_status_message()

        if index == _SETTINGS_INDEX:
            # Settings is built once at startup (like the other two
            # pages) and never rebuilt on nav -- refresh whatever it
            # shows that can change while the app's been running
            # (filters created/renamed from the Library since launch).
            self.settings_page.refresh_dynamic_lists()

        if index == _EDITOR_INDEX and self.editor_page.current_video_id is None:
            # Nothing has ever been loaded into the Editor -- go to the
            # Library instead and prompt there, rather than showing the
            # Editor's own empty state. Re-check Library so the nav
            # buttons stay in sync with what's actually on screen.
            self.library_nav_btn.setChecked(True)
            index = _LIBRARY_INDEX
            self.library_page.show_status_message("Select a video.")

        self.stack.setCurrentIndex(index)
        # Library reflects any edits/deletes made from the Editor page
        # (e.g. an Undo changing has_edit, or a delete elsewhere) whenever
        # it's navigated back to, rather than needing a manual refresh.
        if index == _LIBRARY_INDEX:
            self.library_page.refresh()

    def _open_in_editor(self, video_id: int) -> None:
        self.editor_page.load_video(video_id)
        self.editor_nav_btn.setChecked(True)
        self.stack.setCurrentIndex(_EDITOR_INDEX)
