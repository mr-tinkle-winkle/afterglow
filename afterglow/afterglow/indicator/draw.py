"""
Pure drawing for the clip indicator: the clapper, the "hands" (clapping cartoon
gloves), the loading circle and every enter / exit / fail animation.  No windows
and no clocks in here -- everything is a function of its arguments, so the helper's
overlay window and Settings' live preview draw with exactly the same code and the
tests can render any frame offscreen.

Conventions
  * ``rect`` is the item's resting rectangle (``item_size(size)``), in painter space.
  * An animation is ``animate(phase, kind, t, anchor, space) -> Xform`` for t in
    [0, 1] (linear time; each animation applies its own easing).  ``enter`` ends at
    the identity transform (t=1), ``exit`` starts at it (t=0) and ends fully gone,
    ``fail`` starts at it and ends off the bottom of the surface.
  * ``Space`` says how far the item has to travel to leave the screen / surface,
    because the overlay surface is anchored to the screen edge with the padding
    applied in the drawing (so animations can start/finish truly off-screen).
  * "The edge" is the edge the item comes in through / leaves by: the TOP or BOTTOM edge for
    every corner and for ``top`` / ``bottom`` (never a side edge), a side edge only for ``left`` /
    ``right``, and the bottom one for ``center`` (which has no edge of its own, so the edge-bound
    animations fade while they travel).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF, QSizeF
from PySide6.QtGui import (
    QColor, QImage, QPainter, QPainterPath, QPen, QTransform, QFont,
)

# ------------------------------------------------------------------ sizes / colours

ASPECT = 0.86          # clapper height / width


def item_size(size: float) -> QSizeF:
    """The item's box for a given width setting."""
    return QSizeF(float(size), float(size) * ASPECT)


def circle_diameter(size: float) -> float:
    return max(20.0, round(size * 0.30))


def afterglow_defaults(appearance=None) -> "dict[str, str]":
    """The default part colours: the afterglow theme's own colours (``appearance`` is an
    ``config.AppearanceSettings``; None = the theme's built-in values).  Taken from the stored hex
    values rather than ``gui.theme.Theme`` so the headless daemon can compute them too."""
    if appearance is None:
        from ..config import AppearanceSettings
        appearance = AppearanceSettings()

    def col(name: str, fallback: str) -> QColor:
        c = QColor(str(getattr(appearance, name, "") or ""))
        return c if c.isValid() else QColor(fallback)
    accent = col("afterglow_color_accent", "#152c4f")
    card = col("afterglow_color_card_background", "#091e37")
    app_bg = col("afterglow_color_app_background", "#0d1621")
    teal = col("afterglow_color_turquoise", "#0c8ea0")
    pale = mix(teal, QColor("#ffffff"), 0.82)
    return {
        # clapper: a navy board with turquoise / navy stripes and pale chalk lines
        "stripe_a": teal.name(), "stripe_b": accent.name(), "hinge": mix(teal, QColor("#ffffff"), 0.45).name(),
        "board": card.name(), "lines": pale.name(), "outline": mix(teal, app_bg, 0.15).name(),
        # hands: pale gloves outlined in the dark app colour, turquoise cuffs and stitching
        "glove": mix(teal, QColor("#ffffff"), 0.92).name(), "glove_outline": app_bg.name(),
        "cuff": teal.name(), "stitches": teal.name(),
    }


DEFAULT_COLORS = None     # filled in below (needs mix())
CLAPPER_COLOR_KEYS = ("stripe_a", "stripe_b", "hinge", "board", "lines", "outline")
HANDS_COLOR_KEYS = ("glove", "glove_outline", "cuff", "stitches")
COLOR_LABELS = {
    "stripe_a": "Stripe A", "stripe_b": "Stripe B", "hinge": "Hinge", "board": "Board",
    "lines": "Chalk lines", "outline": "Outline",
    "glove": "Glove", "glove_outline": "Glove outline", "cuff": "Cuff", "stitches": "Stitches",
}
DEFAULT_CIRCLE_COLOR = "#9a9a9a"
DEFAULT_OVERLAY_CIRCLE_COLOR = "#9b5cff"
FAIL_RED = "#e5484d"


def resolve_colors(overrides: "dict | None" = None) -> "dict[str, QColor]":
    """The afterglow defaults, overridden by any valid colour in ``overrides``."""
    out = {}
    for k, default in DEFAULT_COLORS.items():
        c = QColor(str((overrides or {}).get(k) or ""))
        out[k] = c if c.isValid() else QColor(default)
    return out


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t, a.alphaF() + (b.alphaF() - a.alphaF()) * t)


# ------------------------------------------------------------------ easing

DEFAULT_COLORS = afterglow_defaults()


def clamp01(t: float) -> float:
    return 0.0 if t < 0.0 else 1.0 if t > 1.0 else t


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out_quad(t):
    t = clamp01(t)
    return 1 - (1 - t) ** 2


def ease_in_quad(t):
    t = clamp01(t)
    return t * t


def ease_out_cubic(t):
    t = clamp01(t)
    return 1 - (1 - t) ** 3


def ease_in_cubic(t):
    t = clamp01(t)
    return t ** 3


def ease_in_out(t):
    t = clamp01(t)
    return 3 * t * t - 2 * t * t * t


def ease_out_back(t, s=1.70158):
    t = clamp01(t) - 1.0
    return t * t * ((s + 1) * t + s) + 1.0


# ------------------------------------------------------------------ clap pose

CLAP_SHUT_MS = 80.0        # the clap itself: the top stick snaps shut / the hands come together
CLAP_REBOUND_MS = 140.0    # a small bounce back after the impact
CLAP_TOTAL_MS = 380.0      # until the impact has settled (the "processing" step starts here)
CLAPPER_OPEN_DEGREES = 28.0


@dataclass
class ClapPose:
    open: float = 0.0       # 1 = ready to clap (clapper: stick up 28 deg; hands: apart), 0 = shut (neutral / clapped)
    squash: float = 0.0     # px the item is squashed on impact
    impact: float = 0.0     # 0..1 strength of the impact lines


def clap_pose(t_ms: float) -> ClapPose:
    """The clap, from the READY pose (stick already up / hands already apart -- that is how the
    item arrives and waits for OBS): shut in ~80 ms (accelerating), a 2-3 px squash and a few
    impact lines at the contact point, a small rebound, then settled shut (``CLAP_TOTAL_MS``)."""
    if t_ms <= 0:
        return ClapPose(open=1.0)
    if t_ms < CLAP_SHUT_MS:
        return ClapPose(open=1.0 - ease_in_quad(t_ms / CLAP_SHUT_MS))
    since = t_ms - CLAP_SHUT_MS
    squash = 3.0 * max(0.0, 1.0 - since / 90.0)
    if since < 160:
        squash += 0.8 * math.sin(since / 60.0 * math.pi) * max(0.0, 1.0 - since / 160.0)
    rebound = 0.09 * math.sin(math.pi * since / CLAP_REBOUND_MS) if since < CLAP_REBOUND_MS else 0.0
    impact = max(0.0, 1.0 - since / (CLAP_TOTAL_MS - CLAP_SHUT_MS))
    return ClapPose(open=max(0.0, rebound), squash=max(0.0, squash), impact=impact)


def idle_bob(t_seconds: float, h: float) -> float:
    """"Stay" mode: a subtle vertical bob (px) while the clip is processing."""
    return math.sin(t_seconds * 2 * math.pi / 1.6) * h * 0.022


# ------------------------------------------------------------------ icon

_ICON_CACHE: "dict[str, tuple[float, QImage | None]]" = {}


def load_icon(path: "str | None") -> "QImage | None":
    """A custom icon image, or None for no icon (empty / missing / unreadable file)."""
    if not path:
        return None
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None
    hit = _ICON_CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    img = QImage(str(path))
    img = None if img.isNull() else img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    _ICON_CACHE[path] = (mtime, img)
    return img


def fit_rect(box: QRectF, img_w: float, img_h: float) -> QRectF:
    """The largest rect with the image's aspect that fits inside ``box``, centred."""
    if img_w <= 0 or img_h <= 0 or box.width() <= 0 or box.height() <= 0:
        return QRectF()
    s = min(box.width() / img_w, box.height() / img_h)
    w, h = img_w * s, img_h * s
    return QRectF(box.center().x() - w / 2, box.center().y() - h / 2, w, h)


def _draw_icon(p: QPainter, box: QRectF, icon: "QImage | None") -> "QRectF | None":
    if icon is None:
        return None
    r = fit_rect(box, icon.width(), icon.height())
    if r.isEmpty():
        return None
    p.save()
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.drawImage(r, icon)
    p.restore()
    return r


# ------------------------------------------------------------------ clapper

def _pen(color, width) -> QPen:
    pen = QPen(QColor(color), width)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    return pen


def _stripes(p: QPainter, bar: QRectF, a: QColor, b: QColor, phase: int) -> None:
    """Diagonal two-tone stripes filling ``bar`` (the caller has clipped to its shape)."""
    sw = bar.height() * 1.15
    slant = bar.height()
    p.setPen(Qt.NoPen)
    n = int(bar.width() / sw) + 4
    x0 = bar.left() - slant
    for k in range(n):
        col = a if (k + phase) % 2 == 0 else b
        poly = QPainterPath(QPointF(x0 + k * sw, bar.bottom()))
        poly.lineTo(x0 + k * sw + slant, bar.top())
        poly.lineTo(x0 + (k + 1) * sw + slant, bar.top())
        poly.lineTo(x0 + (k + 1) * sw, bar.bottom())
        poly.closeSubpath()
        p.fillPath(poly, col)


def clapper_geometry(rect: QRectF) -> dict:
    """The clapper's parts as rects/points (shared by the drawing and the tests)."""
    w, h = rect.width(), rect.height()
    L, T = rect.left(), rect.top()
    stick = QRectF(L, T, w, h * 0.17)
    body = QRectF(L, T + h * 0.17, w, h * 0.83)
    bar = QRectF(L, body.top(), w, h * 0.16)
    board = QRectF(L, bar.bottom(), w, body.bottom() - bar.bottom())
    margin = w * 0.08
    y1 = board.top() + board.height() * 0.17
    y2 = board.top() + board.height() * 0.40
    lines_bottom = y2
    icon_area = QRectF(L + margin, lines_bottom + h * 0.04, w - 2 * margin, body.bottom() - h * 0.05 - (lines_bottom + h * 0.04))
    side = min(board.height() * 0.45, icon_area.height(), icon_area.width() * 0.8)
    icon_box = QRectF(icon_area.center().x() - side / 2, icon_area.center().y() - side / 2, side, side)
    return {
        "stick": stick, "body": body, "bar": bar, "board": board,
        "hinge": QPointF(L + w * 0.055, stick.bottom()), "tip": QPointF(L + w * 0.985, stick.top() + stick.height() * 0.5),
        "line_y": (y1, y2), "margin": margin, "icon_box": icon_box,
    }


def draw_clapper(p: QPainter, rect: QRectF, pose: ClapPose, colors: "dict[str, QColor]",
                 icon: "QImage | None" = None) -> "QRectF | None":
    """The movie clapper.  Returns the rect the icon was drawn in (None = no icon)."""
    w, h = rect.width(), rect.height()
    g = clapper_geometry(rect)
    lw = max(1.4, w * 0.03)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    if pose.squash > 0:                                    # squash about the bottom centre
        p.translate(rect.center().x(), rect.bottom())
        p.scale(1.0 + pose.squash / w * 0.5, max(0.5, 1.0 - pose.squash / h))
        p.translate(-rect.center().x(), -rect.bottom())

    ink = colors["outline"]
    # ---- board body (with the fixed striped bar across its top)
    radius = w * 0.06
    body_path = QPainterPath()
    body_path.addRoundedRect(g["body"], radius, radius)
    p.setPen(Qt.NoPen)
    p.fillPath(body_path, colors["board"])
    p.save()
    p.setClipPath(body_path)
    _stripes(p, g["bar"], colors["stripe_a"], colors["stripe_b"], 1)
    p.restore()
    p.setPen(_pen(ink, lw))
    p.drawLine(QPointF(g["body"].left(), g["bar"].bottom()), QPointF(g["body"].right(), g["bar"].bottom()))
    # ---- chalk lines
    y1, y2 = g["line_y"]
    m = g["margin"]
    left, right = g["body"].left() + m, g["body"].right() - m
    p.setPen(_pen(colors["lines"], max(1.2, lw * 0.8)))
    p.drawLine(QPointF(left, y1), QPointF(right, y1))
    p.drawLine(QPointF(left, y2), QPointF(right, y2))
    p.drawLine(QPointF(left + (right - left) * 0.52, y1), QPointF(left + (right - left) * 0.52, y2))
    # ---- icon, centred in the board area below the lines
    icon_rect = _draw_icon(p, g["icon_box"], icon)
    # ---- body outline
    p.setPen(_pen(ink, lw))
    p.setBrush(Qt.NoBrush)
    p.drawPath(body_path)

    # ---- the moving stick, hinged at its bottom-left
    hinge = g["hinge"]
    p.save()
    p.translate(hinge)
    p.rotate(-CLAPPER_OPEN_DEGREES * clamp01(pose.open))
    p.translate(-hinge)
    stick = g["stick"]
    sp = QPainterPath()
    sp.addRoundedRect(stick, radius * 0.7, radius * 0.7)
    p.setPen(Qt.NoPen)
    p.fillPath(sp, colors["stripe_b"])
    p.save()
    p.setClipPath(sp)
    _stripes(p, stick, colors["stripe_a"], colors["stripe_b"], 0)
    p.restore()
    p.setPen(_pen(ink, lw))
    p.setBrush(Qt.NoBrush)
    p.drawPath(sp)
    p.restore()
    # ---- hinge pin
    p.setPen(_pen(ink, lw * 0.8))
    p.setBrush(colors["hinge"])
    p.drawEllipse(hinge, w * 0.028, w * 0.028)
    p.restore()

    if pose.impact > 0.02:                                  # short impact lines at the tip
        _impact_lines(p, g["tip"], w * 0.16, pose.impact, colors["lines"], lw)
    return icon_rect


def _impact_lines(p: QPainter, tip: QPointF, length: float, strength: float, color: QColor, lw: float,
                  angles=(-75, -40, -5)) -> None:
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    c = QColor(color)
    c.setAlphaF(clamp01(strength))
    p.setPen(_pen(c, max(1.2, lw * 0.8)))
    grow = 0.55 + 0.45 * (1.0 - strength)
    for a in angles:
        r = math.radians(a)
        d0 = length * 0.45 * grow
        d1 = length * grow + length * 0.35
        p.drawLine(QPointF(tip.x() + math.cos(r) * d0, tip.y() + math.sin(r) * d0),
                   QPointF(tip.x() + math.cos(r) * d1, tip.y() + math.sin(r) * d1))
    p.restore()


# ------------------------------------------------------------------ hands

# Mickey-style glove: three plump fingers and a thumb.  Finger centres / tops (v), thumb side
# first; the middle finger is the tallest.
_FINGER_X = (0.355, 0.585, 0.815)
_FINGER_TOP = (0.15, 0.06, 0.18)
_FINGER_W = 0.235


def glove_path(rect: QRectF) -> "tuple[QPainterPath, dict]":
    """One plump cartoon glove, upright, fingers up, seen from the BACK of the hand with the
    thumb on the left: three round fingers pressed together under a scalloped top, a soft palm
    and a thumb leaning out a little.  Returns (hand shape without the cuff, parts)."""
    w, h = rect.width(), rect.height()
    X = lambda u: rect.left() + w * u      # noqa: E731
    Y = lambda v: rect.top() + h * v       # noqa: E731
    palm = QPainterPath()
    palm.addRoundedRect(QRectF(X(0.24), Y(0.36), w * 0.70, h * 0.45), w * 0.16, w * 0.16)
    hand = QPainterPath(palm)
    fw = w * _FINGER_W
    for cx, top in zip(_FINGER_X, _FINGER_TOP):
        f = QPainterPath()
        f.addRoundedRect(QRectF(X(cx) - fw / 2, Y(top), fw, h * 0.42), fw / 2, fw / 2)
        hand = hand.united(f)
    th = QPainterPath()
    tw = w * 0.22
    th.addRoundedRect(QRectF(-tw / 2, -tw / 2, tw, h * 0.34), tw / 2, tw / 2)
    tt = QTransform()
    tt.translate(X(0.30), Y(0.66))
    tt.rotate(142.0)                                   # from the palm: tip up and out to the left
    hand = hand.united(tt.map(th))
    parts = {
        "palm": QRectF(X(0.24), Y(0.36), w * 0.70, h * 0.45),
        "notch": [(X(cx + _FINGER_W / 2), Y(max(a, b) + 0.13)) for cx, a, b in
                  zip(_FINGER_X[:-1], _FINGER_TOP[:-1], _FINGER_TOP[1:])],
        "finger_base": Y(0.47),
        "stitch_top": Y(0.49), "stitch_bottom": Y(0.59), "stitch_x": (X(0.47), X(0.59), X(0.71)),
        "icon_area": QRectF(X(0.36), Y(0.605), w * 0.46, h * 0.165),
        "cuff": QRectF(X(0.30), Y(0.78), w * 0.58, h * 0.22),
    }
    return hand, parts


def draw_glove(p: QPainter, rect: QRectF, colors: "dict[str, QColor]", icon: "QImage | None" = None,
               mirror: bool = False) -> "QRectF | None":
    """A glove: a clean ink outline, short stitch lines on the back, a soft rolled cuff.  If
    ``icon`` is given it sits on the back of the hand just below the stitch lines, fit inside
    ~40% of the glove width and clipped to the glove."""
    w, h = rect.width(), rect.height()
    lw = max(1.4, h * 0.038)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    if mirror:
        p.translate(rect.center().x(), 0)
        p.scale(-1, 1)
        p.translate(-rect.center().x(), 0)
    hand, parts = glove_path(rect)
    ink = colors["glove_outline"]
    # cuff first (a soft, slightly flared band), so the hand overlaps its top edge
    c = parts["cuff"]
    cuff = QPainterPath(QPointF(c.left() + c.width() * 0.10, c.top()))
    cuff.lineTo(c.right() - c.width() * 0.10, c.top())
    cuff.cubicTo(QPointF(c.right() + c.width() * 0.10, c.top() + c.height() * 0.15),
                 QPointF(c.right() + c.width() * 0.10, c.bottom() - c.height() * 0.1),
                 QPointF(c.right() - c.width() * 0.04, c.bottom()))
    cuff.lineTo(c.left() + c.width() * 0.04, c.bottom())
    cuff.cubicTo(QPointF(c.left() - c.width() * 0.10, c.bottom() - c.height() * 0.1),
                 QPointF(c.left() - c.width() * 0.10, c.top() + c.height() * 0.15),
                 QPointF(c.left() + c.width() * 0.10, c.top()))
    cuff.closeSubpath()
    p.setPen(_pen(ink, lw))
    p.setBrush(colors["cuff"])
    p.drawPath(cuff)
    # the hand: fingers, thumb and palm are one soft shape
    p.setPen(_pen(ink, lw))
    p.setBrush(colors["glove"])
    p.drawPath(hand)
    # the cuff's rolled edge sits over the hand's wrist end
    p.setPen(_pen(ink, lw * 0.8))
    p.drawLine(QPointF(c.left() + c.width() * 0.02, c.top()), QPointF(c.right() - c.width() * 0.02, c.top()))
    # finger separations: short creases continuing each notch down into the hand
    p.setPen(_pen(ink, lw * 0.75))
    for x, y in parts["notch"]:
        p.drawLine(QPointF(x, y), QPointF(x, parts["finger_base"]))
    # three stitch lines on the back of the hand
    p.setPen(_pen(colors["stitches"], lw * 0.7))
    for x in parts["stitch_x"]:
        seam = QPainterPath(QPointF(x, parts["stitch_top"]))
        seam.quadTo(QPointF(x - w * 0.012, (parts["stitch_top"] + parts["stitch_bottom"]) / 2), QPointF(x, parts["stitch_bottom"]))
        p.drawPath(seam)
    icon_rect = None
    if icon is not None:
        area = parts["icon_area"]
        side = min(w * 0.40, area.height(), area.width())
        box = QRectF(area.center().x() - side / 2, area.center().y() - side / 2, side, side)
        p.save()
        p.setClipPath(hand)
        if mirror:                                          # the glove is flipped; the picture must not be
            p.translate(box.center().x(), 0)
            p.scale(-1, 1)
            p.translate(-box.center().x(), 0)
        icon_rect = _draw_icon(p, box, icon)
        p.restore()
        if icon_rect is not None and mirror:               # report in unmirrored painter space
            icon_rect = QRectF(2 * rect.center().x() - icon_rect.right(), icon_rect.top(), icon_rect.width(), icon_rect.height())
    p.restore()
    return icon_rect


HAND_TILT_APART = 10.0     # degrees: fingers lean toward each other while the hands wait
HAND_TILT_CLAPPED = 3.0


def hands_geometry(rect: QRectF, open_: float) -> dict:
    """Where the two gloves sit for a given openness (1 = apart, ready to clap; 0 = clapped).
    The hands MOVE: each glove slides toward the middle (and straightens up a little) until
    the two meet, so a clap is the hands coming together, not a rotation.  The gloves are
    bottom-aligned in the item's rect and leave headroom above for the tilt and the impact."""
    w = rect.width()
    gw = w * 0.45
    gh = gw * 1.50
    # the PALMS meet (the thumbs tuck in front of the other hand): measure the palm's inner edge
    _, parts = glove_path(QRectF(0, 0, gw, gh))
    inner = parts["palm"].left()                             # distance from the glove box's left edge
    gap = w * (-0.01 + 0.38 * open_)                         # between the two palms' inner edges
    cx = rect.center().x()
    top = rect.bottom() - gh
    front = QRectF(cx + gap / 2 - inner, top, gw, gh)
    back = QRectF(cx - gap / 2 - (gw - inner), top, gw, gh)
    tilt = HAND_TILT_CLAPPED + (HAND_TILT_APART - HAND_TILT_CLAPPED) * open_
    return {"back": back, "front": front, "tilt": tilt, "meet": QPointF(cx, top + gh * 0.28)}


def draw_hands(p: QPainter, rect: QRectF, pose: ClapPose, colors: "dict[str, QColor]",
               icon: "QImage | None" = None) -> "QRectF | None":
    """Two cartoon gloves clapping.  The FRONT glove shows the back of its hand, which is where
    the custom icon goes."""
    open_ = clamp01(pose.open)
    g = hands_geometry(rect, open_)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    if pose.squash > 0:
        p.translate(rect.center().x(), rect.bottom())
        p.scale(1.0 + pose.squash / rect.width() * 0.5, max(0.6, 1.0 - pose.squash / rect.height()))
        p.translate(-rect.center().x(), -rect.bottom())
    icon_rect = None
    for key, mirror, sign in (("back", True, 1.0), ("front", False, -1.0)):
        r = g[key]
        p.save()
        pivot = QPointF(r.center().x(), r.bottom())
        p.translate(pivot)
        p.rotate(sign * g["tilt"])                         # fingers lean in toward each other
        p.translate(-pivot)
        got = draw_glove(p, r, colors, icon if key == "front" else None, mirror=mirror)
        if key == "front" and got is not None:
            t = QTransform()
            t.translate(pivot.x(), pivot.y())
            t.rotate(sign * g["tilt"])
            t.translate(-pivot.x(), -pivot.y())
            icon_rect = t.mapRect(got)
        p.restore()
    p.restore()
    if pose.impact > 0.02:
        _impact_lines(p, g["meet"], rect.width() * 0.17, pose.impact, colors["lines"],
                      max(1.4, rect.width() * 0.03), angles=(-125, -90, -55))
    return icon_rect


def draw_item(p: QPainter, rect: QRectF, style: str, pose: ClapPose, colors: "dict[str, QColor]",
              icon: "QImage | None" = None, opacity: float = 1.0) -> "QRectF | None":
    """The clapper or the hands.  At ``opacity`` < 1 the whole item is composed fully opaque on a
    layer first and the layer is drawn at that opacity, so overlapping parts do not show through
    each other (a stripe over the board, a glove over the other glove)."""
    opacity = clamp01(opacity)
    if opacity >= 0.995:
        if style == "hands":
            return draw_hands(p, rect, pose, colors, icon)
        return draw_clapper(p, rect, pose, colors, icon)
    if opacity <= 0.003:
        return None
    dpr = max(1.0, float(p.device().devicePixelRatioF()))
    m = rect.width() * 0.35                              # room for impact lines, squash and tilted hands
    box = rect.adjusted(-m, -m, m, m)
    layer = QImage(int(math.ceil(box.width() * dpr)), int(math.ceil(box.height() * dpr)), QImage.Format_ARGB32_Premultiplied)
    layer.setDevicePixelRatio(dpr)
    layer.fill(Qt.transparent)
    q = QPainter(layer)
    q.setRenderHint(QPainter.Antialiasing, True)
    q.setRenderHint(QPainter.SmoothPixmapTransform, True)
    q.translate(-box.topLeft())
    got = draw_hands(q, rect, pose, colors, icon) if style == "hands" else draw_clapper(q, rect, pose, colors, icon)
    q.end()
    p.save()
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.setOpacity(p.opacity() * opacity)
    p.drawImage(box.topLeft(), layer)
    p.restore()
    return got


# ------------------------------------------------------------------ loading circle

def draw_circle(p: QPainter, center: QPointF, diameter: float, angle: float, color: QColor,
                opacity: float = 0.55, alpha: float = 1.0) -> None:
    """The small semi-transparent loading ring: a faint full track plus a ~250 degree arc
    rotating about its centre (``angle`` in degrees).  The ring is composed fully opaque on a
    small layer and that layer is drawn at ``opacity * alpha`` -- so "55% opacity" really is
    55%, however the track / shadow / arc overlap."""
    a = clamp01(alpha) * clamp01(opacity)
    if a <= 0.003:
        return
    dpr = max(1.0, float(p.device().devicePixelRatioF()))
    side = int(math.ceil(diameter + 8))
    layer = QImage(int(side * dpr), int(side * dpr), QImage.Format_ARGB32_Premultiplied)
    layer.setDevicePixelRatio(dpr)
    layer.fill(Qt.transparent)
    q = QPainter(layer)
    q.setRenderHint(QPainter.Antialiasing, True)
    th = max(2.0, diameter * 0.14)
    r = (diameter - th) / 2
    rect = QRectF(side / 2 - r, side / 2 - r, 2 * r, 2 * r)
    base = QColor(color)
    base.setAlpha(255)
    shadow = QColor(0, 0, 0, 90)
    pen = QPen(shadow, th + 2.0)
    pen.setCapStyle(Qt.RoundCap)
    q.setPen(pen)
    q.setBrush(Qt.NoBrush)
    q.drawArc(rect, int(-angle * 16), int(-250 * 16))
    track = mix(QColor(0, 0, 0, 0), base, 0.28)
    q.setPen(QPen(track, th))
    q.drawEllipse(rect)
    pen = QPen(base, th)
    pen.setCapStyle(Qt.RoundCap)
    q.setPen(pen)
    q.drawArc(rect, int(-angle * 16), int(-250 * 16))
    q.end()
    p.save()
    p.setOpacity(p.opacity() * a)
    p.drawImage(QPointF(center.x() - side / 2, center.y() - side / 2), layer)
    p.restore()


CIRCLE_TURN_SECONDS = 1.0


def circle_angle(t_seconds: float) -> float:
    return (t_seconds / CIRCLE_TURN_SECONDS * 360.0) % 360.0


def circle_fail(t: float) -> "tuple[float, float, float]":
    """overlay_fail: the circle flashes red, shakes a little and fades out.
    t in [0, 1] -> (dx px, red amount 0..1, alpha 0..1)."""
    t = clamp01(t)
    shake = math.sin(t * 2 * math.pi * 4.5) * 4.0 * (1.0 - t)
    red = ease_out_quad(min(1.0, t * 5.0))
    alpha = 1.0 if t < 0.55 else 1.0 - (t - 0.55) / 0.45
    return shake, red, clamp01(alpha)


PULSE_SECONDS = 0.40


def pulse_scale(progress: float) -> float:
    """The ring's bump when a stage completes: 1 -> ~1.3 -> 1 over ``PULSE_SECONDS``."""
    if progress < 0 or progress >= 1:
        return 1.0
    return 1.0 + 0.30 * math.sin(math.pi * clamp01(progress)) ** 1.5


def draw_halo(p: QPainter, center: QPointF, diameter: float, color: QColor, progress: float,
              opacity: float = 0.55, alpha: float = 1.0) -> None:
    """A thin ring that expands from the loading ring and fades -- the "ping" that goes with the
    pulse.  ``progress`` in [0, 1); outside it nothing is drawn."""
    if progress < 0 or progress >= 1:
        return
    a = clamp01(opacity) * clamp01(alpha) * (1.0 - ease_out_quad(progress)) * 0.9
    if a <= 0.01:
        return
    r = diameter / 2.0 * (1.0 + 0.85 * ease_out_cubic(progress))
    c = QColor(color)
    c.setAlpha(255)
    pen = QPen(c, max(1.5, diameter * 0.07 * (1.0 - 0.5 * progress)))
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setOpacity(p.opacity() * a)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(center, r, r)
    p.restore()


def draw_badge(p: QPainter, rect: QRectF, n: int, colors: "dict[str, QColor]") -> None:
    """The "+N" badge for captures beyond the visible stack."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    h = rect.height()
    pill = QRectF(rect.center().x() - h * 0.9, rect.top(), h * 1.8, h)
    p.setPen(_pen(colors["outline"], max(1.2, h * 0.08)))
    p.setBrush(colors["board"])
    p.drawRoundedRect(pill, h / 2, h / 2)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(max(8, int(h * 0.62)))
    p.setFont(f)
    p.setPen(colors["lines"])
    p.drawText(pill, Qt.AlignCenter, f"+{n}")
    p.restore()


# ------------------------------------------------------------------ transforms

@dataclass
class Xform:
    dx: float = 0.0
    dy: float = 0.0
    rot: float = 0.0                 # degrees, clockwise, about the pivot
    scale: float = 1.0
    sx: float = 1.0                  # extra horizontal scale (flip)
    sy: float = 1.0                  # extra vertical scale (squash)
    skew: float = 0.0                # horizontal shear
    opacity: float = 1.0
    pivot: "tuple[float, float]" = (0.0, 0.0)    # offset from the rect's centre (rotate / scale about it)


def xform_transform(rect: QRectF, xf: Xform) -> QTransform:
    c = QPointF(rect.center().x() + xf.pivot[0], rect.center().y() + xf.pivot[1])
    t = QTransform()
    t.translate(xf.dx, xf.dy)
    t.translate(c.x(), c.y())
    t.rotate(xf.rot)
    if xf.skew:
        t.shear(xf.skew, 0.0)
    t.scale(xf.scale * xf.sx, xf.scale * xf.sy)
    t.translate(-c.x(), -c.y())
    return t


def apply_xform(p: QPainter, rect: QRectF, xf: Xform) -> None:
    p.setTransform(xform_transform(rect, xf) * p.transform())
    p.setOpacity(p.opacity() * clamp01(xf.opacity))


def mapped_rect(rect: QRectF, xf: Xform) -> QRectF:
    return xform_transform(rect, xf).mapRect(rect)


def is_gone(rect: QRectF, xf: Xform, surface: QRectF) -> bool:
    """True when nothing of the item would be visible inside ``surface``."""
    if xf.opacity <= 0.02 or xf.scale * abs(xf.sx) <= 0.02 or xf.scale * abs(xf.sy) <= 0.02:
        return True
    inter = mapped_rect(rect, xf).intersected(surface)
    return inter.width() < 0.5 or inter.height() < 0.5


# ------------------------------------------------------------------ animations

@dataclass
class Space:
    """How far the item can/must travel, derived from its resting rect inside the overlay
    surface (``Space.for_rect``).  ``edge_travel``: along the anchor's edge direction until
    fully off the screen.  ``room_up`` / ``room_down``: until fully above / below the
    surface.  ``up_screen`` / ``down_screen``: that side of the surface IS a screen edge
    (an item leaving through it needs no fade)."""
    w: float
    h: float
    edge_travel: float
    room_up: float
    room_down: float
    up_screen: bool = False
    down_screen: bool = False
    lateral: float = 1.0          # horizontal direction toward the screen's middle (see lateral_toward_centre)

    @classmethod
    def for_rect(cls, anchor: str, rect: QRectF, surface_w: float, surface_h: float) -> "Space":
        ex, ey = edge_vec(anchor)
        if ex > 0:
            travel = surface_w - rect.left()
        elif ex < 0:
            travel = rect.right()
        elif ey < 0:
            travel = rect.bottom()
        else:
            travel = surface_h - rect.top()
        return cls(rect.width(), rect.height(), travel, rect.bottom(), surface_h - rect.top(),
                   up_screen=anchor in ("top", "top_left", "top_right"),
                   down_screen=anchor in ("bottom", "bottom_left", "bottom_right"),
                   lateral=lateral_toward_centre(anchor))


def edge_vec(anchor: str) -> "tuple[int, int]":
    """Unit vector from the screen centre toward the edge the item comes in through / leaves by.
    Corners use their TOP or BOTTOM edge (never the side edges); only the left / right anchors use
    a side edge; the centre has no edge of its own and uses the bottom one."""
    if anchor == "right":
        return (1, 0)
    if anchor == "left":
        return (-1, 0)
    if anchor in ("top", "top_left", "top_right"):
        return (0, -1)
    return (0, 1)


def lateral_toward_centre(anchor: str) -> float:
    """+1 / -1: which horizontal direction leads toward the middle of the screen (sideways arcs
    and launches go that way, so they never run off the nearest side edge)."""
    return -1.0 if anchor in ("right", "top_right", "bottom_right") else 1.0


def _rot_sign(e) -> float:
    """Which way things tumble when they travel along edge direction ``e``."""
    return 1.0 if (e[0] > 0 or (e[0] == 0 and e[1] > 0)) else -1.0


def _lateral(e) -> "tuple[float, float]":
    """A unit vector perpendicular to the edge direction (the arc's sideways axis for
    top / bottom anchors), always pointing right / down-ish."""
    return (0.0, 1.0) if e[0] != 0 else (1.0, 0.0)


# (milliseconds)
DURATION_MS = {
    "enter": {"slide": 300, "drop": 450, "pop": 300, "swing": 500, "spin": 400, "toss": 450,
              "flip": 300, "peek": 550, "fade": 250},
    "exit": {"slide": 300, "zip": 250, "fall": 400, "shrink": 250, "spin": 400, "toss": 500,
             "flip": 250, "fade": 250, "bow": 550},
    "fail": {"fail": 900},
}


def duration_ms(phase: str, kind: str) -> float:
    return float(DURATION_MS[phase].get(kind) or next(iter(DURATION_MS[phase].values())))


def _off(e, m) -> "tuple[float, float]":
    return e[0] * m, e[1] * m


def _arc(e, height: float, s: float, lateral: float = 1.0) -> "tuple[float, float]":
    """A sideways arc offset (``height`` px at its peak, shaped by s in [0,1]): up for
    left/right edges, sideways -- toward the screen's middle -- for top/bottom edges."""
    if e[0] != 0:
        return (0.0, -height * s)
    return (height * s * lateral, 0.0)


def _enter(kind: str, t: float, e, sp: Space) -> Xform:
    t = clamp01(t)
    ex, ey = e
    along = sp.w if ex != 0 else sp.h
    rs = _rot_sign(e)
    H = sp.h
    if kind == "slide":
        dx, dy = _off(e, sp.edge_travel * (1 - ease_out_back(t, 1.1)))
        return Xform(dx=dx, dy=dy)
    if kind == "drop":
        pivot = (0.0, sp.h / 2)
        fall_end = 0.55
        if t < fall_end:
            u = t / fall_end
            dy = -sp.room_up * (1 - u * u)
            op = 1.0 if sp.up_screen else clamp01(u / 0.25)
            return Xform(dy=dy, opacity=op, pivot=pivot)
        dy = 0.0
        v1 = (t - fall_end) / 0.25
        if 0 <= v1 < 1:
            dy = -0.14 * H * 4 * v1 * (1 - v1)
        v2 = (t - 0.80) / 0.13
        if 0 <= v2 < 1:
            dy = -0.05 * H * 4 * v2 * (1 - v2)
        squash = max(0.0, 1.0 - (t - fall_end) / 0.07)
        squash2 = max(0.0, 1.0 - abs(t - 0.80) / 0.04) * 0.4 if t >= 0.80 else 0.0
        s = max(squash, squash2)
        return Xform(dy=dy, sx=1 + 0.10 * s, sy=1 - 0.14 * s, pivot=pivot)
    if kind == "pop":
        if t < 0.5:
            sc = 1.15 * ease_out_cubic(t / 0.5)
        elif t < 0.8:
            sc = lerp(1.15, 0.95, ease_in_out((t - 0.5) / 0.3))
        else:
            sc = lerp(0.95, 1.0, ease_in_out((t - 0.8) / 0.2))
        return Xform(scale=sc, opacity=clamp01(t * 6))
    if kind == "swing":
        L = 2.2 * H
        amp = -ex * 62.0 if ex else -38.0       # top / bottom anchors have no side edge to hide behind: smaller swing
        ang = amp * (1 - t) ** 1.5 * math.cos(2 * math.pi * 1.25 * t)
        return Xform(rot=ang, pivot=(0.0, -L), opacity=clamp01(t / 0.25))
    if kind == "spin":
        p = ease_out_cubic(t)
        dx, dy = _off(e, sp.edge_travel * (1 - p))
        return Xform(dx=dx, dy=dy, rot=-rs * 360.0 * (1 - p))
    if kind == "toss":
        if t < 0.8:
            u = t / 0.8
            dx, dy = _off(e, sp.edge_travel * (1 - ease_out_quad(u)))
            ax, ay = _arc(e, min(0.9 * H, sp.room_up * 0.8) if ex != 0 else 0.9 * H, math.sin(math.pi * u), sp.lateral)
            return Xform(dx=dx + ax, dy=dy + ay, rot=-rs * 540.0 * (1 - ease_out_quad(u)))
        hop = math.sin(math.pi * (t - 0.8) / 0.2)
        ax, ay = _arc(e, 0.12 * H, hop, sp.lateral)
        return Xform(dx=ax, dy=ay)
    if kind == "flip":
        p = ease_out_cubic(t)
        return Xform(sx=max(p, 0.0), skew=0.18 * (1 - p) * rs, opacity=clamp01(t * 5))
    if kind == "peek":
        half = sp.edge_travel - along / 2
        if t < 0.30:
            m = lerp(sp.edge_travel, half, ease_out_cubic(t / 0.30))
            hop = 0.0
        elif t < 0.50:
            m, hop = half, 0.0
        else:
            v = (t - 0.50) / 0.50
            m = half * (1 - ease_out_back(v, 1.2))
            hop = math.sin(math.pi * v)
        dx, dy = _off(e, m)
        ax, ay = _arc(e, 0.18 * H, hop, sp.lateral)
        return Xform(dx=dx + ax, dy=dy + ay)
    if kind == "fade":
        p = ease_out_cubic(t)
        return Xform(dy=12.0 * (1 - p), opacity=p)
    raise ValueError(f"unknown enter animation: {kind!r}")


def _windup(t: float, e, sp: Space, frac: float, back: float, extra: float = 0.0) -> "tuple[float, float]":
    """Pull back a little against the exit direction, then accelerate out through the edge
    (``extra`` px beyond fully-off, for animations that also stretch)."""
    along = sp.w if e[0] != 0 else sp.h
    if t < frac:
        m = -back * along * math.sin(math.pi / 2 * t / frac)
    else:
        m = lerp(-back * along, sp.edge_travel + extra, ease_in_cubic((t - frac) / (1 - frac)))
    return _off(e, m)


def _exit(kind: str, t: float, e, sp: Space) -> Xform:
    t = clamp01(t)
    ex, ey = e
    rs = _rot_sign(e)
    H = sp.h
    if kind == "slide":
        dx, dy = _windup(t, e, sp, 0.25, 0.12)
        return Xform(dx=dx, dy=dy)
    if kind == "zip":
        along = sp.w if ex else sp.h
        dx, dy = _windup(t, e, sp, 0.30, 0.10, extra=0.12 * along)
        stretch = 1.0 + 0.15 * clamp01((t - 0.3) / 0.3)
        return Xform(dx=dx, dy=dy, sx=stretch if ex else 1.0, sy=stretch if not ex else 1.0)
    if kind == "fall":
        dy = (sp.room_down + 0.3 * sp.h) * ease_in_quad(t)     # until fully clear, tilt included
        op = 1.0 if sp.down_screen else clamp01((1 - t) / 0.25)
        return Xform(dy=dy, rot=rs * 14.0 * t, opacity=op)
    if kind == "shrink":
        return Xform(scale=max(0.0, 1.0 - ease_in_quad(t)), rot=rs * 120.0 * t, opacity=1.0 - t * t)
    if kind == "spin":
        dx, dy = _off(e, sp.edge_travel * ease_in_cubic(t))
        return Xform(dx=dx, dy=dy, rot=rs * 360.0 * ease_in_quad(t))
    if kind == "toss":
        if t < 0.15:
            ax, ay = _arc(e, 0.12 * H, math.sin(math.pi * t / 0.15), sp.lateral)
            return Xform(dx=ax, dy=ay)
        v = (t - 0.15) / 0.85
        dx, dy = _off(e, sp.edge_travel * ease_in_quad(v))
        ax, ay = _arc(e, min(0.8 * H, sp.room_up * 0.6) if ex != 0 else 0.8 * H, 4 * v * (1 - v), sp.lateral)
        return Xform(dx=dx + ax, dy=dy + ay, rot=-rs * 720.0 * v)
    if kind == "flip":
        return Xform(sx=max(0.0, 1.0 - ease_in_cubic(t)), skew=-0.18 * t * rs, opacity=clamp01((1 - t) * 6))
    if kind == "fade":
        return Xform(dy=12.0 * ease_in_quad(t), opacity=1.0 - ease_in_quad(t))
    if kind == "bow":
        pivot = (0.0, sp.h / 2)
        if t < 0.4:
            return Xform(rot=rs * 20.0 * math.sin(math.pi * t / 0.4), pivot=pivot)
        dx, dy = _off(e, sp.edge_travel * ease_in_cubic((t - 0.4) / 0.6))
        return Xform(dx=dx, dy=dy)
    raise ValueError(f"unknown exit animation: {kind!r}")


def _fail(t: float, e, sp: Space) -> Xform:
    """Launched up and away from the edge with a fast spin; gravity takes over; falls off the
    bottom of the surface.  Rises at most ``min(1.6 h, 0.9 room_up)`` so the arc stays inside."""
    t = clamp01(t)
    ex, ey = e
    rs = _rot_sign(e)
    hp = max(0.2 * sp.h, min(1.6 * sp.h, sp.room_up * 0.9))
    d = sp.room_down + 0.2 * sp.h
    s = math.sqrt(hp) + math.sqrt(hp + d)               # solves G - 2 sqrt(hp G) - d = 0 for sqrt(G)
    G = s * s
    V = 2 * math.sqrt(hp * G)
    dy = -V * t + G * t * t
    away = -ex if ex else sp.lateral                           # away from the edge, toward the screen's middle
    dx = away * 1.2 * sp.w * ease_out_quad(t)
    rot = rs * 1080.0 * (1 - (1 - t) ** 2) * 0.85
    op = 1.0 if sp.down_screen else clamp01((1 - t) / 0.15)
    return Xform(dx=dx, dy=dy, rot=rot, opacity=op)


# Animations that travel through the edge.  The centre has no screen edge, so for these the item
# fades in / out while it travels instead of appearing from the overlay surface's invisible boundary.
_EDGE_KINDS = {"enter": ("slide", "spin", "toss", "peek", "swing"), "exit": ("slide", "zip", "spin", "toss", "bow")}


def animate(phase: str, kind: str, t: float, anchor: str, space: Space) -> Xform:
    """The transform for ``phase`` ("enter" | "exit" | "fail") of animation ``kind`` at
    progress ``t`` in [0, 1]. ``enter`` ends at the identity transform; ``exit`` starts at
    it and ends gone; ``fail`` starts at it and falls off the bottom of the surface."""
    e = edge_vec(anchor)
    if phase == "enter":
        xf = _enter(kind, t, e, space)
        if anchor == "center" and kind in _EDGE_KINDS["enter"]:
            xf.opacity *= clamp01(t / 0.5)
        return xf
    if phase == "exit":
        xf = _exit(kind, t, e, space)
        if anchor == "center" and kind in _EDGE_KINDS["exit"]:
            xf.opacity *= clamp01((1.0 - t) / 0.5)
        return xf
    if phase == "fail":
        return _fail(t, e, space)
    raise ValueError(f"unknown animation phase: {phase!r}")
