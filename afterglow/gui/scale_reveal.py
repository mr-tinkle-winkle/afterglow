"""
A short "grow out of the button that triggered it" animation, used for
the Sort popover and the Settings page's tab switching -- per Max's
direct request, instead of an instant swap (or a plain fade), both
should visually appear to scale outward from whichever button opened
them.

Two different mechanisms because the two things being revealed aren't
the same kind of widget:
- SortPopover is a genuine top-level popup (Qt.Popup window) -- its own
  geometry can be animated directly, since nothing else's layout
  depends on it.
- The Settings tab pages live inside a QStackedWidget, whose layout
  fully owns and re-asserts each page's geometry -- animating a page's
  real geometry would just get fought and overridden by that layout on
  the very next layout pass. Instead, ScaleRevealOverlay grabs a
  snapshot of the page (already correctly placed and fully visible
  underneath, an instant swap happened as normal) and animates a
  temporary copy of that snapshot growing from the button's position
  up to the real page's rect, then deletes itself -- the real page was
  never touched or delayed, only briefly covered by the animated copy.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QRect, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
from PySide6.QtWidgets import QLabel, QWidget

_DURATION_MS = 220
_START_SIZE = 24


class ScaleRevealOverlay(QLabel):
    def __init__(self, parent_widget: QWidget, pixmap, origin_local: QPoint, final_rect: QRect):
        super().__init__(parent_widget)
        self.setPixmap(pixmap)
        self.setScaledContents(True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        start_rect = QRect(
            origin_local.x() - _START_SIZE // 2, origin_local.y() - _START_SIZE // 2,
            _START_SIZE, _START_SIZE,
        )
        self.setGeometry(start_rect)
        self.show()
        self.raise_()

        self._anim = QPropertyAnimation(self, b"geometry", self)
        self._anim.setDuration(_DURATION_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(start_rect)
        self._anim.setEndValue(final_rect)
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()


def reveal_from_point(target_widget: QWidget, origin_global_point: QPoint) -> None:
    """Call AFTER target_widget already shows its real final content
    (e.g. right after QStackedWidget.setCurrentIndex swaps to it) --
    this only overlays a brief animated copy on top, it doesn't delay
    or hide the real thing."""
    if target_widget.width() <= 0 or target_widget.height() <= 0:
        return
    pixmap = target_widget.grab()
    final_rect = target_widget.rect()
    origin_local = target_widget.mapFromGlobal(origin_global_point)
    ScaleRevealOverlay(target_widget, pixmap, origin_local, final_rect)


def animate_popup_from_point(popup: QWidget, origin_global_point: QPoint, final_geometry: QRect) -> None:
    """For a genuine top-level popup (Qt.Popup) -- animates its OWN
    geometry and opacity growing from a point near origin_global_point
    up to final_geometry, rather than an overlay copy (there's no
    surrounding layout to fight here, so animating the real widget
    directly is simpler and just as correct)."""
    origin_local_in_final = QPoint(
        origin_global_point.x() - final_geometry.x(), origin_global_point.y() - final_geometry.y()
    )
    start_rect = QRect(
        final_geometry.x() + origin_local_in_final.x() - _START_SIZE // 2,
        final_geometry.y() + origin_local_in_final.y() - _START_SIZE // 2,
        _START_SIZE, _START_SIZE,
    )
    popup.setGeometry(start_rect)
    popup.setWindowOpacity(0.0)
    popup.show()

    geo_anim = QPropertyAnimation(popup, b"geometry", popup)
    geo_anim.setDuration(_DURATION_MS)
    geo_anim.setEasingCurve(QEasingCurve.OutCubic)
    geo_anim.setStartValue(start_rect)
    geo_anim.setEndValue(final_geometry)

    opacity_anim = QPropertyAnimation(popup, b"windowOpacity", popup)
    opacity_anim.setDuration(_DURATION_MS)
    opacity_anim.setStartValue(0.0)
    opacity_anim.setEndValue(1.0)

    group = QParallelAnimationGroup(popup)
    group.addAnimation(geo_anim)
    group.addAnimation(opacity_anim)
    # Parented to popup (not left to be garbage-collected) and started
    # detached -- popup itself owns/outlives this group for as long as
    # it's open, and a fresh one is created each time show_below() runs.
    popup._reveal_anim_group = group
    group.start()
