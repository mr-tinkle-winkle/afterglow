"""
Comic touches for text elements (drawn by render.py, so the preview and the
export are identical):

* COMIC EFFECTS / EXPRESSIONS -- procedural, animated drawings that sit in a
  text element's picture slot (TextStyle.comic_effect), so they can be
  placed, scaled, rotated, keyframed, bubbled, faded and saved as global
  presets like any text. Every effect has
    - its OWN in / out transition (TextStyle.effect_in / effect_out seconds):
      a scribblenado winds itself up and unwinds, unease lines grow out of
      the head and pull back in, a vein pops in, hearts start floating...;
    - an IDLE loop while it's on screen (TextStyle.effect_speed; 0 = still):
      the vein throbs, the scribble boils and spins, rain falls...
  See EFFECTS below (grouped by feeling) for the whole list.
* TEXT MOTION -- whole-element animation for any text (also onomatopoeia):
  Bounce in, Jiggle in/out, an "Animate in" / "Animate out" style (pop,
  slam, spin, stretch, drop, zoom, flip, letters... / pop, shrink, spin,
  fly up, fall, flip, letters, explode) and an Idle loop (shake, pulse,
  wobble, float, swing, wave, jitter). text_motion() returns the extra
  offset / rotation / scale / opacity for a moment; letter_motion() the
  per-letter part (letters / explode / wave / jitter / jumble).

Everything is a pure function of time, so a frame always renders the same.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
                           QRadialGradient)

EPS = 1e-6
BOIL_FPS = 12.0          # hand-drawn "boil" redraw rate
INK = QColor("#1a1a1a")


# =========================================================================
# small helpers
# =========================================================================
def _rand(seed: int) -> float:
    """Deterministic 0..1 from an integer (no global RNG -- frames must repeat)."""
    x = (int(seed) * 1103515245 + 12345) & 0x7FFFFFFF
    x ^= x >> 13
    x = (x * 2654435761) & 0xFFFFFFFF
    return (x & 0xFFFFFF) / float(0xFFFFFF)


def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def ease_out_back(x: float, overshoot: float = 1.70158) -> float:
    """0 -> 1, passing ~1.1 on the way (the overshoot) and settling back."""
    x = _clamp(x) - 1.0
    return 1.0 + (overshoot + 1.0) * x ** 3 + overshoot * x ** 2


def ease_in_back(x: float, overshoot: float = 1.70158) -> float:
    """0 -> 1 with a small wind-up below 0 first (anticipation)."""
    x = _clamp(x)
    return (overshoot + 1.0) * x ** 3 - overshoot * x ** 2


def ease_out_cubic(x: float) -> float:
    return 1.0 - (1.0 - _clamp(x)) ** 3


def ease_in_cubic(x: float) -> float:
    return _clamp(x) ** 3


def ease_out_elastic(x: float) -> float:
    x = _clamp(x)
    if x in (0.0, 1.0):
        return x
    return 2 ** (-10 * x) * math.sin((x * 10 - 0.75) * (2 * math.pi) / 3) + 1


def _stagger(g: float, i: int, n: int, spread: float = 0.6) -> float:
    """Progress of item i of n when they start one after another over `spread` of g."""
    if n <= 1:
        return _clamp(g)
    start = spread * i / (n - 1)
    return _clamp((g - start) / max(EPS, 1.0 - spread))


def pop_scale(g: float, out: bool) -> float:
    """In: 0 -> overshoot -> 1. Out (g 1 -> 0): a little swell, then gone."""
    if g >= 1.0:
        return 1.0
    if not out:
        return max(0.0, ease_out_back(g, 2.0))
    return max(0.0, 1.0 - ease_in_back(1.0 - g, 2.0))


def _scaled(p: QPainter, c: QPointF, s: float, rot: float = 0.0) -> None:
    p.translate(c)
    if rot:
        p.rotate(rot)
    p.scale(s, s)
    p.translate(-c.x(), -c.y())


def _pen(color, w, cap=Qt.RoundCap, join=Qt.RoundJoin) -> QPen:
    pen = QPen(QColor(color), max(0.6, w))
    pen.setCapStyle(cap)
    pen.setJoinStyle(join)
    return pen


def _alpha(color, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(_clamp(c.alphaF() * a))
    return c


def _partial(path_pts: list, frac: float) -> QPainterPath:
    """A polyline through the first `frac` of the points."""
    path = QPainterPath()
    n = max(1, int(len(path_pts) * _clamp(frac)))
    for i, (x, y) in enumerate(path_pts[:n]):
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    return path


def glyph_path(text: str, rect: QRectF, weight=QFont.Black, family: str = "Sans Serif") -> QPainterPath:
    """`text` as an outline path, fitted (keeping its shape) and centred in rect."""
    from PySide6.QtGui import QTransform
    f = QFont(family)
    f.setPixelSize(200)
    f.setWeight(weight)
    path = QPainterPath()
    path.addText(QPointF(0, 0), f, text)
    br = path.boundingRect()
    if br.width() <= 0 or br.height() <= 0:
        return path
    k = min(rect.width() / br.width(), rect.height() / br.height())
    tr = QTransform()
    tr.translate(rect.center().x(), rect.center().y())
    tr.scale(k, k)
    tr.translate(-br.center().x(), -br.center().y())
    return tr.map(path)


def heart_path(cx: float, cy: float, s: float) -> QPainterPath:
    """A heart of width ~s centred on (cx, cy)."""
    path = QPainterPath(QPointF(cx, cy + s * 0.42))
    path.cubicTo(cx - s * 0.62, cy + s * 0.02, cx - s * 0.52, cy - s * 0.52, cx, cy - s * 0.2)
    path.cubicTo(cx + s * 0.52, cy - s * 0.52, cx + s * 0.62, cy + s * 0.02, cx, cy + s * 0.42)
    path.closeSubpath()
    return path


def star_path(cx: float, cy: float, r_out: float, r_in: float, points: int = 5, rot: float = -90.0) -> QPainterPath:
    path = QPainterPath()
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(rot + i * 180.0 / points)
        pt = QPointF(cx + r * math.cos(a), cy + r * math.sin(a))
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


def sparkle_path(cx: float, cy: float, r: float) -> QPainterPath:
    """A 4-point twinkle (concave sides)."""
    path = QPainterPath(QPointF(cx, cy - r))
    k = r * 0.18
    for (x, y) in ((cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)):
        path.quadTo(QPointF(cx + (k if x >= cx else -k), cy + (k if y >= cy else -k)), QPointF(x, y))
    path.closeSubpath()
    return path


def cloud_path(c: QPointF, w: float, h: float, lumps: int = 7, seed: int = 0) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    for i in range(lumps):
        a = 2 * math.pi * i / lumps
        rr = h * (0.34 + 0.08 * _rand(seed + i))
        x = c.x() + math.cos(a) * w * 0.32
        y = c.y() + math.sin(a) * h * 0.18
        path.addEllipse(QPointF(x, y), rr * (w / h) ** 0.25, rr)
    path.addEllipse(c, w * 0.34, h * 0.32)
    return path.simplified()


def _cycle(t: float, period: float, offset: float = 0.0) -> float:
    """0..1 sawtooth."""
    return ((t / max(EPS, period)) + offset) % 1.0


# =========================================================================
# the effects
# Each: fn(painter, rect, t, g, out, color). t = idle time (s x speed; 0 when
# still), g = transition progress (1 = fully shown), out = leaving.
# =========================================================================
def fx_scribblenado(p, r, t, g, out, color):
    """Anger: one continuous looping pen stroke narrowing into a funnel,
    spinning; jitter re-seeded 12x/s so it boils. In = winds itself up from
    the top; out = unwinds back up and shrinks."""
    boil = int(math.floor(t * BOIL_FPS))
    cx, top, h, w = r.center().x(), r.top(), r.height(), r.width()
    loops, spl = 11, 26
    spin = t * 2 * math.pi * 1.6 + (1.0 - g) * (3.0 if out else -3.0)
    pts = []
    n = loops * spl
    for i in range(n + 1):
        u = i / n
        a = spin + i / spl * 2 * math.pi
        loop = i // spl
        wild = 0.8 + 0.35 * _rand(boil * 389 + loop * 11)
        rx = w * 0.5 * (1.0 - 0.72 * u ** 1.15) * wild
        ry = h * 0.10 * (1.0 - 0.6 * u) * wild
        k = i // 5
        f = (i % 5) / 5.0
        jx = ((_rand(boil * 977 + k * 31) * (1 - f) + _rand(boil * 977 + (k + 1) * 31) * f) - 0.5) * w * 0.16
        jy = ((_rand(boil * 577 + k * 17 + 5) * (1 - f) + _rand(boil * 577 + (k + 1) * 17 + 5) * f) - 0.5) * h * 0.09
        sway = math.sin(u * math.pi * 1.5 + t * 3.0) * w * 0.06 * u
        pts.append((cx + rx * math.cos(a) + jx + sway, top + h * 0.12 + u * h * 0.76 + ry * math.sin(a) + jy))
    s = 0.55 + 0.45 * ease_out_cubic(g)
    _scaled(p, QPointF(cx, top + h * 0.15), s)
    p.setPen(_pen(color, h * 0.022))
    p.setBrush(Qt.NoBrush)
    p.drawPath(_partial(pts, ease_out_cubic(g)))
    if g > 0.85:
        pen = _pen(color, h * 0.014)
        p.setPen(pen)
        for j in range(3):
            sd = boil * 13 + j * 101
            a0 = spin * 0.5 + j * 2.1
            bx = cx + math.cos(a0) * w * (0.42 + 0.1 * _rand(sd))
            by = top + h * (0.06 + 0.08 * _rand(sd + 1))
            bit = QPainterPath(QPointF(bx, by))
            for m in range(1, 6):
                bit.lineTo(bx + (_rand(sd + m * 3) - 0.5) * w * 0.12, by + (_rand(sd + m * 3 + 1) - 0.5) * h * 0.08)
            p.drawPath(bit)


def _vein_piece() -> list:
    """One arm of the anger mark (in units of the mark's radius, pointing up):
    a thick bent stroke, its bend toward the centre, the two arms reaching
    out -- the left one steeper, so the four together read as a pinwheel."""
    return [((-0.30, -0.97), (-0.18, -0.70), (0.02, -0.50), (0.20, -0.50)),
            ((0.20, -0.50), (0.38, -0.50), (0.58, -0.62), (0.72, -0.78))]


def fx_vein(p, r, t, g, out, color):
    """Anger: the bulging vein mark -- four thick bent strokes around the
    centre. In = pops out (overshoot, a twist); idle = throbs like a pulse;
    out = squeezes back to nothing."""
    c = r.center()
    R = min(r.width(), r.height()) / 2 / 1.1
    beat = (math.sin(t * 2 * math.pi * 1.5) * 0.5 + 0.5) ** 4 if t > 0 else 0.0     # short, sharp swells
    s = pop_scale(g, out) * (1.0 + 0.13 * beat)
    rot = -(1.0 - g) * 35.0 if not out else (1.0 - g) * 25.0
    _scaled(p, c, s, rot)
    pen = QPen(QColor(color), R * (0.21 + 0.03 * beat))
    pen.setCapStyle(Qt.FlatCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for q in range(4):
        a = q * math.pi / 2
        ca, sa = math.cos(a), math.sin(a)

        def P(x, y):
            return QPointF(c.x() + (x * ca - y * sa) * R, c.y() + (x * sa + y * ca) * R)
        segs = _vein_piece()
        path = QPainterPath(P(*segs[0][0]))
        for s0, c1, c2, e in segs:
            path.cubicTo(P(*c1), P(*c2), P(*e))
        p.drawPath(path)


def fx_wiggle_lines(p, r, t, g, out, color):
    """Unease: wavy lines radiating out over the top three-quarters of a
    circle (an empty middle for the head), waves crawling outward. In = the
    lines grow out from the head one after another; out = they pull back in."""
    c = r.center()
    R = min(r.width(), r.height()) / 2
    r0, r1 = R * 0.42, R * 0.98
    count = 11
    start, end = math.radians(-215), math.radians(35)
    p.setPen(_pen(color, R * 0.045))
    p.setBrush(Qt.NoBrush)
    for i in range(count):
        gi = _stagger(g, i if not out else count - 1 - i, count, 0.5)
        if gi <= 0.0:
            continue
        ang = start + (end - start) * i / (count - 1)
        length_k = (0.8 + 0.2 * _rand(i * 7 + 3)) * ease_out_cubic(gi)
        dx, dy = math.cos(ang), math.sin(ang)
        nx, ny = -dy, dx
        path = QPainterPath()
        steps = 24
        amp = R * 0.055
        for s in range(steps + 1):
            u = s / steps
            d = r0 + (r1 - r0) * u * length_k
            wob = amp * math.sin(u * length_k * 3.2 * 2 * math.pi - t * 2 * math.pi * 1.8 + i * 1.3) * (0.35 + 0.65 * u)
            x, y = c.x() + dx * d + nx * wob, c.y() + dy * d + ny * wob
            if s == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.drawPath(path)


def fx_steam(p, r, t, g, out, color):
    """Fuming: puffs of steam jetting up out of both sides, swelling and
    fading as they rise. In = the jets start; out = they die down."""
    w, h = r.width(), r.height()
    outline = QColor(color).darker(170)
    jets = [(r.left() + w * 0.18, -1), (r.right() - w * 0.18, 1)]
    for side_i, (x0, side) in enumerate(jets):
        for k in range(4):
            life = _cycle(t + 0.11 * side_i, 1.1, k / 4.0)
            a = (1.0 - life) ** 0.8 * _clamp(g * 1.4 - k * 0.1)
            if a <= 0.01:
                continue
            size = h * (0.10 + 0.22 * life) * (0.6 + 0.4 * g)
            cx = x0 + side * w * 0.22 * life ** 0.8
            cy = r.bottom() - h * 0.18 - h * 0.62 * life
            puff = cloud_path(QPointF(cx, cy), size * 1.3, size, 5, k * 7 + side_i)
            p.setPen(_pen(_alpha(outline, a), h * 0.012))
            p.setBrush(_alpha(color, a))
            p.drawPath(puff)


def fx_sweatdrop(p, r, t, g, out, color):
    """Awkward: the big anime sweat drop. In = slides down into place; idle =
    creeps down a little and wobbles; out = slides away down and fades."""
    w, h = r.width(), r.height()
    slide = (1.0 - ease_out_cubic(g)) * (-0.35 if not out else 0.35) * h
    creep = (math.sin(t * 1.4) * 0.5 + 0.5) * 0.05 * h if t > 0 else 0.0
    cx = r.center().x()
    top = r.top() + h * 0.06 + slide + creep
    bw = w * 0.82
    bh = h * 0.86
    squash = 1.0 + 0.04 * math.sin(t * 5.0) if t > 0 else 1.0
    path = QPainterPath(QPointF(cx, top))
    path.cubicTo(cx + bw * 0.12, top + bh * 0.28, cx + bw * 0.5 * squash, top + bh * 0.48,
                 cx + bw * 0.5 * squash, top + bh * 0.68)
    path.arcTo(QRectF(cx - bw * 0.5 * squash, top + bh * 0.36, bw * squash, bh * 0.64), 0, -180)
    path.cubicTo(cx - bw * 0.5 * squash, top + bh * 0.48, cx - bw * 0.12, top + bh * 0.28, cx, top)
    a = _clamp(g * 1.5)
    p.setPen(_pen(_alpha(QColor(color).darker(160), a), w * 0.05))
    p.setBrush(_alpha(color, a))
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(_alpha(QColor(255, 255, 255, 220), a))
    p.drawEllipse(QRectF(cx - bw * 0.30, top + bh * 0.55, bw * 0.16, bh * 0.22))


def fx_sweat(p, r, t, g, out, color):
    """Nervous: little drops springing off in arcs, again and again."""
    c = QPointF(r.center().x(), r.top() + r.height() * 0.62)
    w, h = r.width(), r.height()
    for k in range(5):
        life = _cycle(t, 0.9, k / 5.0)
        side = -1 if k % 2 == 0 else 1
        spread = 0.25 + 0.2 * _rand(k * 13)
        x = c.x() + side * w * spread * life
        y = c.y() - h * 0.45 * life + h * 0.55 * life * life
        a = math.sin(life * math.pi) * _clamp(g * 1.4)
        if a <= 0.02:
            continue
        s = h * 0.26 * (0.7 + 0.3 * _rand(k))
        ang = math.degrees(math.atan2(-0.45 + 1.1 * life, side * spread)) - 90
        p.save()
        p.translate(x, y)
        p.rotate(ang)
        drop = QPainterPath(QPointF(0, -s * 0.6))
        drop.quadTo(QPointF(s * 0.42, s * 0.05), QPointF(0, s * 0.4))
        drop.quadTo(QPointF(-s * 0.42, s * 0.05), QPointF(0, -s * 0.6))
        p.setPen(_pen(_alpha(QColor(color).darker(160), a), s * 0.12))
        p.setBrush(_alpha(color, a))
        p.drawPath(drop)
        p.restore()


def fx_gloom(p, r, t, g, out, color):
    """Gloomy / depressed: the manga curtain of vertical lines hanging down,
    fading toward the bottom. In = the lines drop down one by one; out =
    they're pulled back up."""
    n = 17
    w, h = r.width(), r.height()
    for i in range(n):
        gi = _stagger(g, i if not out else n - 1 - i, n, 0.55)
        if gi <= 0:
            continue
        x = r.left() + w * (i + 0.5) / n + (_rand(i * 5) - 0.5) * w / n * 0.5
        breathe = 0.04 * math.sin(t * 1.3 + i) if t > 0 else 0.0
        length = h * (0.45 + 0.5 * _rand(i * 11 + 2) + breathe) * ease_out_cubic(gi)
        grad = QLinearGradient(QPointF(x, r.top()), QPointF(x, r.top() + max(1.0, length)))
        grad.setColorAt(0.0, QColor(color))
        grad.setColorAt(1.0, _alpha(color, 0.0))
        pen = QPen(QBrush(grad), max(0.8, w * (0.008 + 0.01 * _rand(i * 3))))
        pen.setCapStyle(Qt.FlatCap)
        p.setPen(pen)
        p.drawLine(QPointF(x, r.top()), QPointF(x, r.top() + length))


def fx_raincloud(p, r, t, g, out, color):
    """Sad: a little rain cloud raining. In = the cloud puffs up, then the
    rain starts; out = the rain stops and the cloud shrinks away."""
    w, h = r.width(), r.height()
    cloud_g = _clamp(g / 0.6) if not out else _clamp((g - 0.4) / 0.6)
    rain_a = _clamp((g - 0.5) / 0.5) if not out else _clamp(g / 0.5)
    cc = QPointF(r.center().x(), r.top() + h * 0.24)
    if rain_a > 0.01:
        rain = QColor("#4dabf7")
        p.setPen(_pen(_alpha(rain, rain_a), h * 0.018))
        for k in range(14):
            x0 = r.left() + w * (0.18 + 0.64 * ((k * 0.618) % 1.0))
            life = _cycle(t, 0.55, _rand(k * 7))
            y0 = r.top() + h * 0.38 + h * 0.55 * life
            p.drawLine(QPointF(x0 - w * 0.02 * life, y0), QPointF(x0 - w * 0.035, y0 + h * 0.08))
    if cloud_g > 0.01:
        p.save()
        _scaled(p, cc, pop_scale(cloud_g, out) * (1.0 + 0.02 * math.sin(t * 1.7)))
        p.setPen(_pen(QColor(color).darker(150), h * 0.018))
        p.setBrush(QColor(color))
        p.drawPath(cloud_path(cc, w * 0.86, h * 0.34, 7, 3))
        p.restore()


def fx_dots(p, r, t, g, out, color):
    """Bored / awkward silence: "..." -- the dots pop in one at a time, bob
    in a wave, and vanish in reverse."""
    w, h = r.width(), r.height()
    rad = min(w / 9.0, h * 0.4)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    for i in range(3):
        gi = _stagger(g, i if not out else 2 - i, 3, 0.66)
        s = pop_scale(gi, out)
        if s <= 0.01:
            continue
        bob = -math.sin(t * 4.0 - i * 0.9) * h * 0.12 if t > 0 else 0.0
        x = r.left() + w * (0.2 + 0.3 * i)
        y = r.center().y() + bob
        p.drawEllipse(QPointF(x, y), rad * s, rad * s)


def fx_zzz(p, r, t, g, out, color):
    """Bored / sleepy: "Z z z" floating up and drifting, growing as they go,
    fading out at the top."""
    w, h = r.width(), r.height()
    outline = QColor(color).darker(220) if QColor(color).lightness() > 128 else QColor(255, 255, 255, 200)
    for k in range(3):
        life = _cycle(t, 2.4, k / 3.0) if t > 0 else (k + 0.5) / 3.0
        a = math.sin(life * math.pi) ** 0.6 * _clamp(g * 1.3 - k * 0.15)
        if a <= 0.02:
            continue
        size = h * (0.16 + 0.22 * life)
        x = r.left() + w * (0.15 + 0.6 * life) + math.sin(life * 6 + k) * w * 0.05
        y = r.bottom() - h * 0.1 - h * 0.7 * life
        path = glyph_path("Z", QRectF(x - size / 2, y - size / 2, size, size))
        p.setPen(_pen(_alpha(outline, a), size * 0.07))
        p.setBrush(_alpha(color, a))
        p.drawPath(path)


def _burst_lines(p, c, r_in, r_out, n, color, width, start=-90.0, span=360.0, a=1.0):
    p.setPen(_pen(_alpha(color, a), width))
    for i in range(n):
        ang = math.radians(start + span * (i + 0.5) / n)
        p.drawLine(QPointF(c.x() + math.cos(ang) * r_in, c.y() + math.sin(ang) * r_in),
                   QPointF(c.x() + math.cos(ang) * r_out, c.y() + math.sin(ang) * r_out))


def _glyph_mark(p, r, t, g, out, color, text, rock=0.0, shake=0.0, lines=True):
    w, h = r.width(), r.height()
    c = r.center()
    boil = int(t * BOIL_FPS)
    jx = (_rand(boil * 3) - 0.5) * w * shake if t > 0 else 0.0
    jy = (_rand(boil * 3 + 1) - 0.5) * h * shake if t > 0 else 0.0
    if lines and g > 0.3:
        lg = _clamp((g - 0.3) / 0.7)
        ln = ease_out_cubic(lg)
        R = min(w, h) / 2
        for side in (-1, 1):
            _burst_lines(p, c, R * 0.62, R * (0.62 + 0.3 * ln), 3, color, R * 0.06,
                         start=(180 if side < 0 else 0) - 35, span=70, a=lg)
    p.save()
    rot = rock * math.sin(t * 2.2) if t > 0 else 0.0
    p.translate(jx, jy)                       # the quiver
    _scaled(p, c, pop_scale(g, out), rot + (1.0 - g) * (-20 if not out else 20))
    path = glyph_path(text, QRectF(r.left() + w * 0.22, r.top() + h * 0.08, w * 0.56, h * 0.84))
    p.setPen(_pen(QColor(color).darker(200), h * 0.03))
    p.setBrush(QColor(color))
    p.drawPath(path)
    p.restore()


def fx_exclaim(p, r, t, g, out, color):
    """Surprise: a big "!" popping in with little pop lines, quivering."""
    _glyph_mark(p, r, t, g, out, color, "!", shake=0.025)


def fx_question(p, r, t, g, out, color):
    """Confused: a "?" popping in, then rocking back and forth."""
    _glyph_mark(p, r, t, g, out, color, "?", rock=10.0, lines=False)


def fx_interrobang(p, r, t, g, out, color):
    """Shocked & confused: "!?" """
    _glyph_mark(p, r, t, g, out, color, "!?", rock=6.0, shake=0.012)


def fx_shock(p, r, t, g, out, color):
    """Surprise lines: short straight lines bursting out in a ring around a
    head. In = they shoot out from the middle; idle = they flicker; out =
    they fly off and fade."""
    c = r.center()
    R = min(r.width(), r.height()) / 2
    boil = int(t * BOIL_FPS)
    n = 14
    for i in range(n):
        gi = _stagger(g, i % 4, 4, 0.4)
        if gi <= 0:
            continue
        ang = 2 * math.pi * i / n + 0.12 * (_rand(i * 9) - 0.5)
        jit = 0.06 * (_rand(boil * 31 + i) - 0.5) if t > 0 else 0.0
        if not out:
            rin = R * (0.25 + 0.4 * ease_out_back(gi) + jit)
            a = 1.0
        else:
            rin = R * (0.65 + 0.35 * (1.0 - gi) + jit)
            a = gi
        rout = rin + R * (0.25 + 0.1 * _rand(i * 5))
        p.setPen(_pen(_alpha(color, a), R * 0.05))
        p.drawLine(QPointF(c.x() + math.cos(ang) * rin, c.y() + math.sin(ang) * rin),
                   QPointF(c.x() + math.cos(ang) * min(rout, R), c.y() + math.sin(ang) * min(rout, R)))


def fx_spiral(p, r, t, g, out, color):
    """Dizzy: a spinning spiral. In = it winds out from the middle; out = it
    winds back in."""
    c = r.center()
    R = min(r.width(), r.height()) / 2 * 0.95
    turns = 3.2
    pts = []
    rot = t * 2 * math.pi * 0.9
    for i in range(241):
        u = i / 240
        a = rot + u * turns * 2 * math.pi
        rr = R * u
        pts.append((c.x() + rr * math.cos(a), c.y() + rr * math.sin(a)))
    p.setPen(_pen(color, R * 0.08))
    p.setBrush(Qt.NoBrush)
    p.drawPath(_partial(pts, g))


def fx_dizzy_stars(p, r, t, g, out, color):
    """Dizzy / knocked out: little stars orbiting around a head, the far ones
    smaller and behind."""
    c = r.center()
    w, h = r.width(), r.height()
    spread = ease_out_back(g, 1.2) if not out else g
    stars = []
    for k in range(4):
        a = t * 2 * math.pi * 0.7 + k * math.pi / 2
        depth = math.sin(a)
        stars.append((depth, c.x() + math.cos(a) * w * 0.42 * spread, c.y() + depth * h * 0.28 * spread))
    for depth, x, y in sorted(stars):
        s = min(w, h) * 0.13 * (0.75 + 0.3 * (depth + 1) / 2) * _clamp(g * 2)
        p.setPen(_pen(QColor(color).darker(170), s * 0.15))
        p.setBrush(QColor(color))
        p.drawPath(star_path(x, y, s, s * 0.45, 5, -90 + t * 120 + depth * 10))


def fx_hearts(p, r, t, g, out, color):
    """In love: hearts floating up, swaying, swelling then fading."""
    w, h = r.width(), r.height()
    outline = QColor(color).darker(160)
    for k in range(5):
        life = _cycle(t, 2.2, k / 5.0) if t > 0 else (k + 0.5) / 5.0
        a = min(1.0, life * 5.0, (1.0 - life) * 3.0) * _clamp(g * 1.5 - k * 0.12)
        if a <= 0.02:
            continue
        s = w * (0.16 + 0.12 * math.sin(life * math.pi)) * (0.8 + 0.4 * _rand(k * 3))
        x = r.left() + w * (0.2 + 0.6 * _rand(k * 7 + 1)) + math.sin(life * 7 + k) * w * 0.07
        y = r.bottom() - h * 0.12 - h * 0.76 * life
        p.setPen(_pen(_alpha(outline, a), s * 0.07))
        p.setBrush(_alpha(color, a))
        p.drawPath(heart_path(x, y, s))


def fx_heartbeat(p, r, t, g, out, color):
    """Smitten: one big heart thumping lub-dub."""
    c = r.center()
    s0 = min(r.width(), r.height()) * 1.05
    ph = (t * 1.2) % 1.0 if t > 0 else 0.5
    beat = math.exp(-((ph - 0.1) / 0.05) ** 2) + 0.7 * math.exp(-((ph - 0.28) / 0.05) ** 2)
    s = pop_scale(g, out) * (1.0 + 0.12 * beat)
    _scaled(p, c, s)
    p.setPen(_pen(QColor(color).darker(160), s0 * 0.05))
    p.setBrush(QColor(color))
    p.drawPath(heart_path(c.x(), c.y() + s0 * 0.04, s0 * 0.9))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 150))
    p.drawEllipse(QRectF(c.x() - s0 * 0.3, c.y() - s0 * 0.22, s0 * 0.14, s0 * 0.1))


def _note(p, x, y, s, color, double=False):
    p.setPen(_pen(color, s * 0.09, Qt.FlatCap))
    p.setBrush(color)
    heads = [(x, y)] if not double else [(x - s * 0.32, y + s * 0.1), (x + s * 0.32, y)]
    for hx, hy in heads:
        p.save()
        p.translate(hx, hy)
        p.rotate(-22)
        p.drawEllipse(QPointF(0, 0), s * 0.2, s * 0.14)
        p.restore()
        p.drawLine(QPointF(hx + s * 0.17, hy - s * 0.05), QPointF(hx + s * 0.17, hy - s * 0.75))
    if double:
        beam = QPainterPath(QPointF(heads[0][0] + s * 0.17, heads[0][1] - s * 0.75))
        beam.lineTo(heads[1][0] + s * 0.17, heads[1][1] - s * 0.75)
        p.setPen(_pen(color, s * 0.16, Qt.FlatCap))
        p.drawPath(beam)
    else:
        flag = QPainterPath(QPointF(x + s * 0.17, y - s * 0.75))
        flag.cubicTo(x + s * 0.45, y - s * 0.55, x + s * 0.5, y - s * 0.4, x + s * 0.36, y - s * 0.22)
        p.setPen(_pen(color, s * 0.09))
        p.setBrush(Qt.NoBrush)
        p.drawPath(flag)


def fx_music(p, r, t, g, out, color):
    """Happy / humming: music notes bobbing up and away."""
    w, h = r.width(), r.height()
    for k in range(4):
        life = _cycle(t, 2.6, k / 4.0) if t > 0 else (k + 0.5) / 4.0
        a = min(1.0, life * 4.0, (1.0 - life) * 3.0) * _clamp(g * 1.5 - k * 0.12)
        if a <= 0.02:
            continue
        s = h * 0.3
        x = r.left() + w * (0.15 + 0.55 * life) + math.sin(life * 9 + k * 2) * w * 0.06
        y = r.bottom() - h * 0.12 - h * 0.6 * life + math.sin(t * 6 + k) * h * 0.02
        _note(p, x, y, s, _alpha(color, a), double=(k % 2 == 1))


def fx_sparkles(p, r, t, g, out, color):
    """Shiny / cool / clean: four-point twinkles blinking in turn."""
    w, h = r.width(), r.height()
    spots = [(0.25, 0.3, 1.0), (0.72, 0.22, 0.7), (0.6, 0.7, 0.85), (0.2, 0.75, 0.55), (0.88, 0.55, 0.5)]
    for k, (u, v, sz) in enumerate(spots):
        gi = _stagger(g, k if not out else len(spots) - 1 - k, len(spots), 0.5)
        if gi <= 0:
            continue
        tw = (0.55 + 0.45 * math.sin(t * 4.0 + k * 1.7)) if t > 0 else 1.0
        s = min(w, h) * 0.22 * sz * tw * pop_scale(gi, out)
        if s <= 0.5:
            continue
        x, y = r.left() + w * u, r.top() + h * v
        glow = QRadialGradient(QPointF(x, y), s * 1.4)
        glow.setColorAt(0.0, _alpha(color, 0.55))
        glow.setColorAt(1.0, _alpha(color, 0.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(QPointF(x, y), s * 1.4, s * 1.4)
        p.save()
        p.translate(x, y)
        p.rotate(t * 30 * (1 if k % 2 else -1))
        p.setPen(_pen(QColor(color).darker(140), s * 0.06))
        p.setBrush(QColor(color))
        p.drawPath(sparkle_path(0, 0, s))
        p.restore()


def fx_bulb(p, r, t, g, out, color):
    """Idea!: a light bulb pops up and lights, its rays pulsing."""
    w, h = r.width(), r.height()
    c = QPointF(r.center().x(), r.top() + h * 0.42)
    R = min(w, h * 0.8) * 0.3
    bulb_g = _clamp(g / 0.6) if not out else g
    lit = _clamp((g - 0.5) / 0.5) if not out else _clamp((g - 0.3) / 0.7)
    pulse = 0.5 + 0.5 * math.sin(t * 5.0) if t > 0 else 1.0
    if lit > 0.01:
        glow = QRadialGradient(c, R * 2.2)
        glow.setColorAt(0.0, _alpha(color, 0.65 * lit))
        glow.setColorAt(1.0, _alpha(color, 0.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(glow))
        p.drawEllipse(c, R * 2.2, R * 2.2)
        _burst_lines(p, c, R * 1.3, R * (1.65 + 0.25 * pulse), 7, QColor(color).darker(115), R * 0.12,
                     start=-180, span=180, a=lit)
    p.save()
    _scaled(p, QPointF(c.x(), c.y() + R), pop_scale(bulb_g, out))
    glass = QColor(color) if lit > 0.5 else QColor("#f1f3f5")
    p.setPen(_pen(INK, R * 0.08))
    p.setBrush(glass)
    body = QPainterPath()
    body.addEllipse(c, R, R)
    neck = QPainterPath(QPointF(c.x() - R * 0.55, c.y() + R * 0.8))
    neck.lineTo(c.x() - R * 0.38, c.y() + R * 1.35)
    neck.lineTo(c.x() + R * 0.38, c.y() + R * 1.35)
    neck.lineTo(c.x() + R * 0.55, c.y() + R * 0.8)
    neck.closeSubpath()
    p.drawPath(body.united(neck))
    p.setBrush(QColor("#adb5bd"))
    base = QRectF(c.x() - R * 0.42, c.y() + R * 1.32, R * 0.84, R * 0.5)
    p.drawRoundedRect(base, R * 0.12, R * 0.12)
    p.setPen(_pen(INK, R * 0.05))
    for k in range(2):
        yy = base.top() + base.height() * (0.33 + 0.33 * k)
        p.drawLine(QPointF(base.left(), yy), QPointF(base.right(), yy))
    fil = QPainterPath(QPointF(c.x() - R * 0.3, c.y() + R * 0.75))
    fil.lineTo(c.x() - R * 0.25, c.y() + R * 0.1)
    fil.cubicTo(c.x() - R * 0.1, c.y() - R * 0.2, c.x() + R * 0.1, c.y() + R * 0.3, c.x() + R * 0.25, c.y() + R * 0.1)
    fil.lineTo(c.x() + R * 0.3, c.y() + R * 0.75)
    p.setBrush(Qt.NoBrush)
    p.setPen(_pen(QColor("#e67700") if lit > 0.5 else INK, R * 0.07))
    p.drawPath(fil)
    p.restore()


def fx_blush(p, r, t, g, out, color):
    """Embarrassed: two pink cheek ovals with diagonal blush hatching."""
    w, h = r.width(), r.height()
    a = ease_out_cubic(g)
    shimmer = 0.85 + 0.15 * math.sin(t * 3.0) if t > 0 else 1.0
    for side in (-1, 1):
        cx = r.center().x() + side * w * 0.3
        cy = r.center().y()
        ow, oh = w * 0.36 * (0.7 + 0.3 * a), h * 0.62 * (0.7 + 0.3 * a)
        grad = QRadialGradient(QPointF(cx, cy), ow / 2)
        grad.setColorAt(0.0, _alpha(color, 0.75 * a))
        grad.setColorAt(1.0, _alpha(color, 0.0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), ow / 2, oh / 2)
        p.setPen(_pen(_alpha(QColor(color).darker(150), a * shimmer), h * 0.06))
        for k in range(4):
            x = cx - ow * 0.3 + ow * 0.2 * k
            p.drawLine(QPointF(x - ow * 0.06, cy + oh * 0.2), QPointF(x + ow * 0.06, cy - oh * 0.2))


def _thumb(p, r, color):
    """A thumbs-up hand in r (fist with fingers toward the viewer, sleeve cuff)."""
    w, h = r.width(), r.height()
    X = lambda u: r.left() + w * u     # noqa: E731
    Y = lambda v: r.top() + h * v      # noqa: E731
    outline = QColor(color).darker(260)
    lw = h * 0.035
    p.setPen(_pen(outline, lw))
    p.setBrush(QColor("#4c6ef5"))
    p.drawRoundedRect(QRectF(X(0.30), Y(0.86), w * 0.44, h * 0.13), h * 0.03, h * 0.03)
    p.setBrush(QColor(color))
    thumb = QPainterPath()
    thumb.addRoundedRect(QRectF(X(0.22), Y(0.04), w * 0.26, h * 0.52), w * 0.13, w * 0.13)
    fist = QPainterPath()
    fist.addRoundedRect(QRectF(X(0.16), Y(0.40), w * 0.68, h * 0.50), h * 0.12, h * 0.12)
    p.drawPath(fist.united(thumb))
    for k in range(3):
        y = Y(0.53 + 0.11 * k)
        seg = QPainterPath(QPointF(X(0.86), y))
        seg.quadTo(QPointF(X(0.6), y - h * 0.015), QPointF(X(0.44), y + h * 0.01))
        p.setBrush(Qt.NoBrush)
        p.drawPath(seg)
    nail = QPainterPath()
    nail.addRoundedRect(QRectF(X(0.27), Y(0.08), w * 0.13, h * 0.11), w * 0.05, w * 0.05)
    p.setBrush(QColor(255, 255, 255, 110))
    p.setPen(_pen(outline, lw * 0.6))
    p.drawPath(nail)


def fx_thumbs_up(p, r, t, g, out, color):
    """Thumbs up: springs up with a twist, then gives little pumps."""
    c = QPointF(r.center().x(), r.bottom())
    pump = (math.sin(t * 2 * math.pi * 1.1) * 0.5 + 0.5) ** 3 if t > 0 else 0.0
    rot = (1.0 - g) * (-45 if not out else 30) - 6 * pump
    p.translate(0, -pump * r.height() * 0.05)
    _scaled(p, c, pop_scale(g, out), rot)
    _thumb(p, r, color)


def fx_thumbs_down(p, r, t, g, out, color):
    """Thumbs down: drops in turning over, then wags disapprovingly."""
    c = r.center()
    wag = math.sin(t * 2 * math.pi * 1.3) * 8 if t > 0 else 0.0
    rot = 180 + (1.0 - g) * (45 if not out else -30) + wag
    _scaled(p, c, pop_scale(g, out), rot)
    _thumb(p, r, color)


def fx_skull(p, r, t, g, out, color):
    """Dead / cringe: a skull floating, its jaw chattering a laugh."""
    w, h = r.width(), r.height()
    bob = math.sin(t * 2.0) * h * 0.03 if t > 0 else 0.0
    laughing = t > 0 and math.sin(t * 0.9) > 0.3
    chatter = max(0.0, math.sin(t * 2 * math.pi * 3.0)) * h * 0.035 if laughing else 0.0
    p.translate(0, bob)
    _scaled(p, r.center(), pop_scale(g, out), (1.0 - g) * (-25 if not out else 25))
    lw = h * 0.035
    p.setPen(_pen(INK, lw))
    p.setBrush(QColor(color))
    jaw = QRectF(r.left() + w * 0.3, r.top() + h * 0.66 + chatter, w * 0.4, h * 0.26)
    p.drawRoundedRect(jaw, w * 0.08, w * 0.08)
    for k in range(1, 4):
        x = jaw.left() + jaw.width() * k / 4
        p.drawLine(QPointF(x, jaw.top() + jaw.height() * 0.1), QPointF(x, jaw.bottom() - jaw.height() * 0.15))
    cran = QPainterPath()
    cran.addEllipse(QRectF(r.left() + w * 0.12, r.top() + h * 0.04, w * 0.76, h * 0.62))
    cheek = QPainterPath()
    cheek.addRoundedRect(QRectF(r.left() + w * 0.24, r.top() + h * 0.45, w * 0.52, h * 0.3), w * 0.1, w * 0.1)
    p.drawPath(cran.united(cheek))
    p.setPen(Qt.NoPen)
    p.setBrush(INK)
    for side in (-1, 1):
        p.drawEllipse(QPointF(r.center().x() + side * w * 0.17, r.top() + h * 0.42), w * 0.11, h * 0.1)
    nose = QPainterPath(QPointF(r.center().x(), r.top() + h * 0.53))
    nose.lineTo(r.center().x() - w * 0.05, r.top() + h * 0.63)
    nose.lineTo(r.center().x() + w * 0.05, r.top() + h * 0.63)
    nose.closeSubpath()
    p.drawPath(nose)


def _flame(cx, base, w, hgt, sway) -> QPainterPath:
    path = QPainterPath(QPointF(cx - w / 2, base))
    path.cubicTo(cx - w * 0.6, base - hgt * 0.45, cx - w * 0.1 + sway * 0.3, base - hgt * 0.6, cx + sway, base - hgt)
    path.cubicTo(cx + w * 0.15 + sway * 0.3, base - hgt * 0.55, cx + w * 0.62, base - hgt * 0.45, cx + w / 2, base)
    path.quadTo(QPointF(cx, base + w * 0.25), QPointF(cx - w / 2, base))
    return path


def fx_fire(p, r, t, g, out, color):
    """Fired up / furious: flickering flames. In = they flare up from the
    bottom; out = they die down."""
    w, h = r.width(), r.height()
    grow = ease_out_back(g, 1.3) if not out else ease_out_cubic(g)
    inner = QColor("#ffd43b")
    base = r.bottom() - h * 0.06
    tongues = [(0.5, 0.95, 0.55), (0.28, 0.62, 0.36), (0.72, 0.7, 0.36), (0.38, 0.8, 0.3), (0.62, 0.78, 0.3)]
    for layer, (col, k_size) in enumerate(((QColor(color), 1.0), (inner, 0.62))):
        p.setPen(Qt.NoPen if layer else _pen(QColor(color).darker(150), h * 0.015))
        p.setBrush(col)
        for i, (u, hh, ww) in enumerate(tongues):
            fl = 0.85 + 0.15 * math.sin(t * 9.0 + i * 1.7) + 0.08 * math.sin(t * 17.0 + i) if t > 0 else 1.0
            sway = (math.sin(t * 5.0 + i * 2.1) * w * 0.06) if t > 0 else 0.0
            p.drawPath(_flame(r.left() + w * u, base, w * ww * k_size, h * hh * fl * k_size * grow, sway * k_size))


def fx_focus_lines(p, r, t, g, out, color):
    """Manga focus / concentration lines: dark wedges rushing in from the
    edges toward a clear oval in the middle. In = they rush in; idle = they
    flicker; out = they pull back out. Make it as big as the frame."""
    c = r.center()
    w, h = r.width(), r.height()
    diag = math.hypot(w, h) / 2
    boil = int(t * BOIL_FPS)
    reach = ease_out_cubic(g) if not out else g
    n = 96
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    for i in range(n):
        ang = 2 * math.pi * (i + 0.6 * (_rand(boil * 41 + i) - 0.5)) / n
        rin_k = 0.62 + 0.38 * _rand(boil * 23 + i * 7)
        rin_x, rin_y = w * 0.5 * rin_k, h * 0.5 * rin_k
        inner = 1.0 / math.sqrt((math.cos(ang) / rin_x) ** 2 + (math.sin(ang) / rin_y) ** 2)
        inner = diag - (diag - inner) * reach
        half = math.pi / n * (0.25 + 0.5 * _rand(i * 13 + boil))
        o1 = QPointF(c.x() + math.cos(ang - half) * diag * 1.05, c.y() + math.sin(ang - half) * diag * 1.05)
        o2 = QPointF(c.x() + math.cos(ang + half) * diag * 1.05, c.y() + math.sin(ang + half) * diag * 1.05)
        tip = QPointF(c.x() + math.cos(ang) * inner, c.y() + math.sin(ang) * inner)
        wedge = QPainterPath(o1)
        wedge.lineTo(tip)
        wedge.lineTo(o2)
        wedge.closeSubpath()
        p.drawPath(wedge)


def fx_speed_lines(p, r, t, g, out, color):
    """Speed / whoosh: streaks racing past horizontally."""
    w, h = r.width(), r.height()
    a = ease_out_cubic(g)
    p.setPen(Qt.NoPen)
    for k in range(26):
        y = r.top() + h * ((k * 0.618 + 0.1) % 1.0)
        L = w * (0.18 + 0.35 * _rand(k * 9))
        if t > 0:
            x = r.right() + L - (w + 2 * L) * _cycle(t, 0.5 + 0.4 * _rand(k), _rand(k * 3))
        else:
            x = r.left() + w * _rand(k * 5)
        th = h * (0.006 + 0.014 * _rand(k * 7))
        streak = QPainterPath(QPointF(x, y))
        streak.lineTo(x + L, y - th)
        streak.lineTo(x + L, y + th)
        streak.closeSubpath()
        p.setBrush(_alpha(color, a * (0.5 + 0.5 * _rand(k * 11))))
        p.drawPath(streak)


def burst_path(c: QPointF, rx: float, ry: float, spikes: int, seed: int, depth: float = 0.35) -> QPainterPath:
    path = QPainterPath()
    for i in range(spikes * 2):
        a = 2 * math.pi * i / (spikes * 2) + 0.15 * (_rand(seed + i * 3) - 0.5)
        k = 1.0 if i % 2 == 0 else (1.0 - depth) * (0.85 + 0.3 * _rand(seed + i))
        if i % 2 == 0:
            k *= 0.85 + 0.25 * _rand(seed + i * 7)
        pt = QPointF(c.x() + math.cos(a) * rx * k, c.y() + math.sin(a) * ry * k)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    return path


def fx_impact(p, r, t, g, out, color):
    """Impact / POW burst (without words): a jagged starburst exploding out,
    its points boiling."""
    c = r.center()
    boil = int(t * 8.0)
    s = pop_scale(g, out)
    rx, ry = r.width() / 2 * s, r.height() / 2 * s
    pen = _pen(QColor("#e03131"), min(rx, ry) * 0.07, join=Qt.MiterJoin)
    pen.setMiterLimit(2.0)
    p.setPen(pen)
    p.setBrush(QColor(color))
    p.drawPath(burst_path(c, rx, ry, 13, boil * 17 + 3))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ff922b"))
    p.drawPath(burst_path(c, rx * 0.55, ry * 0.55, 11, boil * 29 + 7, 0.3))


def fx_poof(p, r, t, g, out, color):
    """Poof! a puff of smoke bursting out, churning, then thinning away."""
    c = r.center()
    w, h = r.width(), r.height()
    spread = ease_out_back(g, 1.4) if not out else 1.0 + (1.0 - g) * 0.4
    a = 1.0
    p.setOpacity(p.opacity() * (1.0 if not out else g))       # fades as one cloud, not lump by lump
    outline = QColor(color).darker(150)
    for k in range(8):
        ang = 2 * math.pi * k / 8 + 0.3 * _rand(k)
        churn = math.sin(t * 2.0 + k) * 0.04 if t > 0 else 0.0
        d = (0.28 + 0.06 * _rand(k * 3) + churn) * spread
        x = c.x() + math.cos(ang) * w * d
        y = c.y() + math.sin(ang) * h * d
        rr = min(w, h) * (0.17 + 0.06 * _rand(k * 5)) * min(1.0, spread)
        p.setPen(_pen(_alpha(outline, a), h * 0.015))
        p.setBrush(_alpha(color, a))
        p.drawEllipse(QPointF(x, y), rr, rr)
    p.setPen(Qt.NoPen)
    p.setBrush(_alpha(color, a))
    p.drawEllipse(c, w * 0.28 * min(1.0, spread), h * 0.26 * min(1.0, spread))


# =========================================================================
# registry: key -> (label, group, function, width/height, default color, default size)
# =========================================================================
EFFECTS = {
    "scribblenado": ("Scribblenado", "Anger", fx_scribblenado, 0.95, "#161616", 0.24),
    "vein": ("Anger vein", "Anger", fx_vein, 1.0, "#ad2a38", 0.12),
    "steam": ("Fuming steam", "Anger", fx_steam, 1.3, "#f8f9fa", 0.25),
    "fire": ("Flames", "Anger", fx_fire, 1.1, "#ff6b00", 0.3),
    "wiggle_lines": ("Unease lines", "Unease", fx_wiggle_lines, 1.0, "#2f3b55", 0.3),
    "sweatdrop": ("Sweat drop", "Unease", fx_sweatdrop, 0.6, "#74c0fc", 0.14),
    "sweat": ("Nervous sweat", "Unease", fx_sweat, 1.4, "#74c0fc", 0.2),
    "gloom": ("Gloom lines", "Sad", fx_gloom, 1.2, "#3b3b6d", 0.35),
    "raincloud": ("Rain cloud", "Sad", fx_raincloud, 1.1, "#868e96", 0.3),
    "dots": ("… (dots)", "Bored", fx_dots, 2.4, "#1a1a1a", 0.08),
    "zzz": ("Zzz", "Bored", fx_zzz, 1.0, "#f8f9fa", 0.25),
    "exclaim": ("!", "Surprise", fx_exclaim, 1.0, "#e03131", 0.2),
    "question": ("?", "Surprise", fx_question, 0.9, "#4c6ef5", 0.2),
    "interrobang": ("!?", "Surprise", fx_interrobang, 1.1, "#e03131", 0.2),
    "shock": ("Shock lines", "Surprise", fx_shock, 1.0, "#1a1a1a", 0.32),
    "spiral": ("Dizzy spiral", "Surprise", fx_spiral, 1.0, "#1a1a1a", 0.18),
    "dizzy_stars": ("Dizzy stars", "Surprise", fx_dizzy_stars, 1.8, "#fcc419", 0.16),
    "hearts": ("Floating hearts", "Love & joy", fx_hearts, 0.9, "#ff4d6d", 0.3),
    "heartbeat": ("Beating heart", "Love & joy", fx_heartbeat, 1.0, "#ff4d6d", 0.18),
    "music": ("Music notes", "Love & joy", fx_music, 1.3, "#1a1a1a", 0.22),
    "sparkles": ("Sparkles", "Love & joy", fx_sparkles, 1.2, "#fff3bf", 0.25),
    "bulb": ("Idea bulb", "Idea", fx_bulb, 0.85, "#ffd43b", 0.24),
    "blush": ("Blush", "Embarrassed", fx_blush, 2.4, "#ff8fab", 0.08),
    "thumbs_up": ("Thumbs up", "Reactions", fx_thumbs_up, 0.85, "#ffcc4d", 0.26),
    "thumbs_down": ("Thumbs down", "Reactions", fx_thumbs_down, 0.85, "#ffcc4d", 0.26),
    "skull": ("Skull", "Reactions", fx_skull, 0.85, "#f1f3f5", 0.22),
    "focus_lines": ("Focus lines", "Action", fx_focus_lines, 16 / 9, "#000000", 1.0),
    "speed_lines": ("Speed lines", "Action", fx_speed_lines, 16 / 9, "#f8f9fa", 1.0),
    "impact": ("Impact burst", "Action", fx_impact, 1.3, "#ffd43b", 0.35),
    "poof": ("Poof cloud", "Action", fx_poof, 1.3, "#f1f3f5", 0.3),
}
GROUPS = ["Anger", "Unease", "Sad", "Bored", "Surprise", "Love & joy", "Idea", "Embarrassed", "Reactions", "Action"]
COMIC_EFFECTS = [("None", "")] + [(f"{EFFECTS[k][1]}: {EFFECTS[k][0]}", k)
                                  for grp in GROUPS for k in EFFECTS if EFFECTS[k][1] == grp]
EFFECT_ASPECT = {k: v[3] for k, v in EFFECTS.items()}
DEFAULT_EFFECT_COLOR = {k: v[4] for k, v in EFFECTS.items()}
DEFAULT_EFFECT_SIZE = {k: v[5] for k, v in EFFECTS.items()}


def valid_effect(name: str) -> str:
    return name if name in EFFECTS else ""


def effect_label(name: str) -> str:
    return EFFECTS[name][0] if name in EFFECTS else ""


def effect_color(st) -> QColor:
    name = valid_effect(getattr(st, "comic_effect", ""))
    return QColor(getattr(st, "effect_color", "") or DEFAULT_EFFECT_COLOR.get(name, "#000000"))


def effect_progress(st, local: float, duration: float) -> "tuple[float, bool]":
    """(g, out) of the effect's own in/out transition: g = 1 fully shown."""
    ei = max(0.0, getattr(st, "effect_in", 0.0))
    eo = max(0.0, getattr(st, "effect_out", 0.0))
    if duration > 0 and ei + eo > duration:
        k = duration / (ei + eo)
        ei, eo = ei * k, eo * k
    g_in = _clamp(local / ei) if ei > EPS else 1.0
    g_out = _clamp((duration - local) / eo) if eo > EPS and duration > 0 else 1.0
    if g_out < g_in:
        return g_out, True
    return g_in, False


def draw_comic_effect(painter: QPainter, name: str, rect: QRectF, t: float, color: QColor,
                      animated: bool = True, g: float = 1.0, out: bool = False) -> None:
    """Draw effect `name` filling `rect`: `t` = idle time (already x speed),
    `g` / `out` = its in/out transition (see effect_progress)."""
    name = valid_effect(name)
    if not name or rect.width() <= 1 or rect.height() <= 1 or g <= 0.0:
        return
    if not animated:
        t = 0.0
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    try:
        EFFECTS[name][2](painter, rect, max(0.0, t), _clamp(g), out, QColor(color))
    finally:
        painter.restore()


# =========================================================================
# text motion: Bounce, Jiggle, Animate in / out, Idle
# =========================================================================
BOUNCE_SIDES = [("Below", "down"), ("Above", "up"), ("Left", "left"), ("Right", "right")]
ANIM_IN = [("None", ""), ("Pop", "pop"), ("Slam", "slam"), ("Spin in", "spin"), ("Stretch", "stretch"),
           ("Drop", "drop"), ("Zoom", "zoom"), ("Flip", "flip"), ("Letters pop in", "letters"),
           ("Letters rain in", "letters_drop")]
ANIM_OUT = [("None", ""), ("Pop", "pop"), ("Shrink", "shrink"), ("Spin out", "spin"), ("Fly up", "fly"),
            ("Fall", "fall"), ("Flip", "flip"), ("Letters pop out", "letters"), ("Explode", "explode")]
IDLES = [("None", ""), ("Shake", "shake"), ("Pulse", "pulse"), ("Wobble", "wobble"), ("Float", "float"),
         ("Swing", "swing"), ("Wave (letters)", "wave"), ("Jitter (letters)", "jitter")]
LETTER_IN = {"letters", "letters_drop"}
LETTER_OUT = {"letters", "explode"}
LETTER_IDLE = {"wave", "jitter"}

BOUNCE_DISTANCE = 1.25        # canvases away: "really far" -- fully off-screen
JIGGLE_DEG = 11.0
JIGGLE_SHIFT = 0.012          # fraction of the canvas


def motion_windows(st, duration: float) -> dict:
    """The motion windows (seconds, element-local), fitted inside the
    element's time like the other in/out effects. In: bounce and the
    Animate-in style run together from the start, the jiggle after them.
    Out: the Animate-out style and the jiggle-out end together at the end."""
    bi = max(0.0, getattr(st, "bounce_in", 0.0))
    ai = max(0.0, getattr(st, "anim_in_dur", 0.0)) if getattr(st, "anim_in", "") else 0.0
    ji = max(0.0, getattr(st, "jiggle_in", 0.0))
    jo = max(0.0, getattr(st, "jiggle_out", 0.0))
    ao = max(0.0, getattr(st, "anim_out_dur", 0.0)) if getattr(st, "anim_out", "") else 0.0
    lead = max(bi, ai)
    total = lead + ji + max(jo, ao)
    k = 1.0
    if duration > 0 and total > duration:
        k = duration / total
    return {"bi": bi * k, "ai": ai * k, "ji": ji * k, "jo": jo * k, "ao": ao * k, "lead": lead * k,
            "duration": max(0.0, duration)}


def has_motion(st) -> bool:
    if st is None:
        return False
    return bool(any(getattr(st, k, 0.0) > EPS for k in ("bounce_in", "jiggle_in", "jiggle_out"))
                or (getattr(st, "anim_in", "") and getattr(st, "anim_in_dur", 0.0) > EPS)
                or (getattr(st, "anim_out", "") and getattr(st, "anim_out_dur", 0.0) > EPS)
                or getattr(st, "idle", ""))


def text_motion(st, local: float, duration: float, cw: float, ch: float) -> "tuple":
    """(dx, dy, drotation, sx, sy, alpha) to apply to a whole text element
    at element-local time `local`: offsets in canvas pixels, rotation in
    degrees, scale factors and an opacity multiplier."""
    if not has_motion(st):
        return 0.0, 0.0, 0.0, 1.0, 1.0, 1.0
    w = motion_windows(st, duration)
    dx = dy = rot = 0.0
    sx = sy = alpha = 1.0
    end = w["duration"]
    # ---- bounce in
    if w["bi"] > EPS and local < w["bi"]:
        far = 1.0 - ease_out_back(local / w["bi"])
        side = getattr(st, "bounce_from", "down")
        if side == "up":
            dy -= far * BOUNCE_DISTANCE * ch
        elif side == "left":
            dx -= far * BOUNCE_DISTANCE * cw
        elif side == "right":
            dx += far * BOUNCE_DISTANCE * cw
        else:
            dy += far * BOUNCE_DISTANCE * ch
    # ---- animate in (whole-element styles)
    kind = getattr(st, "anim_in", "")
    if w["ai"] > EPS and local < w["ai"] and kind and kind not in LETTER_IN:
        u = local / w["ai"]
        if kind == "pop":
            s = max(0.0, ease_out_back(u, 2.4))
            sx *= s
            sy *= s
        elif kind == "slam":
            # from huge and see-through down onto the screen, then a thud shake
            if u < 0.55:
                v = ease_in_cubic(u / 0.55)
                s = 3.2 - 2.2 * v
                alpha *= 0.25 + 0.75 * v
            else:
                v = (u - 0.55) / 0.45
                s = 1.0 + 0.06 * math.sin(v * math.pi * 3) * (1 - v)
                dx += math.sin(v * 47.0) * (1 - v) * 0.012 * cw
                dy += math.cos(v * 53.0) * (1 - v) * 0.012 * ch
            sx *= s
            sy *= s
        elif kind == "spin":
            s = max(0.0, ease_out_back(u, 1.2))
            sx *= s
            sy *= s
            rot += -360.0 * (1.0 - ease_out_cubic(u))
        elif kind == "stretch":
            # squash & stretch: shoots up tall and thin, squashes wide, settles
            if u < 0.2:
                v = u / 0.2
                sx *= 0.2 + 0.6 * ease_out_cubic(v)
                sy *= 0.3 + 1.6 * ease_out_cubic(v)
            else:
                v = (u - 0.2) / 0.8
                wob = math.sin(v * math.pi * 3.0) * (1 - v) ** 1.5
                sx *= 0.8 + 0.2 * ease_out_cubic(v) + 0.25 * wob
                sy *= 1.9 - 0.9 * ease_out_cubic(v) - 0.25 * wob
        elif kind == "drop":
            # falls from a little above and bounces to a stop (gravity)
            hgt = 0.35 * ch
            if u < 0.3:
                v = u / 0.3
                dy -= hgt * (1 - v * v)
                alpha *= _clamp(v * 3)
            else:
                v = (u - 0.3) / 0.7
                dy -= hgt * 0.3 * abs(math.sin(v * math.pi * 2.0)) * (1 - v) ** 2
                if v < 0.08:          # squash on landing
                    sy *= 0.85
                    sx *= 1.12
        elif kind == "zoom":
            s = ease_out_cubic(u)
            sx *= s
            sy *= s
            alpha *= _clamp(u * 2)
        elif kind == "flip":
            sx *= math.cos(_clamp(1.0 - ease_out_back(u, 1.0), -0.5, 1.0) * math.pi / 2)
    # ---- jiggle in (after the lead-in) / out
    env, tj = 0.0, 0.0
    if w["ji"] > EPS and w["lead"] - EPS <= local < w["lead"] + w["ji"]:
        tj = local - w["lead"]
        env = (1.0 - tj / w["ji"]) ** 2
    if w["jo"] > EPS and end > 0 and local > end - w["jo"]:
        tj = local - (end - w["jo"])
        env = max(env, (tj / w["jo"]) ** 2)
    if env > EPS:
        rot += JIGGLE_DEG * env * math.sin(tj * 2 * math.pi * 3.1)
        dx += JIGGLE_SHIFT * cw * env * math.sin(tj * 2 * math.pi * 2.3 + 0.7)
        dy += JIGGLE_SHIFT * ch * env * math.sin(tj * 2 * math.pi * 3.7 + 1.9)
    # ---- animate out
    kind = getattr(st, "anim_out", "")
    if w["ao"] > EPS and end > 0 and local > end - w["ao"] and kind and kind not in LETTER_OUT:
        u = _clamp((local - (end - w["ao"])) / w["ao"])
        if kind == "pop":
            s = max(0.0, 1.0 - ease_in_back(u, 2.4))
            sx *= s
            sy *= s
        elif kind == "shrink":
            s = 1.0 - ease_in_cubic(u)
            sx *= s
            sy *= s
            alpha *= 1.0 - u * u
        elif kind == "spin":
            s = max(0.0, 1.0 - ease_in_back(u, 1.2))
            sx *= s
            sy *= s
            rot += 360.0 * ease_in_cubic(u)
        elif kind == "fly":
            # a little crouch, then off the top
            if u < 0.25:
                v = u / 0.25
                dy += 0.03 * ch * math.sin(v * math.pi)
                sy *= 1.0 - 0.15 * math.sin(v * math.pi)
                sx *= 1.0 + 0.1 * math.sin(v * math.pi)
            else:
                v = (u - 0.25) / 0.75
                dy -= 1.3 * ease_in_cubic(v) * ch
                sy *= 1.0 + 0.25 * v
                sx *= 1.0 - 0.15 * v
        elif kind == "fall":
            dy += 1.3 * ease_in_cubic(u) * ch
            rot += 35.0 * ease_in_cubic(u)
        elif kind == "flip":
            sx *= math.cos(_clamp(ease_in_back(u, 1.0), -0.5, 1.0) * math.pi / 2)
    # ---- idle
    idle = getattr(st, "idle", "")
    if idle and idle not in LETTER_IDLE:
        amt = max(0.0, getattr(st, "idle_amount", 1.0))
        ts = local * max(0.0, getattr(st, "idle_speed", 1.0))
        if idle == "shake":
            q = int(ts * 24)
            dx += (_rand(q * 2) - 0.5) * 0.012 * cw * amt
            dy += (_rand(q * 2 + 1) - 0.5) * 0.012 * ch * amt
            rot += (_rand(q * 5) - 0.5) * 3.0 * amt
        elif idle == "pulse":
            s = 1.0 + 0.07 * amt * (math.sin(ts * 2 * math.pi * 1.2) * 0.5 + 0.5)
            sx *= s
            sy *= s
        elif idle == "wobble":
            rot += 6.0 * amt * math.sin(ts * 2 * math.pi * 0.9)
            sx *= 1.0 + 0.03 * amt * math.sin(ts * 2 * math.pi * 1.8)
            sy *= 1.0 - 0.03 * amt * math.sin(ts * 2 * math.pi * 1.8)
        elif idle == "float":
            dy += 0.015 * ch * amt * math.sin(ts * 2 * math.pi * 0.6)
            rot += 1.5 * amt * math.sin(ts * 2 * math.pi * 0.3)
        elif idle == "swing":
            rot += 9.0 * amt * math.sin(ts * 2 * math.pi * 0.7)
    return dx, dy, rot, sx, sy, alpha


def has_letter_motion(st) -> bool:
    if st is None:
        return False
    return bool((getattr(st, "anim_in", "") in LETTER_IN and getattr(st, "anim_in_dur", 0.0) > EPS)
                or (getattr(st, "anim_out", "") in LETTER_OUT and getattr(st, "anim_out_dur", 0.0) > EPS)
                or getattr(st, "idle", "") in LETTER_IDLE or getattr(st, "jumble", 0.0) > EPS)


def letter_motion(st, i: int, n: int, local: float, duration: float, em: float) -> "tuple":
    """Per-letter (dx, dy, rotation, scale, alpha) for letter i of n -- the
    letter styles of Animate in / out, the Wave / Jitter idles and the
    static Jumble -- in pixels of the text (em = the font size)."""
    dx = dy = rot = 0.0
    s = a = 1.0
    jum = max(0.0, getattr(st, "jumble", 0.0))
    if jum > EPS:                                   # comic-lettering scramble (static)
        rot += (_rand(i * 17 + 3) - 0.5) * 30.0 * jum
        dy += (_rand(i * 29 + 5) - 0.5) * 0.35 * em * jum
        s *= 1.0 + (_rand(i * 41 + 7) - 0.5) * 0.35 * jum
    w = motion_windows(st, duration)
    kind = getattr(st, "anim_in", "")
    if kind in LETTER_IN and w["ai"] > EPS and local < w["ai"]:
        u = _clamp((local / w["ai"]) * 1.6 - 0.6 * i / max(1, n - 1)) if n > 1 else local / w["ai"]
        if kind == "letters":
            s *= max(0.0, ease_out_back(u, 2.6))
        else:                                        # letters_drop: each falls in and lands
            dy -= (1.0 - ease_out_back(u, 1.4)) * em * 2.5
            a *= _clamp(u * 3)
    kind = getattr(st, "anim_out", "")
    end = w["duration"]
    if kind in LETTER_OUT and w["ao"] > EPS and end > 0 and local > end - w["ao"]:
        u = _clamp((local - (end - w["ao"])) / w["ao"])
        if kind == "letters":
            v = _clamp(u * 1.6 - 0.6 * (n - 1 - i) / max(1, n - 1)) if n > 1 else u
            s *= max(0.0, 1.0 - ease_in_back(v, 2.6))
        else:                                        # explode: letters fly apart, spinning, fading
            ang = 2 * math.pi * _rand(i * 13 + 1)
            dist = em * (1.5 + 2.5 * _rand(i * 7 + 2)) * ease_out_cubic(u)
            dx += math.cos(ang) * dist
            dy += math.sin(ang) * dist - em * 0.8 * math.sin(u * math.pi)
            rot += (_rand(i * 3) - 0.5) * 720 * u
            a *= 1.0 - u * u
    idle = getattr(st, "idle", "")
    if idle in LETTER_IDLE:
        amt = max(0.0, getattr(st, "idle_amount", 1.0))
        ts = local * max(0.0, getattr(st, "idle_speed", 1.0))
        if idle == "wave":
            dy += -math.sin(ts * 2 * math.pi * 1.1 - i * 0.55) * em * 0.18 * amt
        else:                                        # jitter: every letter boils on its own
            q = int(ts * BOIL_FPS)
            dx += (_rand(q * 31 + i * 7) - 0.5) * em * 0.08 * amt
            dy += (_rand(q * 37 + i * 11) - 0.5) * em * 0.08 * amt
            rot += (_rand(q * 43 + i * 13) - 0.5) * 10.0 * amt
    return dx, dy, rot, s, a


# =========================================================================
# onomatopoeia backdrops (a shape behind the words)
# =========================================================================
BACKDROPS = [("None", ""), ("Starburst", "burst"), ("Explosion", "jagged"), ("Boom cloud", "boom"),
             ("Soft cloud", "cloud"), ("Splat", "splat"), ("Flash rays", "flash")]


def valid_backdrop(name: str) -> str:
    return name if name in {v for _l, v in BACKDROPS if v} else ""


def _splat_path(c: QPointF, rx: float, ry: float, seed: int) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    n = 22
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        k = 0.78 + 0.3 * _rand(seed + i * 5)
        if i % 5 == 2:
            k += 0.25                                     # a few longer drips
        pts.append(QPointF(c.x() + math.cos(a) * rx * k, c.y() + math.sin(a) * ry * k))
    path.moveTo((pts[-1] + pts[0]) / 2)
    for i in range(n):
        nxt = pts[(i + 1) % n]
        path.quadTo(pts[i], (pts[i] + nxt) / 2)
    path.closeSubpath()
    for j in range(5):                                    # flung droplets
        a = 2 * math.pi * _rand(seed + 97 + j)
        d = 1.2 + 0.25 * _rand(seed + 131 + j)
        rr = min(rx, ry) * (0.06 + 0.07 * _rand(seed + 71 + j))
        path.addEllipse(QPointF(c.x() + math.cos(a) * rx * d, c.y() + math.sin(a) * ry * d), rr, rr)
    return path


def backdrop_path(kind: str, rect: QRectF, seed: int = 0) -> QPainterPath:
    c = rect.center()
    rx, ry = rect.width() / 2, rect.height() / 2
    if kind == "burst":
        return burst_path(c, rx, ry, 12, seed + 5, 0.3)
    if kind == "jagged":
        return burst_path(c, rx, ry, 15, seed + 11, 0.42)
    if kind == "boom":
        return cloud_path(c, rx * 2.05, ry * 2.0, 9, seed + 3)
    if kind == "cloud":
        path = QPainterPath()
        path.addRoundedRect(QRectF(c.x() - rx * 0.92, c.y() - ry * 0.8, rx * 1.84, ry * 1.6), ry * 0.8, ry * 0.8)
        return path
    if kind == "splat":
        return _splat_path(c, rx * 0.95, ry * 0.95, seed + 17)
    return QPainterPath()


def backdrop_scale(st, local: float, duration: float) -> float:
    """With a per-letter Animate in / out the words build up / fall apart
    letter by letter, so the backdrop pops in with them and pops out as
    they go (a whole-element style already scales it along with the words)."""
    w = motion_windows(st, duration)
    k = 1.0
    if getattr(st, "anim_in", "") in LETTER_IN and w["ai"] > EPS and local < w["ai"]:
        k = min(k, max(0.0, ease_out_back(local / w["ai"], 2.0)))
    end = w["duration"]
    if getattr(st, "anim_out", "") in LETTER_OUT and w["ao"] > EPS and end > 0 and local > end - w["ao"]:
        u = _clamp((local - (end - w["ao"])) / w["ao"])
        k = min(k, max(0.0, 1.0 - ease_in_back(_clamp(u * 1.4), 2.0)))
    return k


def draw_backdrop(painter: QPainter, st, text_rect: QRectF, local: float, ch: float,
                  duration: float = 0.0) -> None:
    """The shape behind an onomatopoeia's words, sized around them. It boils
    (redraws a little differently ~8x a second) while the text has a Shake or
    Jitter idle, like hand-drawn comic SFX."""
    kind = valid_backdrop(getattr(st, "backdrop", ""))
    if not kind or text_rect.width() <= 0:
        return
    w = max(text_rect.width() * 1.55, text_rect.height() * 2.2)
    h = max(text_rect.height() * 2.0, w * 0.45)
    rect = QRectF(text_rect.center().x() - w / 2, text_rect.center().y() - h / 2, w, h)
    boil = 0
    if getattr(st, "idle", "") in ("shake", "jitter"):
        boil = int(max(0.0, local) * max(0.0, getattr(st, "idle_speed", 1.0)) * 8.0) * 37
    fill = QColor(getattr(st, "backdrop_fill", "#ffd43b"))
    line = QColor(getattr(st, "backdrop_outline", "#000000"))
    lw = max(1.0, min(w, h) * 0.022)
    k = backdrop_scale(st, local, duration)
    if k <= 0.001:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    if k != 1.0:
        _scaled(painter, rect.center(), k)
    if kind == "flash":
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        c = rect.center()
        R = math.hypot(w, h) * 0.55
        n = 26
        for i in range(n):
            a = 2 * math.pi * (i + 0.4 * (_rand(boil + i) - 0.5)) / n
            half = math.pi / n * (0.35 + 0.3 * _rand(boil + i * 3))
            L = R * (0.7 + 0.3 * _rand(boil + i * 7))
            wedge = QPainterPath(c)
            wedge.lineTo(c.x() + math.cos(a - half) * L, c.y() + math.sin(a - half) * L * 0.7)
            wedge.lineTo(c.x() + math.cos(a + half) * L, c.y() + math.sin(a + half) * L * 0.7)
            wedge.closeSubpath()
            painter.drawPath(wedge)
    else:
        path = backdrop_path(kind, rect, boil)
        pen = _pen(line, lw, join=Qt.MiterJoin)
        pen.setMiterLimit(2.0)                            # sharp points without long black needles
        painter.setPen(pen)
        painter.setBrush(fill)
        painter.drawPath(path)
        if kind in ("burst", "jagged"):                   # a lighter core
            core = QColor(fill).lighter(130)
            painter.setPen(Qt.NoPen)
            painter.setBrush(core)
            painter.drawPath(backdrop_path(kind, QRectF(rect.center().x() - w * 0.3, rect.center().y() - h * 0.3,
                                                        w * 0.6, h * 0.6), boil + 999))
    painter.restore()
