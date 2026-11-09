"""
The themes' built-in animations and decorations (gui/theme_presets.py):

  paint_backdrop()      the Library grid's background (static texture, plus a
                        moving layer for the themes that have one)
  BackdropDriver        repaints the moving layer -- ~15 fps, only while the
                        Library is on screen and Theme animations are on
  card_body_rect() / paint_card_shadow() / paint_card_frame()
                        a card's frame, hard shadow and decorations (tape,
                        drawing number, seal...), drawn into the card's own
                        cached background (so scrolling stays a blit)
  CardFx                a transparent overlay on each card for its entrance
                        animation and its hover effect -- paint-only, never
                        moves the card, so the grid layout is untouched
  ThemeHeader           the Library header's title + subtitle
  paint_nav_label()     a sidebar / tab button's word next to its glyph

Everything is a no-op for the classic theme (theme_presets.active() None).
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient, QRegion)
from PySide6.QtWidgets import QWidget

from . import theme_presets as tp
from .rounded_rect import rounded_rect_path

ANIMATED_BACKDROPS = {"synthwave", "phosphor", "aero", "aero_dark", "grimoire", "stars"}
FPS = 12


def C(s: str, alpha: "int | None" = None) -> QColor:
    c = tp._qcolor(s)
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def _anim_on() -> bool:
    return tp.animations_enabled()


# ==========================================================================
# backdrop
# ==========================================================================
_static_cache: dict = {}


def _static_layer(p, size: QSize) -> QPixmap:
    key = (p.id, p.backdrop, size.width(), size.height())
    pm = _static_cache.get(key)
    if pm is not None:
        return pm
    if len(_static_cache) > 6:
        _static_cache.clear()
    w, h = max(1, size.width()), max(1, size.height())
    pm = QPixmap(w, h)
    pm.fill(C(p.library_bg))
    g = QPainter(pm)
    g.setRenderHint(QPainter.Antialiasing)
    kind = p.backdrop
    rnd = random.Random(7)
    if kind == "synthwave":
        lg = QLinearGradient(0, 0, 0, h)
        lg.setColorAt(0, C("#12002a"))
        lg.setColorAt(0.6, C("#2a0548"))
        lg.setColorAt(1, C("#4a0b52"))
        g.fillRect(pm.rect(), lg)
        # striped sun, top right
        r = min(w, h) * 0.32
        cx, cy = w * 0.82, h * 0.42
        sun = QLinearGradient(0, cy - r, 0, cy + r)
        sun.setColorAt(0, C("#ffcf3f", 90))
        sun.setColorAt(1, C("#ff2fa6", 90))
        path = QPainterPath()
        path.addEllipse(QPointF(cx, cy), r, r)
        stripes = QPainterPath()
        y = cy
        k = 0
        while y < cy + r:
            hgt = 6 + k * 2
            stripes.addRect(QRectF(cx - r, y, 2 * r, hgt * 0.45))
            y += hgt
            k += 1
        g.fillPath(path.subtracted(stripes), sun)
    elif kind == "phosphor":
        rg = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.7)
        rg.setColorAt(0, C("#062a10"))
        rg.setColorAt(1, C("#020a03"))
        g.fillRect(pm.rect(), rg)
        g.setPen(Qt.NoPen)
        for y in range(0, h, 3):
            g.fillRect(QRectF(0, y, w, 1), C("#000000", 70))
    elif kind == "halftone":
        g.fillRect(pm.rect(), C("#ffe14d"))
        g.setPen(Qt.NoPen)
        g.setBrush(C("#e8b800"))
        for y in range(0, h, 12):
            for x in range((y // 12 % 2) * 6, w, 12):
                g.drawEllipse(QPointF(x, y), 2.4, 2.4)
    elif kind == "blueprint":
        g.fillRect(pm.rect(), C("#123d7a"))
        for step, a in ((20, 18), (100, 40)):
            g.setPen(QPen(C("#ffffff", a), 1))
            for x in range(0, w, step):
                g.drawLine(x, 0, x, h)
            for y in range(0, h, step):
                g.drawLine(0, y, w, y)
    elif kind in ("paper", "paper_dark"):
        dark = kind == "paper_dark"
        g.fillRect(pm.rect(), C(p.library_bg))
        dot = C("#ffffff", 9) if dark else C("#000000", 14)
        g.setPen(Qt.NoPen)
        g.setBrush(dot)
        for y in range(0, h, 5):
            for x in range(0, w, 5):
                g.drawRect(QRectF(x, y, 1, 1))
        if not dark:
            g.setPen(QPen(C("#e2a0a0", 70), 1))
            g.drawLine(40, 0, 40, h)
    elif kind == "washi":
        g.fillRect(pm.rect(), C("#f1ebdf"))
        for _ in range(int(w * h / 900)):
            x, y = rnd.uniform(0, w), rnd.uniform(0, h)
            ln = rnd.uniform(6, 26)
            ang = rnd.uniform(0, math.pi)
            g.setPen(QPen(C("#000000", rnd.randint(6, 14)), 0.8))
            g.drawLine(QPointF(x, y), QPointF(x + math.cos(ang) * ln, y + math.sin(ang) * ln))
        # faint 残光 down the left and an open brush ring bottom right
        f = QFont(tp.INK)
        f.setPixelSize(int(min(h * 0.11, 90)))
        g.setFont(f)
        g.setPen(C("#1a1714", 18))
        fm = QFontMetricsF(f)
        for i, ch in enumerate("残光"):
            g.drawText(QPointF(24, h * 0.3 + i * fm.height() * 1.1), ch)
        ring = QPen(C("#1a1714", 16), max(20, min(w, h) * 0.05), Qt.SolidLine, Qt.RoundCap)
        g.setPen(ring)
        rr = min(w, h) * 0.38
        g.drawArc(QRectF(w - rr * 1.3, h - rr * 1.2, rr * 2, rr * 2), 40 * 16, 290 * 16)
    elif kind in ("aero", "aero_dark"):
        lg = QLinearGradient(0, 0, 0, h)
        if kind == "aero":
            lg.setColorAt(0, C("#8fd3ff"))
            lg.setColorAt(0.45, C("#d8f3ff"))
            lg.setColorAt(1, C("#9ee6a8"))
        else:
            lg.setColorAt(0, C("#031522"))
            lg.setColorAt(0.55, C("#06283a"))
            lg.setColorAt(1, C("#0b3a2c"))
        g.fillRect(pm.rect(), lg)
        if kind == "aero_dark":                      # the aurora
            ag = QLinearGradient(w * 0.1, 0, w, 0)
            for t_, c_ in ((0, "#28d6a400"), (0.3, "#28d6a466"), (0.6, "#2b8cff55"), (0.85, "#9a5bff44"), (1, "#9a5bff00")):
                ag.setColorAt(t_, C(c_))
            g.setPen(Qt.NoPen)
            g.setBrush(ag)
            g.drawEllipse(QRectF(w * 0.15, -h * 0.25, w * 0.95, h * 0.55))
    elif kind == "grimoire":
        rg = QRadialGradient(QPointF(w / 2, h * 0.3), max(w, h) * 0.8)
        rg.setColorAt(0, C("#2a1f3a"))
        rg.setColorAt(0.6, C("#140f1e"))
        rg.setColorAt(1, C("#0a0710"))
        g.fillRect(pm.rect(), rg)
    elif kind == "brutalist":
        g.fillRect(pm.rect(), C("#ffffff"))
    elif kind == "stars":
        rg = QRadialGradient(QPointF(w * 0.8, h * 0.1), max(w, h))
        rg.setColorAt(0, C("#1d2a6b"))
        rg.setColorAt(0.4, C("#0a0f2e"))
        rg.setColorAt(1, C("#03040f"))
        g.fillRect(pm.rect(), rg)
        # ringed planet, top right
        r = min(w, h) * 0.24
        cx, cy = w + r * 0.15, -r * 0.2
        pg = QRadialGradient(QPointF(cx - r * 0.3, cy - r * 0.3), r * 1.4)
        pg.setColorAt(0, C("#7a8cff"))
        pg.setColorAt(0.55, C("#2b2f8a"))
        pg.setColorAt(0.8, C("#0a0d30"))
        g.setPen(Qt.NoPen)
        g.setBrush(pg)
        g.drawEllipse(QPointF(cx, cy), r, r)
        g.save()
        g.translate(cx, cy + r * 0.1)
        g.rotate(-14)
        g.setBrush(Qt.NoBrush)
        g.setPen(QPen(C("#b9c3ff", 70), 3))
        g.drawEllipse(QPointF(0, 0), r * 1.7, r * 0.22)
        g.restore()
    g.end()
    _static_cache[key] = pm
    return pm


_STAR_SEED: dict = {}


def _stars(w, h, n):
    key = (w // 50, h // 50, n)
    if key not in _STAR_SEED:
        rnd = random.Random(11)
        _STAR_SEED.clear()
        _STAR_SEED[key] = [(rnd.uniform(0, w), rnd.uniform(0, h), rnd.uniform(0.6, 1.8), rnd.uniform(0, 6.28),
                            rnd.uniform(0.6, 2.2)) for _ in range(n)]
    return _STAR_SEED[key]


def paint_backdrop(painter: QPainter, rect: QRectF, p=None, t: "float | None" = None) -> None:
    """Fill `rect` (the visible viewport, in the painter's coordinates) with
    the theme's backdrop. `t` = seconds, for the moving layer (None = still)."""
    p = p or tp.active()
    if p is None:
        return
    size = QSize(max(1, int(rect.width())), max(1, int(rect.height())))
    painter.drawPixmap(rect.topLeft(), _static_layer(p, size))
    if t is None or p.backdrop not in ANIMATED_BACKDROPS:
        return
    w, h = rect.width(), rect.height()
    painter.save()
    painter.translate(rect.topLeft())
    painter.setRenderHint(QPainter.Antialiasing)
    kind = p.backdrop
    if kind == "synthwave":
        # perspective grid rolling toward the viewer, bottom 40%
        horizon = h * 0.6
        pen = QPen(C("#ff2fa6", 110), 1.4)
        painter.setPen(pen)
        vx = w * 0.5
        for i in range(-14, 15):
            x2 = vx + i * w * 0.12
            painter.drawLine(QPointF(vx + i * 8, horizon), QPointF(x2, h))
        phase = (t * 0.6) % 1.0
        for j in range(12):
            z = (j + phase) / 12.0
            y = horizon + (h - horizon) * (z ** 2.2)
            painter.setPen(QPen(C("#ff2fa6", int(40 + 140 * z)), 1.4))
            painter.drawLine(QPointF(0, y), QPointF(w, y))
    elif kind == "phosphor":
        # a soft bright band rolling down, and a faint flicker
        y = (t * 60) % (h + 200) - 100
        g = QLinearGradient(0, y - 80, 0, y + 80)
        g.setColorAt(0, C("#39ff6a", 0))
        g.setColorAt(0.5, C("#39ff6a", 14))
        g.setColorAt(1, C("#39ff6a", 0))
        painter.fillRect(QRectF(0, y - 80, w, 160), g)
        if math.sin(t * 23) > 0.96:
            painter.fillRect(QRectF(0, 0, w, h), C("#39ff6a", 6))
    elif kind in ("aero", "aero_dark"):
        rnd = random.Random(5)
        for i in range(14):
            r = rnd.uniform(14, 70)
            x = rnd.uniform(0, w) + math.sin(t * 0.4 + i) * 18
            speed = rnd.uniform(8, 22)
            y = h + r - ((t * speed + rnd.uniform(0, h + 2 * r)) % (h + 2 * r))
            _bubble(painter, QPointF(x, y), r, dark=kind == "aero_dark")
    elif kind == "grimoire":
        rnd = random.Random(3)
        painter.setPen(Qt.NoPen)
        for i in range(40):
            x = (rnd.uniform(0, w) + t * rnd.uniform(-6, 6)) % w
            y = (rnd.uniform(0, h) - t * rnd.uniform(4, 14)) % h
            a = int(60 + 120 * (0.5 + 0.5 * math.sin(t * rnd.uniform(0.6, 1.6) + i)))
            painter.setBrush(QColor(233, 196, 106, a))
            painter.drawEllipse(QPointF(x, y), 1.4, 1.4)
    elif kind == "stars":
        painter.setPen(Qt.NoPen)
        star = QColor(255, 255, 255)
        for (x, y, r, ph, sp) in _stars(int(w), int(h), int(w * h / 7000)):
            star.setAlpha(int(70 + 185 * (0.5 + 0.5 * math.sin(t * sp + ph))))
            painter.setBrush(star)
            painter.drawEllipse(QPointF(x, y), r, r)
    painter.restore()


def _bubble(painter, c: QPointF, r: float, dark=False):
    g = QRadialGradient(QPointF(c.x() - r * 0.35, c.y() - r * 0.4), r * 1.3)
    g.setColorAt(0, C("#ffffff", 200 if not dark else 120))
    g.setColorAt(0.12, C("#ffffff", 90 if not dark else 40))
    g.setColorAt(0.55, C("#ffffff", 25 if not dark else 10))
    g.setColorAt(1, C("#ffffff", 80 if not dark else 40))
    painter.setPen(QPen(C("#ffffff", 140 if not dark else 70), 1))
    painter.setBrush(g)
    painter.drawEllipse(c, r, r)


class BackdropDriver(QObject):
    """Keeps a widget's moving backdrop going: repaints `target` ~15 times a
    second while it's visible, its window is active-or-visible, and Theme
    animations are on. Stops by itself when any of that stops being true."""

    def __init__(self, target: QWidget):
        super().__init__(target)
        self._target = target
        self._t0 = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / FPS))
        self._timer.timeout.connect(self._tick)
        p = tp.active()
        if p is not None and p.backdrop in ANIMATED_BACKDROPS and _anim_on():
            self._timer.start()

    def now(self) -> "float | None":
        p = tp.active()
        if p is None or p.backdrop not in ANIMATED_BACKDROPS or not self._timer.isActive():
            return None
        return time.monotonic() - self._t0

    def _tick(self) -> None:
        w = self._target
        win = w.window()
        # only while afterglow is the focused window: behind a game (or any
        # other app) nothing moves and nothing is spent
        if w.isVisible() and win.isVisible() and not win.isMinimized() and win.isActiveWindow():
            w.update(self.exposed_region(w))

    @staticmethod
    def exposed_region(w: QWidget) -> QRegion:
        """The part of `w` the backdrop actually shows through: its visible
        area minus its visible child widgets (the cards). Repainting only that
        keeps the cards themselves from being redrawn on every frame."""
        vis = w.visibleRegion()
        if vis.isEmpty():
            return vis
        for child in w.children():
            if isinstance(child, QWidget) and child.isVisible() and not child.isWindow():
                vis = vis.subtracted(QRegion(child.geometry()))
        return vis


# ==========================================================================
# card frame (painted into VideoCard's cached background)
# ==========================================================================
def card_body_rect(rect) -> QRectF:
    """The card's own body inside its widget rect -- smaller by the hard
    shadow's offset (the shadow is drawn in the strip that's left)."""
    p = tp.active()
    r = QRectF(rect)
    if p is not None and p.shadow and p.shadow_offset:
        o = p.shadow_offset
        r.adjust(0, 0, -o, -o)
    return r


def paint_card_shadow(painter: QPainter, rect, radius: float) -> None:
    p = tp.active()
    if p is None or not p.shadow or not p.shadow_offset:
        return
    body = card_body_rect(rect)
    o = p.shadow_offset
    sh = body.translated(o, o)
    painter.save()
    if C(p.shadow).alpha() < 255:                     # soft: a few stacked, fading rects
        for i in range(4):
            c = C(p.shadow)
            c.setAlpha(int(c.alpha() * (1 - i / 4)))
            painter.fillPath(rounded_rect_path(sh.adjusted(-i, -i, i, i), radius + i), c)
    else:
        painter.fillPath(rounded_rect_path(sh, radius), C(p.shadow))
    painter.restore()


def paint_card_frame(painter: QPainter, rect, radius: float, favorite: bool, video_id: int, video_rect: QRectF) -> None:
    """Frame + decorations on top of the card's fill and thumbnail border."""
    p = tp.active()
    if p is None:
        return
    body = card_body_rect(rect)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    border = p.fav_border if (favorite and p.fav_border) else p.border
    if p.glow and border:
        g = C(p.glow)
        for wdt, a in ((10, 26), (6, 50), (3, 90)):
            g.setAlpha(a if not favorite else min(255, a + 30))
            painter.setPen(QPen(g, wdt))
            painter.drawPath(rounded_rect_path(body.adjusted(wdt / 2, wdt / 2, -wdt / 2, -wdt / 2), radius))
    if border and p.border_width > 0:
        bw = p.border_width
        painter.setPen(QPen(C(border), bw))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(rounded_rect_path(body.adjusted(bw / 2, bw / 2, -bw / 2, -bw / 2), radius))
    d = p.decor
    if d == "dwg":
        # dimension line above the thumbnail; a dashed outline on approved (favourite) drawings
        y = video_rect.top() - 9
        painter.setPen(QPen(C(p.border, 160), 1))
        painter.drawLine(QPointF(video_rect.left(), y), QPointF(video_rect.right(), y))
        for x in (video_rect.left(), video_rect.right()):
            painter.drawLine(QPointF(x, y - 5), QPointF(x, y + 5))
        if favorite:
            painter.setPen(QPen(C("#ffd84a"), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(body.adjusted(3, 3, -3, -3))
    elif d == "legendary" and favorite:
        g = QLinearGradient(body.topLeft(), body.bottomRight())
        for t_, c_ in ((0, "#ffb347"), (0.35, "#e9c46a"), (0.5, "#fff3c4"), (0.65, "#e9c46a"), (1, "#b8860b")):
            g.setColorAt(t_, C(c_))
        painter.setPen(QPen(QBrush(g), 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(rounded_rect_path(body.adjusted(1.5, 1.5, -1.5, -1.5), radius))
    painter.restore()


def paint_card_decor(painter: QPainter, rect, favorite: bool, video_id: int) -> None:
    """Decorations that sit ON TOP of a card's contents (drawn by CardFx):
    tape, stickers, the drawing number, the seal, the big index number."""
    p = tp.active()
    if p is None or not p.decor:
        return
    body = card_body_rect(rect)
    d = p.decor
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    if d == "tape":
        tw, th = min(110.0, body.width() * 0.32), 26.0
        painter.translate(body.center().x(), body.top() + 6)
        painter.rotate(-4 + (video_id % 5) * 2)
        painter.fillRect(QRectF(-tw / 2, -th / 2, tw, th), C("#f3d27a", 150) if not tp._dark(p.app_bg) else C("#c9a24b", 120))
        painter.resetTransform()
        if favorite:
            _sticker(painter, QPointF(body.right() - 26, body.top() + 24), 18, "♥", "#e2675a")
    elif d == "dwg":
        label = f"DWG-{video_id:03d}"
        f = QFont(p.font_body)
        f.setPixelSize(11)
        fm = QFontMetricsF(f)
        bx = QRectF(body.right() - fm.horizontalAdvance(label) - 16, body.bottom() - 22, fm.horizontalAdvance(label) + 16, 22)
        painter.setPen(QPen(C(p.border), 2))
        painter.setBrush(C(p.card_bg))
        painter.drawRect(bx)
        painter.setFont(f)
        painter.drawText(bx, Qt.AlignCenter, label)
    elif d == "seal" and favorite:
        s = 36.0
        painter.translate(body.right() - s * 0.75, body.top() + s * 0.75)
        painter.rotate(-6)
        painter.setPen(Qt.NoPen)
        painter.setBrush(C("#b8221c", 235))
        painter.drawRoundedRect(QRectF(-s / 2, -s / 2, s, s), 4, 4)
        f = QFont(tp.INK)
        f.setPixelSize(int(s * 0.62))
        painter.setFont(f)
        painter.setPen(C("#fffaf0"))
        painter.drawText(QRectF(-s / 2, -s / 2, s, s), Qt.AlignCenter, "残")
        painter.resetTransform()
    elif d == "number":
        f = QFont(p.font_display)
        f.setPixelSize(40)
        painter.setFont(f)
        painter.setPen(C("#000000", 40))
        painter.drawText(body.adjusted(0, 4, -12, 0), Qt.AlignRight | Qt.AlignTop, f"{video_id % 100:02d}")
        if favorite:
            painter.fillRect(QRectF(body.left(), body.top(), body.width(), 8), C("#ff3300"))
    elif d == "caption" and favorite:
        _burst(painter, QPointF(body.right() - 30, body.top() + 28), 24, "#e3262f", "★")
    painter.restore()


def _sticker(painter, c, r, ch, color):
    painter.setPen(QPen(C("#00000033"), 1))
    painter.setBrush(C(color))
    painter.drawEllipse(c, r, r)
    f = QFont(tp.SYM)
    f.setPixelSize(int(r * 1.1))
    painter.setFont(f)
    painter.setPen(C("#ffffff"))
    painter.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter, ch)


def _burst_path(c: QPointF, r: float, n: int = 12, inner: float = 0.55) -> QPainterPath:
    path = QPainterPath()
    for i in range(n * 2):
        a = math.pi * i / n - math.pi / 2
        rr = r if i % 2 == 0 else r * inner
        pt = QPointF(c.x() + math.cos(a) * rr, c.y() + math.sin(a) * rr)
        (path.moveTo if i == 0 else path.lineTo)(pt)
    path.closeSubpath()
    return path


def _burst(painter, c, r, color, text=""):
    path = _burst_path(c, r)
    painter.setPen(QPen(C("#111111"), 2.5))
    painter.setBrush(C(color))
    painter.drawPath(path)
    if text:
        f = QFont("Bangers")
        f.setPixelSize(int(r * 0.8))
        painter.setFont(f)
        painter.setPen(C("#ffffff"))
        painter.drawText(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), Qt.AlignCenter, text)


# ==========================================================================
# per-card entrance + hover overlay
# ==========================================================================
ENTER_MS = {"fade": 380, "neon": 520, "scan": 520, "slam": 520, "draw": 700, "drop": 460, "bleed": 650,
            "float": 520, "shimmer": 700, "cut": 0, "twinkle": 600}
LOOPING_HOVER = {"neon", "cursor", "shine", "sparkle", "orbit"}


class _Clock(QObject):
    """One 60 fps timer shared by every CardFx that is currently animating."""

    def __init__(self):
        super().__init__()
        self.subs: set = set()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)

    def add(self, fx):
        self.subs.add(fx)
        if not self.timer.isActive():
            self.timer.start()

    def remove(self, fx):
        self.subs.discard(fx)
        if not self.subs:
            self.timer.stop()

    def _tick(self):
        for fx in list(self.subs):
            try:
                fx._step()
            except RuntimeError:                       # its C++ side is gone
                self.subs.discard(fx)
        if not self.subs:
            self.timer.stop()


_clock: "_Clock | None" = None
_stagger = {"t": 0.0, "n": 0}


def _get_clock() -> _Clock:
    global _clock
    if _clock is None:
        _clock = _Clock()
    return _clock


class CardFx(QWidget):
    """Transparent overlay covering a VideoCard: its entrance animation (once,
    when it first appears) and its hover effect. Mouse events pass through."""

    def __init__(self, card: QWidget, favorite: bool = False):
        super().__init__(card)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._card = card
        self._p = tp.active()
        self._fav = favorite
        self._enter_t = 1.0
        self._enter_start = None
        self._hover = 0.0
        self._hover_target = 0.0
        self._hover_t0 = time.monotonic()
        self._t0 = time.monotonic()
        self._last = time.monotonic()
        self._anim = _anim_on()
        kind = self._p.enter if self._p else "cut"
        if self._anim and ENTER_MS.get(kind, 0) > 0:
            self._enter_t = 0.0
        card.installEventFilter(self)
        self.setGeometry(card.rect())
        self.raise_()

    # ---- events from the card
    def eventFilter(self, obj, ev):
        et = ev.type()
        if et == ev.Type.Resize:
            self.setGeometry(self._card.rect())
        elif et == ev.Type.Show and self._enter_t < 1.0 and self._enter_start is None:
            now = time.monotonic()
            if now - _stagger["t"] > 0.25:              # a new batch of cards: restart the stagger
                _stagger["n"] = 0
            _stagger["t"] = now
            self._enter_start = now + min(_stagger["n"], 12) * 0.045
            _stagger["n"] += 1
            _get_clock().add(self)
        elif et == ev.Type.Enter:
            self._set_hover(1.0)
        elif et == ev.Type.Leave:
            self._set_hover(0.0)
        return False

    def _set_hover(self, target: float):
        if self._p is None or self._p.hover in ("", "none"):
            return
        if target > 0 and self._hover <= 0:
            self._hover_t0 = time.monotonic()      # loops start from the beginning on each hover
        self._hover_target = target
        if not self._anim:
            self._hover = target
            self.update()
            return
        self._last = time.monotonic()
        _get_clock().add(self)

    def _step(self):
        now = time.monotonic()
        dt = now - self._last
        self._last = now
        busy = False
        if self._enter_t < 1.0 and self._enter_start is not None:
            ms = ENTER_MS.get(self._p.enter, 400) / 1000.0
            self._enter_t = 0.0 if now < self._enter_start else min(1.0, (now - self._enter_start) / ms)
            busy = busy or self._enter_t < 1.0
        if self._hover != self._hover_target:
            step = dt / 0.16
            self._hover = min(self._hover_target, self._hover + step) if self._hover_target > self._hover \
                else max(self._hover_target, self._hover - step)
            busy = True
        if self._hover > 0 and self._p.hover in LOOPING_HOVER:
            busy = True
        self.update()
        if not busy:
            _get_clock().remove(self)

    # ---- painting
    def paintEvent(self, _ev):
        p = self._p
        if p is None:
            return
        if self._enter_t >= 1.0 and self._hover <= 0 and not p.decor:
            return
        g = QPainter(self)
        g.setRenderHint(QPainter.Antialiasing)
        r = card_body_rect(self.rect())
        rad = p.radius
        t = time.monotonic() - self._t0
        paint_card_decor(g, self.rect(), self._fav, int(self._card.property("video_id") or 0))
        if self._hover > 0:
            self._paint_hover(g, r, rad, self._hover, t)
        if self._enter_t < 1.0:
            self._paint_enter(g, QRectF(self.rect()), r, rad, self._enter_t)
        g.end()

    def _paint_enter(self, g, full, r, rad, e):
        p = self._p
        ease = 1 - (1 - e) ** 3
        cover = C(p.library_bg)
        kind = p.enter
        if kind in ("fade", "float", "twinkle", "neon", "drop", "shimmer"):
            cover.setAlpha(int(255 * (1 - ease)))
            if kind == "drop":
                g.fillRect(QRectF(full.left(), full.top(), full.width(), full.height() * (1 - ease)), C(p.library_bg))
            else:
                g.fillRect(full, cover)
            if kind == "neon" and e < 0.7:
                c = C(p.glow or p.border)
                c.setAlpha(int(220 * (1 - e / 0.7)))
                g.setPen(QPen(c, 4))
                g.drawPath(rounded_rect_path(r.adjusted(2, 2, -2, -2), rad))
            if kind == "shimmer":
                x = r.left() - r.width() * 0.4 + (r.width() * 1.8) * ease
                band = QLinearGradient(x - 60, 0, x + 60, 0)
                band.setColorAt(0, C("#fff3c4", 0))
                band.setColorAt(0.5, C("#fff3c4", int(150 * (1 - e))))
                band.setColorAt(1, C("#fff3c4", 0))
                g.save()
                g.setClipPath(rounded_rect_path(r, rad))
                g.fillRect(r, band)
                g.restore()
        elif kind == "scan":
            y = full.top() + full.height() * ease
            g.fillRect(QRectF(full.left(), y, full.width(), full.bottom() - y), C(p.library_bg))
            g.fillRect(QRectF(full.left(), y - 2, full.width(), 3), C("#39ff6a", 200))
        elif kind == "slam":
            if e < 0.18:
                g.fillRect(full, C("#ffffff", int(255 * (1 - e / 0.18))))
            if e < 0.9:
                k = math.sin(min(1.0, e / 0.5) * math.pi / 2)
                rr = 36 * k * (1.0 if e < 0.6 else (0.9 - e) / 0.3)
                if rr > 2:
                    g.save()
                    _burst(g, QPointF(r.right() - 42, r.top() + 40), rr, "#e3262f", "POW!")
                    g.restore()
        elif kind == "draw":
            per = 2 * (r.width() + r.height())
            drawn = per * min(1.0, e / 0.65)
            path = QPainterPath()
            pts = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft(), r.topLeft()]
            left = drawn
            path.moveTo(pts[0])
            for a, b in zip(pts, pts[1:]):
                seg = math.hypot(b.x() - a.x(), b.y() - a.y())
                if left >= seg:
                    path.lineTo(b)
                    left -= seg
                else:
                    f = left / seg
                    path.lineTo(QPointF(a.x() + (b.x() - a.x()) * f, a.y() + (b.y() - a.y()) * f))
                    break
            cover.setAlpha(int(255 * (1 - max(0.0, (e - 0.35) / 0.65))))
            g.fillRect(full, cover)
            g.setPen(QPen(C(p.border), 2))
            g.drawPath(path)
        elif kind == "bleed":
            hole = QPainterPath()
            hole.addRect(full)
            rad_ = math.hypot(full.width(), full.height()) * 0.55 * ease
            circ = QPainterPath()
            circ.addEllipse(full.center(), rad_, rad_ * 0.85)
            g.fillPath(hole.subtracted(circ), C(p.library_bg))

    def _paint_hover(self, g, r, rad, h, t):
        p = self._p
        kind = p.hover
        if kind == "neon":
            pulse = 0.65 + 0.35 * math.sin(t * 5)
            c = C(p.fav_border if self._fav and p.fav_border else (p.glow or p.border))
            for wdt, a in ((14, 40), (8, 80), (4, 160)):
                c.setAlpha(int(a * h * pulse))
                g.setPen(QPen(c, wdt))
                g.drawPath(rounded_rect_path(r.adjusted(wdt / 2, wdt / 2, -wdt / 2, -wdt / 2), rad))
        elif kind == "cursor":
            g.setPen(QPen(C("#39ff6a", int(255 * h)), 1.5))
            g.drawRect(r.adjusted(0.75, 0.75, -0.75, -0.75))
            if int(t * 2.2) % 2 == 0:
                g.fillRect(QRectF(r.right() - 22, r.bottom() - 26, 12, 18), C("#39ff6a", int(230 * h)))
        elif kind == "pow":
            g.setPen(QPen(C("#e3262f", int(255 * h)), 4))
            g.drawPath(rounded_rect_path(r.adjusted(2, 2, -2, -2), rad))
            if h > 0.3:
                _burst(g, QPointF(r.left() + 30, r.top() + 30), 22 * h, "#ffe14d", "!")
        elif kind == "brackets":
            inset = 14 * (1 - h) + 4
            ln = 22.0
            g.setPen(QPen(C("#ffd84a", int(255 * h)), 2.5))
            for (x, y, dx, dy) in ((r.left() + inset, r.top() + inset, 1, 1), (r.right() - inset, r.top() + inset, -1, 1),
                                   (r.left() + inset, r.bottom() - inset, 1, -1),
                                   (r.right() - inset, r.bottom() - inset, -1, -1)):
                g.drawLine(QPointF(x, y), QPointF(x + dx * ln, y))
                g.drawLine(QPointF(x, y), QPointF(x, y + dy * ln))
        elif kind == "lift":
            c = C(p.highlight)
            c.setAlpha(int(200 * h))
            g.setPen(QPen(c, 3))
            g.drawPath(rounded_rect_path(r.adjusted(1.5, 1.5, -1.5, -1.5), rad))
        elif kind == "brush":
            y = r.bottom() - 7
            wdt = (r.width() - 40) * h
            path = QPainterPath()
            path.moveTo(r.left() + 20, y)
            path.cubicTo(r.left() + 20 + wdt * 0.3, y - 4, r.left() + 20 + wdt * 0.7, y + 3, r.left() + 20 + wdt, y - 1)
            g.setPen(QPen(C("#1a1714", 200), 5, Qt.SolidLine, Qt.RoundCap))
            g.drawPath(path)
        elif kind == "shine":
            g.save()
            g.setClipPath(rounded_rect_path(r, rad))
            # one sweep per period, faded in and out at its ends so the restart
            # back at the left edge is never visible (it used to jump there)
            period = 1.8
            ph = ((time.monotonic() - self._hover_t0) % period) / period
            x = r.left() - 60 + ph * (r.width() + 120)
            env = math.sin(math.pi * ph)
            band = QLinearGradient(x - 50, r.top(), x + 50, r.bottom())
            band.setColorAt(0, C("#ffffff", 0))
            band.setColorAt(0.5, C("#ffffff", int(90 * h * env)))
            band.setColorAt(1, C("#ffffff", 0))
            g.fillRect(r, band)
            g.restore()
            g.setPen(QPen(C(p.glow or "#7fd8ff", int(200 * h)), 2.5))
            g.drawPath(rounded_rect_path(r.adjusted(1, 1, -1, -1), rad))
        elif kind == "sparkle":
            rnd = random.Random(self._card.property("video_id") or 1)
            per = 2 * (r.width() + r.height())
            for i in range(7):
                d = (rnd.uniform(0, per) + t * 30) % per
                pt = _perimeter_point(r, d)
                a = 0.5 + 0.5 * math.sin(t * 4 + i * 1.7)
                s = 3 + 4 * a
                g.setPen(QPen(C("#fff3c4", int(255 * h * a)), 1.6))
                g.drawLine(QPointF(pt.x() - s, pt.y()), QPointF(pt.x() + s, pt.y()))
                g.drawLine(QPointF(pt.x(), pt.y() - s), QPointF(pt.x(), pt.y() + s))
        elif kind == "invert":
            g.setPen(QPen(C("#ff3300", int(255 * h)), 8))
            g.drawRect(r.adjusted(4, 4, -4, -4))
        elif kind == "orbit":
            per = 2 * (r.width() + r.height())
            for k in range(3):
                pt = _perimeter_point(r.adjusted(3, 3, -3, -3), (t * 260 - k * 14) % per)
                c = C("#ffd98a", int(255 * h * (1 - k * 0.3)))
                g.setPen(Qt.NoPen)
                g.setBrush(c)
                g.drawEllipse(pt, 3.2 - k * 0.8, 3.2 - k * 0.8)
            g.setPen(QPen(C("#4b5cff", int(160 * h)), 1.5))
            g.setBrush(Qt.NoBrush)
            g.drawPath(rounded_rect_path(r.adjusted(1, 1, -1, -1), rad))


def _perimeter_point(r: QRectF, d: float) -> QPointF:
    w, h = r.width(), r.height()
    d %= 2 * (w + h)
    if d < w:
        return QPointF(r.left() + d, r.top())
    d -= w
    if d < h:
        return QPointF(r.right(), r.top() + d)
    d -= h
    if d < w:
        return QPointF(r.right() - d, r.bottom())
    d -= w
    return QPointF(r.left(), r.bottom() - d)


# ==========================================================================
# Library header title
# ==========================================================================
class ThemeHeader(QWidget):
    """The theme's page title + subtitle, drawn in the Library header's free
    space (between the Local/Uploaded buttons and the toolbar)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._p = tp.active()
        self._count = 0
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setMinimumWidth(0)
        self._blink = None
        if self._p is not None and self._p.title_style == "cursor" and _anim_on():
            self._blink = QTimer(self)
            self._blink.setInterval(530)
            self._blink.timeout.connect(self.update)
            self._blink.start()

    def set_count(self, n: int) -> None:
        if n != self._count:
            self._count = n
            self.update()

    def sizeHint(self):
        return QSize(200, 60)

    def paintEvent(self, _ev):
        p = self._p
        if p is None:
            return
        title = p.words.get("header_title", "").replace("{count}", str(self._count))
        sub = p.words.get("header_sub", "").replace("{count}", str(self._count))
        g = QPainter(self)
        g.setRenderHint(QPainter.Antialiasing)
        g.setRenderHint(QPainter.TextAntialiasing)
        h = self.height()
        sub_px = max(10, h * 0.2)
        title_px = h * (0.62 if p.title_style not in ("brutal",) else 0.86)
        if p.title_style == "script":
            title_px = h * 0.78
        tf = tp.display_font(title_px, p)
        sf = QFont(p.font_body)
        sf.setPixelSize(max(11, int(sub_px)))
        tp.set_weight(sf, 600 if p.font_body.endswith(" AG") else 700)
        if p.title_style in ("chrome", "spaced", "underline"):
            sf.setLetterSpacing(QFont.PercentageSpacing, 140)
            sf.setCapitalization(QFont.AllUppercase)
        x = 18.0
        fm = QFontMetricsF(tf)
        avail = self.width() - x - 10
        while fm.horizontalAdvance(title) > avail and tf.pixelSize() > 10:
            tf.setPixelSize(tf.pixelSize() - 2)
            fm = QFontMetricsF(tf)
        base = h * 0.92
        has_sub = bool(sub) and h > 44 and p.title_style != "brutal"
        if has_sub:
            g.setFont(sf)
            if p.title_style == "comic":
                sfm = QFontMetricsF(sf)
                bx = QRectF(x, 2, sfm.horizontalAdvance(sub) + 16, sub_px + 8)
                g.setPen(QPen(C("#111111"), 2.5))
                g.setBrush(C("#ffffff"))
                g.drawRect(bx)
                g.setPen(C("#111111"))
                g.drawText(bx, Qt.AlignCenter, sub)
            else:
                g.setPen(C(p.muted if p.title_style != "spaced" else p.highlight))
                g.drawText(QPointF(x, sub_px + 2), sub)
        path = QPainterPath()
        path.addText(QPointF(x, base - fm.descent() * 0.6), tf, title)
        path.setFillRule(Qt.WindingFill)
        st = p.title_style
        if st == "chrome":
            for wdt, a in ((14, 40), (8, 70)):
                g.strokePath(path, QPen(C("#ff2fa6", a), wdt))
            g.save()
            g.translate(3, 3)
            g.fillPath(path, C("#7af3ff"))
            g.restore()
            br = path.boundingRect()
            lg = QLinearGradient(0, br.top(), 0, br.bottom())
            lg.setColorAt(0, C("#ffffff"))
            lg.setColorAt(0.48, C("#7af3ff"))
            lg.setColorAt(0.52, C("#ff2fa6"))
            lg.setColorAt(1, C("#ffcf3f"))
            g.fillPath(path, lg)
        elif st == "cursor":
            for wdt, a in ((8, 40),):
                g.strokePath(path, QPen(C("#39ff6a", a), wdt))
            g.fillPath(path, C("#39ff6a"))
            if self._blink is None or int(time.monotonic() * 1.9) % 2 == 0:
                br = path.boundingRect()
                g.fillRect(QRectF(br.right() + 6, br.top(), fm.horizontalAdvance("M") * 0.7, br.height()), C("#39ff6a"))
        elif st == "comic":
            g.save()
            g.translate(5, 5)
            g.fillPath(path, C("#e3262f"))
            g.restore()
            g.strokePath(path, QPen(C("#111111"), 6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            g.fillPath(path, C("#ffffff"))
        elif st == "underline":
            g.fillPath(path, C(p.text))
            br = path.boundingRect()
            g.fillRect(QRectF(x, br.bottom() + 4, br.width() + 10, 2), C(p.text))
        elif st == "script":
            g.fillPath(path, C("#7a3b2e" if p.app_bg != "#141210" else "#f0b8a8"))
        elif st == "brush":
            br = path.boundingRect()
            stroke = QPainterPath()
            stroke.moveTo(br.left() - 6, br.bottom() + 6)
            stroke.cubicTo(br.left() + br.width() * 0.3, br.bottom() + 2, br.left() + br.width() * 0.7, br.bottom() + 10,
                           br.right() + 26, br.bottom() + 3)
            g.strokePath(stroke, QPen(C("#1a1714", 215), 9, Qt.SolidLine, Qt.RoundCap))
            g.fillPath(path, C("#1a1714"))
        elif st == "aero":
            dark = tp._dark(p.app_bg)
            for wdt, a in ((10, 70), (5, 120)):
                g.strokePath(path, QPen(C("#2b8cff" if dark else "#ffffff", a), wdt))
            g.fillPath(path, C("#ffffff" if dark else "#0d6aa8"))
        elif st == "gilded":
            g.save()
            g.translate(0, 2)
            g.fillPath(path, C("#5a4214"))
            g.restore()
            br = path.boundingRect()
            lg = QLinearGradient(0, br.top(), 0, br.bottom())
            lg.setColorAt(0, C("#fff3c4"))
            lg.setColorAt(0.5, C("#e9c46a"))
            lg.setColorAt(1, C("#b8860b"))
            g.fillPath(path, lg)
        elif st == "brutal":
            g.fillPath(path, C("#000000"))
        else:                                   # spaced / plain
            g.fillPath(path, C(p.text))
        g.end()


# ==========================================================================
# words on nav / tab buttons
# ==========================================================================
def paint_nav_label(painter: QPainter, rect: QRectF, glyph: "QPixmap | None", word: str, color: QColor,
                    glyph_size: int) -> None:
    """A tall sidebar button: glyph near the top, the word running down the
    rest of it. A wide button: glyph on the left, word to its right."""
    p = tp.active()
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    vertical = rect.height() > rect.width() * 1.8
    f = _nav_font(p)
    if vertical:
        gs = min(glyph_size, int(rect.width() * 0.62))
        top = rect.top() + rect.width() * 0.22
        if glyph is not None and not glyph.isNull():
            painter.drawPixmap(QRectF(rect.center().x() - gs / 2, top, gs, gs).toRect(), glyph)
        avail_len = rect.bottom() - (top + gs) - rect.width() * 0.3
        px = rect.width() * 0.34
        f.setPixelSize(int(px))
        fm = QFontMetricsF(f)
        while fm.horizontalAdvance(word) > avail_len and f.pixelSize() > 9:
            f.setPixelSize(f.pixelSize() - 1)
            fm = QFontMetricsF(f)
        painter.setFont(f)
        painter.setPen(color)
        cx = rect.center().x()
        cy = top + gs + (rect.bottom() - (top + gs)) / 2
        painter.translate(cx, cy)
        painter.rotate(90)
        painter.drawText(QRectF(-avail_len / 2 - 40, -fm.height() / 2, avail_len + 80, fm.height()), Qt.AlignCenter, word)
    else:
        gs = min(glyph_size, int(rect.height() * 0.66))
        x = rect.left() + rect.height() * 0.22
        if glyph is not None and not glyph.isNull():
            painter.drawPixmap(QRectF(x, rect.center().y() - gs / 2, gs, gs).toRect(), glyph)
        f.setPixelSize(int(rect.height() * 0.28))
        f.setLetterSpacing(QFont.PercentageSpacing, 100 + min(p.letter_spacing, 4))
        painter.setFont(f)
        painter.setPen(color)
        painter.drawText(QRectF(x + gs + 8, rect.top(), rect.right() - (x + gs + 8) - 8, rect.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, word)
    painter.restore()


def _nav_font(p, px: int = 10) -> QFont:
    if p.nav_font:
        f = QFont(p.nav_font)
        f.setPixelSize(px)
        tp.set_weight(f, 700)
        return f
    return tp.display_font(px, p)


def nav_word_width(word: str, height: int) -> int:
    p = tp.active()
    if p is None or not word:
        return 0
    f = _nav_font(p, max(9, int(height * 0.28)))
    f.setLetterSpacing(QFont.PercentageSpacing, 100 + min(p.letter_spacing, 4))
    return int(QFontMetricsF(f).horizontalAdvance(word)) + 18


class _BackdropPainter(QObject):
    """Paints the theme backdrop under a scroll area's content, fixed to the
    viewport (cards scroll over it). Installed as an event filter: the
    widget's own background fill happens first, its children paint after."""

    def __init__(self, container: QWidget, viewport: QWidget):
        super().__init__(container)
        self._container = container
        self._viewport = viewport
        self.driver = BackdropDriver(container)
        container.installEventFilter(self)
        viewport.installEventFilter(self)
        self.driver._timer.timeout.connect(self._update_viewport)

    def _update_viewport(self) -> None:
        # only where the viewport shows past the content (a short library)
        if self._viewport.isVisible() and self._viewport.window().isActiveWindow():
            r = QRegion(self._viewport.rect()).subtracted(QRegion(self._container.geometry()))
            if not r.isEmpty():
                self._viewport.update(r)

    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.Paint:
            if obj is self._container:
                origin = -QPointF(self._container.pos())
            else:
                origin = QPointF(0, 0)
            g = QPainter(obj)
            paint_backdrop(g, QRectF(origin, QSize(self._viewport.size()).toSizeF()), t=self.driver.now())
            g.end()
        return False


def install_backdrop(container: QWidget, viewport: QWidget) -> "_BackdropPainter | None":
    if tp.active() is None:
        return None
    return _BackdropPainter(container, viewport)
