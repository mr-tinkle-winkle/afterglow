"""Shared pieces for the reference sheets: building poses from specs, framing, effects, rendering."""
import math, copy
import numpy as np
from PySide6.QtGui import QColor, QPen
from PySide6.QtCore import QPointF, Qt
import g3d
from g3d import glove_prims, render, transform_prims, to_qimage
from ready3d import ready3d
from clasp2 import clasp2
from pose3d import prim_bounds, _flex_of, _set_flex

CUFF = QColor("#0c8ea0")
LIGHT = (-0.55, 0.55, 0.63)


def mixq(a, b, t):
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def build(fr):
    """A frame spec -> [left HandPose, right HandPose] (plus ._clasped)."""
    fr = dict(fr)
    for k in ("fx", "kind"):
        fr.pop(k, None)
    relax_f = fr.pop("relax_fingers", 0.0)
    relax_t = fr.pop("relax_thumbs", 0.0)
    if "apart" in fr or "turn" in fr:
        L, R = ready3d(**fr)
        L._clasped = R._clasped = False
        return [L, R]
    L, R = clasp2(**fr)
    for hand in (L, R):
        for name in ("index", "middle", "pinky"):
            _set_flex(hand, name, [max(0.0, f - relax_f) for f in _flex_of(hand, name)])
        _set_flex(hand, "thumb", [max(0.0, f - relax_t) for f in _flex_of(hand, "thumb")])
    L._clasped = R._clasped = True
    return [L, R]


def scaled(hands, k):
    out = []
    for hp in hands:
        q = copy.deepcopy(hp)
        q.O = tuple(np.asarray(hp.O, float) * k)
        q.s = hp.s * k
        out.append(q)
    return out


def view_prims(hands, Rv, tint=None):
    return [transform_prims(glove_prims(hp, gi), Rv) for gi, hp in enumerate(hands)]


def bounds(hands, Rv):
    return prim_bounds([p for ps in view_prims(hands, Rv) for p in ps])


def recentre(hands, Rv):
    lo, hi = bounds(hands, Rv)
    c2 = (lo + hi) / 2
    d = Rv.T @ np.array([-c2[0], -c2[1], 0.0])
    out = []
    for hp in hands:
        q = copy.deepcopy(hp)
        q.O = tuple(np.asarray(hp.O, float) + d)
        q._clasped = getattr(hp, "_clasped", False)
        out.append(q)
    return out


def row_scale(frames, Rv, fill_w=0.94, fill_h=0.84):
    k = 1e9
    for hands in frames:
        lo, hi = bounds(hands, Rv)
        w_, h_ = hi - lo
        k = min(k, fill_w / w_, fill_h / h_)
    return k


def draw_fx(p, cx, cy, w, k=0.35):
    """The impact: a shock ring and short lines bursting out above it (as the app draws it)."""
    ease_out = 1 - (1 - k) ** 3
    fade = 1 - k * k
    ring = mixq(mixq(CUFF, QColor("#ffffff"), 0.4), QColor("#ffffff"), 0.5)
    for scale, alpha, width in ((1.0, 0.95, 0.050), (0.72, 0.55, 0.032)):
        r = w * (0.14 + 0.30 * ease_out) * scale
        col = QColor(ring); col.setAlphaF(alpha * fade)
        p.setPen(QPen(col, max(1.4, w * width * (1.0 - 0.5 * k)))); p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r * 0.82)
    col = QColor(ring); col.setAlphaF(fade)
    p.setPen(QPen(col, max(1.4, w * 0.028), Qt.SolidLine, Qt.RoundCap))
    top = QPointF(cx, cy - w * 0.05)
    for a in (-150, -120, -90, -60, -30):
        r = math.radians(a)
        d0 = w * (0.13 + 0.12 * ease_out)
        d1 = d0 + w * 0.09 * (1.0 - 0.5 * k)
        p.drawLine(QPointF(top.x() + math.cos(r) * d0, top.y() + math.sin(r) * d0),
                   QPointF(top.x() + math.cos(r) * d1, top.y() + math.sin(r) * d1))


def render_frame(hands, Rv, box_w, ss=3, dim=0.18, tint=None, margin=0.30):
    """Render the hands for an item box box_w wide (0.86 box_w tall, centred on the origin) with a margin.
    Returns (QImage, offset_x, offset_y) -- the image's top-left is (box.left - ox, box.top - oy)."""
    Wpx = int(round(box_w * (1 + 2 * margin)))
    Hpx = int(round(box_w * (0.86 + 2 * margin)))
    old = g3d.DEBUG_TINT
    g3d.DEBUG_TINT = tint
    try:
        arr = render(view_prims(hands, Rv), Wpx, Hpx, -0.5 - margin, 0.43 + margin, box_w, ss=ss, size=hands[0].s,
                     dim={0: dim} if getattr(hands[0], "_clasped", False) and dim else None, light=LIGHT)
    finally:
        g3d.DEBUG_TINT = old
    return to_qimage(arr), margin * box_w, margin * box_w
