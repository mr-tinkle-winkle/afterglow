"""
Shared click-pulse animation: ease into a smaller icon size on
mouse-down, ease back to full size on mouse-up. Used by the sidebar
nav buttons (QToolButton, sizes itself via setIconSize) and the
Library's Local/Uploaded tab icons (QTabBar, sizes itself via
setTabIcon of a rescaled pixmap) -- two different widget types with no
shared base class, but the same press/release feel wanted for both, so
the animation logic itself lives here instead of being duplicated.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QVariantAnimation, QEasingCurve

PULSE_SHRINK_FACTOR = 0.82  # icon shrinks to 82% of full size while held down
PULSE_DOWN_MS = 90
PULSE_UP_MS = 160


class PulseAnimator(QObject):
    def __init__(self, get_base_size, apply_size, parent=None):
        """
        get_base_size: () -> int -- the button/tab's current "full" icon
        size. A callback rather than a captured value so it keeps
        tracking window/tab resizes instead of going stale.

        apply_size: (int) -> None -- called on every animation frame
        with the icon size to render at right now.
        """
        super().__init__(parent)
        self._get_base_size = get_base_size
        self._apply_size = apply_size
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(lambda v: self._apply_size(round(v)))

    def press(self) -> None:
        base = self._get_base_size()
        self._anim.stop()
        self._anim.setDuration(PULSE_DOWN_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(base)
        self._anim.setEndValue(round(base * PULSE_SHRINK_FACTOR))
        self._anim.start()

    def release(self) -> None:
        base = self._get_base_size()
        current = base * PULSE_SHRINK_FACTOR
        if self._anim.state() == QVariantAnimation.Running:
            value = self._anim.currentValue()
            if value is not None:
                current = value
        self._anim.stop()
        self._anim.setDuration(PULSE_UP_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.setStartValue(round(current))
        self._anim.setEndValue(base)
        self._anim.start()
