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
    QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPainterPathStroker, QPen, QRadialGradient, QTransform, QFont,
)

# ------------------------------------------------------------------ sizes / colours

ASPECT = 0.86          # clapper height / width


def item_size(size: float) -> QSizeF:
    """The item's box for a given width setting."""
    return QSizeF(float(size), float(size) * ASPECT)


def circle_diameter(size: float) -> float:
    return max(20.0, round(size * 0.30))


def afterglow_defaults(appearance=None, hands_look: str = "retro") -> "dict[str, str]":
    """The default part colours: the afterglow theme's own colours (``appearance`` is an
    ``config.AppearanceSettings``; None = the theme's built-in values).  Taken from the stored hex
    values rather than ``gui.theme.Theme`` so the headless daemon can compute them too.
    ``hands_look``: the retro look has its own glove colours (cream, black ink, red cuffs); the cel
    look uses the theme's."""
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
        **(_retro_colors() if hands_look == "retro" else {}),
    }


def _retro_colors() -> "dict[str, str]":
    from .hands2d import RETRO_COLORS
    return dict(RETRO_COLORS)


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

DEFAULT_COLORS = afterglow_defaults(hands_look="retro")


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
    # the Hands style keeps its own timeline and idles, so it needs a little more than the clapper:
    clap_ms: float = -1.0   # ms since the `clap` event while clapping, else -1
    age: float = 0.0        # seconds since the indicator appeared (drives the idle wobble)
    clapped: bool = False   # after the clap: the hands rest clasped
    front: str = "right"    # which glove ends up in front ("right" / "left")
    since_clap: float = -1.0  # seconds since the clap began (-1 before it): keeps the clasp's idle in step
    look: str = "retro"     # the Hands look: "retro" / "cel" (see hands2d.py)


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
#
# Two cartoon gloves -- three plump fingers and a thumb -- seen the way clapping hands are seen
# from the front: the RIGHT glove shows the back of the hand (stitching, the custom icon), the
# LEFT glove shows its palm.  Both therefore have the thumb on the left as drawn.  Every finger
# and thumb is a chain of joints drawn as a shaded tube (ink outline, shadow body, lit band,
# highlight), so the fingers bend: a bend shows as foreshortening plus a share of curl in the
# picture plane (``inplane``).
#
# The clap (``pose.clap_ms`` from the `clap` event): a wind-up (hands pull apart, fingers open
# wide), the swing in (with speed streaks), the IMPACT -- the clasp from the classic clapping
# photo: the right hand crosses over the left palm, its fingers curling over and tucking between
# the left thumb and fingers, its thumb lying across the heel of the left hand, the left fingers
# coming out past the right hand's pinky (the back-of-hand glove sits a little higher so the thumb
# below it has room) -- with a shock ring and impact lines, then the hands settle into a relaxed
# clasp and stay clasped, through the exit.  Before the clap (READY) and in the clasp (REST) every
# finger idles: each one bends a little on its own rhythm.
#
# Everything is laid out in the item's own rect (box units: fractions of its WIDTH, origin at its
# top-left), sized to fill it and centred on its middle -- the point every animation turns about.

from . import HANDS_CONTACT_MS, HANDS_SWING_MS, HANDS_WINDUP_MS   # noqa: E402

HANDS_IMPACT_MS = 80.0           # the hands close into the clasp this long after contact
HANDS_SETTLE_MS = 300.0          # then the clap state ends (the clasp keeps settling while the item moves on)
HANDS_CLAP_TOTAL_MS = HANDS_CONTACT_MS + HANDS_IMPACT_MS + HANDS_SETTLE_MS
HANDS_FX_MS = 300.0              # ring + lines fade over this long from contact

# glove anatomy, in hand units (1 = the glove's size), origin at the wrist, y up negative,
# drawn as a right hand from the back / a left hand from the palm (thumb on the left)
GLOVE_FINGERS = (   # name, anchor, segment lengths, width, base direction (deg, 0 = up)
    ("pinky", (0.175, -0.56), (0.17, 0.135, 0.10), 0.245, 9.0),
    ("middle", (0.005, -0.62), (0.21, 0.165, 0.12), 0.265, 0.0),
    ("index", (-0.165, -0.58), (0.195, 0.155, 0.115), 0.26, -8.0),
)
GLOVE_THUMB = ((-0.20, -0.16), (0.25, 0.16), 0.25, -58.0)   # leaves the palm low, sticking out like a real thumb
GLOVE_PALM = ((-0.21, -0.02), (-0.30, -0.22), (-0.30, -0.48), (-0.20, -0.63), (0.0, -0.69), (0.20, -0.63),
              (0.30, -0.48), (0.29, -0.22), (0.21, -0.02))
_FINGER_NAMES = ("index", "middle", "pinky")
_WOBBLE_PHASE = {"index": 0.0, "middle": 1.9, "pinky": 3.7, "thumb": 5.1}
_JOINT_SHARE = (1.0, 1.25, 0.8)  # how a bend spreads over the three joints (fingers curl as a whole)


@dataclass
class GlovePose:
    """One glove in box units.  ``bend``: per finger, three joint angles (deg, + = curl toward the
    palm); ``inplane``: how much of a bend shows as curl in the picture plane (signed: - = to the
    left); ``spread`` fans the fingers apart; ``thumb``: extra thumb direction; ``thumb_bend``."""
    x: float
    y: float
    rot: float
    s: float
    side: str                                   # "dorsal" (back of the hand) / "palmar" (palm)
    bend: dict
    inplane: float
    spread: float = 0.0
    thumb: float = 0.0
    thumb_bend: tuple = (0.0, 0.0)


def _gp(x, y, rot, s, side, bend, inplane, spread=0.0, thumb=0.0, thumb_bend=(0.0, 0.0)) -> GlovePose:
    b = bend if isinstance(bend, dict) else {n: tuple(bend) for n in _FINGER_NAMES}
    return GlovePose(x, y, rot, s, side, {n: tuple(b[n]) for n in _FINGER_NAMES}, inplane, spread, thumb, tuple(thumb_bend))


# the keyframes (left glove = palm view, right glove = back view; the right one ends up in front)
HANDS_READY = (
    _gp(0.240, 0.675, 11.0, 0.53, "palmar", (10, 12, 8), 0.45, 0.3, 0.0, (4, 6)),
    _gp(0.900, 0.675, -11.0, 0.53, "dorsal", (12, 14, 10), -0.45, 0.0, 6.0, (6, 8)),
)
HANDS_WINDUP = (
    _gp(0.155, 0.700, -6.0, 0.56, "palmar", (-6, -4, 0), 0.4, 1.0, -8.0, (0, 0)),
    _gp(0.925, 0.700, 6.0, 0.56, "dorsal", (-6, -4, 0), -0.4, 0.8, -8.0, (0, 0)),
)
# the back-of-hand glove sits well above the palm glove, so the thumb below it has room
HANDS_IMPACT = (
    _gp(0.392, 0.693, 20.0, 0.60, "palmar", {"index": (14, 18, 10), "middle": (12, 16, 10), "pinky": (10, 14, 8)}, 0.3, 1.0, 48.0, (6, 8)),
    _gp(0.682, 0.583, -40.0, 0.60, "dorsal", {"index": (38, 48, 32), "middle": (38, 48, 32), "pinky": (36, 44, 30)}, -0.42, 0.0, 0.0, (0, 0)),
)
HANDS_REST = (
    _gp(0.394, 0.693, 18.0, 0.60, "palmar", {"index": (12, 15, 8), "middle": (10, 13, 8), "pinky": (8, 11, 6)}, 0.3, 0.6, 46.0, (4, 6)),
    _gp(0.674, 0.598, -36.0, 0.60, "dorsal", {"index": (32, 40, 26), "middle": (32, 40, 26), "pinky": (30, 37, 24)}, -0.42, 0.0, 4.0, (0, 0)),
)
HANDS_CONTACT = QPointF(0.47, 0.20)    # where the hands meet (box units): the ring and lines start here


def _lerp_glove(a: GlovePose, b: GlovePose, t: float) -> GlovePose:
    t = clamp01(t)
    return GlovePose(
        lerp(a.x, b.x, t), lerp(a.y, b.y, t), lerp(a.rot, b.rot, t), lerp(a.s, b.s, t), b.side if t >= 0.5 else a.side,
        {n: tuple(lerp(x, y, t) for x, y in zip(a.bend[n], b.bend[n])) for n in _FINGER_NAMES},
        lerp(a.inplane, b.inplane, t), lerp(a.spread, b.spread, t), lerp(a.thumb, b.thumb, t),
        tuple(lerp(x, y, t) for x, y in zip(a.thumb_bend, b.thumb_bend)))


def _wobble(g: GlovePose, age: float, amp: float, hand_phase: float) -> GlovePose:
    """The idle: every finger bends and straightens a little on its own rhythm (all three joints of
    a finger together, as a finger really curls), the thumb too, and the hand sways a touch."""
    if amp <= 0:
        return g
    bend = {}
    for n in _FINGER_NAMES:
        w = amp * (0.5 + 0.5 * math.sin(2 * math.pi * (0.55 + 0.07 * _FINGER_NAMES.index(n)) * age + _WOBBLE_PHASE[n] + hand_phase))
        bend[n] = tuple(v + w * share for v, share in zip(g.bend[n], _JOINT_SHARE))
    tw = amp * 0.6 * (0.5 + 0.5 * math.sin(2 * math.pi * 0.5 * age + _WOBBLE_PHASE["thumb"] + hand_phase))
    sway = math.sin(2 * math.pi * 0.42 * age + hand_phase)
    return GlovePose(g.x, g.y + 0.006 * math.sin(2 * math.pi * 0.6 * age + hand_phase), g.rot + 1.6 * sway * amp / 5.0,
                     g.s, g.side, bend, g.inplane, g.spread, g.thumb, (g.thumb_bend[0] + tw, g.thumb_bend[1] + tw))


@dataclass
class HandsFrame:
    left: GlovePose
    right: GlovePose
    clasped: bool          # drawn interlocked (the left thumb over the right fingertips)
    squash: float          # 0..1 the impact squash
    fx: float              # 0..1 progress of the ring + lines, or -1
    swing: float           # 0..1 speed of the swing in (streaks), 0 = none
    swing_dir: float       # +1 hands moving inward


def hands_frame(pose: ClapPose) -> HandsFrame:
    """Where both gloves are for a pose: READY (idling) until the clap, then wind-up, swing,
    impact, settle, and REST (idling, clasped) after it."""
    age = max(0.0, pose.age)
    t = pose.clap_ms
    if t < 0:
        if pose.clapped:
            return HandsFrame(_wobble(HANDS_REST[0], age, 3.5, 0.0), _wobble(HANDS_REST[1], age, 3.5, 1.3), True, 0.0, -1.0, 0.0, 0.0)
        return HandsFrame(_wobble(HANDS_READY[0], age, 5.0, 0.0), _wobble(HANDS_READY[1], age, 5.0, 1.3), False, 0.0, -1.0, 0.0, 0.0)
    if t < HANDS_WINDUP_MS:                                   # pull apart, fingers open
        k = ease_out_cubic(t / HANDS_WINDUP_MS)
        L, R = (_lerp_glove(a, b, k) for a, b in zip(HANDS_READY, HANDS_WINDUP))
        return HandsFrame(L, R, False, 0.0, -1.0, 0.0, 0.0)
    if t < HANDS_CONTACT_MS:                                  # the swing in
        u = (t - HANDS_WINDUP_MS) / HANDS_SWING_MS
        k = ease_in_quad(u)
        L, R = (_lerp_glove(a, b, k) for a, b in zip(HANDS_WINDUP, HANDS_IMPACT))
        return HandsFrame(L, R, False, 0.0, -1.0, clamp01(0.4 + u), 1.0)
    since = t - HANDS_CONTACT_MS
    fx = since / HANDS_FX_MS if since < HANDS_FX_MS else -1.0
    if since < HANDS_IMPACT_MS:                               # the hit: pressed in, squashed
        sq = 1.0 - ease_out_quad(since / HANDS_IMPACT_MS) * 0.6
        streak = max(0.0, 1.0 - since / 50.0)
        return HandsFrame(HANDS_IMPACT[0], HANDS_IMPACT[1], True, sq, fx, streak, 1.0)
    k = ease_out_cubic((since - HANDS_IMPACT_MS) / HANDS_SETTLE_MS)
    L, R = (_lerp_glove(a, b, k) for a, b in zip(HANDS_IMPACT, HANDS_REST))
    amp = 3.5 * k                                            # the idle fades in as the hands settle
    L, R = _wobble(L, age, amp, 0.0), _wobble(R, age, amp, 1.3)
    return HandsFrame(L, R, True, 0.4 * (1.0 - k), fx, 0.0, 0.0)


def _smooth_open(points) -> QPainterPath:
    path = QPainterPath(points[0])
    n = len(points)
    for i in range(n - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1, p2 = points[i], points[i + 1]
        p3 = points[i + 2] if i + 2 < n else points[i + 1]
        path.cubicTo(p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0, p2)
    return path


def _smooth_closed(points) -> QPainterPath:
    n = len(points)
    path = QPainterPath(points[0])
    for i in range(n):
        p0, p1, p2, p3 = points[i - 1], points[i], points[(i + 1) % n], points[(i + 2) % n]
        path.cubicTo(p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0, p2)
    path.closeSubpath()
    return path


def _joint_points(anchor, segs, base_dir, bends, inplane, s) -> "list[QPointF]":
    """The joints of a finger: each bend turns it by ``bend * inplane`` in the picture plane and
    shortens what is seen of the rest of it (it bends toward / away from the viewer)."""
    pts = [QPointF(anchor[0] * s, anchor[1] * s)]
    d, cum = base_dir, 0.0
    for i, L in enumerate(segs):
        b = bends[i] if i < len(bends) else 0.0
        cum += b
        d += b * inplane
        proj = L * max(0.35, math.cos(math.radians(cum * (1.0 - abs(inplane)) * 0.9)))
        r = math.radians(d)
        last = pts[-1]
        pts.append(QPointF(last.x() + math.sin(r) * proj * s, last.y() - math.cos(r) * proj * s))
    return pts


def _tube(p: QPainter, pts, width: float, tones: dict, lw: float, depth: float = 0.0) -> QPainterPath:
    """A bent tube through ``pts`` lit from the upper left: ink outline, shadow body, a lit band and a
    highlight, clipped to the tube.  Returns its shape."""
    path = _smooth_open(pts)
    st = QPainterPathStroker()
    st.setWidth(width)
    st.setCapStyle(Qt.RoundCap)
    st.setJoinStyle(Qt.RoundJoin)
    shape = st.createStroke(path).simplified()
    p.strokePath(path, QPen(tones["ink"], width + 2 * lw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.save()
    p.setClipPath(shape, Qt.IntersectClip)
    p.fillPath(shape, mix(tones["shade"], tones["deep"], depth))
    p.strokePath(path.translated(-width * 0.13, -width * 0.10),
                 QPen(mix(tones["base"], tones["deep"], depth * 0.8), width * 0.78, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    hi = mix(tones["hi"], tones["deep"], depth * 0.6)
    hi.setAlphaF(0.9)
    p.strokePath(path.translated(-width * 0.26, -width * 0.18), QPen(hi, width * 0.30, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.restore()
    return shape


def _glove_tones(colors: "dict[str, QColor]") -> "dict[str, QColor]":
    g, ink, cuff, white = QColor(colors["glove"]), QColor(colors["glove_outline"]), QColor(colors["cuff"]), QColor("#ffffff")
    return {"base": g, "hi": mix(g, white, 0.85), "shade": mix(g, ink, 0.30), "deep": mix(g, ink, 0.5), "ink": ink,
            "cuff": cuff, "cuff_hi": mix(cuff, white, 0.4), "cuff_shade": mix(cuff, ink, 0.35), "stitch": QColor(colors["stitches"])}


def glove_transform(rect: QRectF, g: GlovePose) -> QTransform:
    w = rect.width()
    t = QTransform()
    t.translate(rect.left() + g.x * w, rect.top() + g.y * w)
    t.rotate(g.rot)
    return t


def _thumb_web(thumb, palm: QPainterPath, s: float) -> "tuple[QPainterPath, QPainterPath]":
    """The skin between the thumb and the hand: from partway up the thumb's inner side back to the
    palm's edge above the root, a soft concave curve.  Returns (the web's area, its free edge)."""
    r0, j1 = thumb[0], thumb[1]
    dx, dy = j1.x() - r0.x(), j1.y() - r0.y()
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    nx, ny = uy, -ux                                   # the thumb's inner side (toward the fingers)
    if ny > 0:
        nx, ny = -nx, -ny
    half = GLOVE_THUMB[2] * s / 2
    a = QPointF(r0.x() + ux * n * 0.62 + nx * half * 0.92, r0.y() + uy * n * 0.62 + ny * half * 0.92)
    # the palm's edge, a little above where the thumb leaves it
    target = QPointF(r0.x() - 0.06 * s, r0.y() - 0.24 * s)
    best, bd = target, 1e18
    for i in range(121):
        q = palm.pointAtPercent(i / 120.0)
        if q.x() > r0.x() + 0.05 * s:                  # only the thumb side of the palm
            continue
        d = (q.x() - target.x()) ** 2 + (q.y() - target.y()) ** 2
        if d < bd:
            best, bd = q, d
    b = best
    crotch = QPointF((a.x() + b.x()) / 2 + (r0.x() - (a.x() + b.x()) / 2) * 0.35,
                     (a.y() + b.y()) / 2 + (r0.y() - (a.y() + b.y()) / 2) * 0.35)
    edge = QPainterPath(a)
    edge.quadTo(crotch, b)
    area = QPainterPath(edge)
    area.lineTo(r0)
    area.closeSubpath()
    return area, edge


def glove_geometry(g: GlovePose, size: float) -> dict:
    """The glove's pieces in its own frame (origin at the wrist): finger joint lists, thumb joints,
    the palm path, the icon box (back of the hand) and the silhouette."""
    s = size
    fingers = {}
    for i, (name, anchor, segs, w, d) in enumerate(GLOVE_FINGERS):
        fingers[name] = _joint_points(anchor, segs, d + g.spread * (i - 1) * -6.0, g.bend[name], g.inplane, s)
    thumb = _joint_points(GLOVE_THUMB[0], GLOVE_THUMB[1], GLOVE_THUMB[3] + g.thumb, g.thumb_bend, -0.7, s)
    palm = _smooth_closed([QPointF(x * s, y * s) for x, y in GLOVE_PALM])
    sil = QPainterPath(palm)
    for name, anchor, segs, w, d in GLOVE_FINGERS:
        st = QPainterPathStroker()
        st.setWidth(w * s)
        st.setCapStyle(Qt.RoundCap)
        st.setJoinStyle(Qt.RoundJoin)
        sil = sil.united(st.createStroke(_smooth_open(fingers[name])))
    st = QPainterPathStroker()
    st.setWidth(GLOVE_THUMB[2] * s)
    st.setCapStyle(Qt.RoundCap)
    sil = sil.united(st.createStroke(_smooth_open(thumb)))
    web, web_edge = _thumb_web(thumb, palm, s)
    sil = sil.united(web)
    side = s * 0.24
    return {"fingers": fingers, "thumb": thumb, "palm": palm, "silhouette": sil, "web": web, "web_edge": web_edge,
            "icon_box": QRectF(-side / 2, -0.36 * s - side / 2, side, side)}


def draw_glove(p: QPainter, rect: QRectF, g: GlovePose, colors: "dict[str, QColor]",
               icon: "QImage | None" = None, parts=("cuff", "fingers", "thumb", "palm"),
               unflip_icon: bool = False) -> "QRectF | None":
    """One glove at its pose in ``rect``.  ``parts`` picks what to draw (so the clasp can put the left
    thumb over the right fingers): any of cuff, fingers, thumb, palm, thumb_only.  Returns the icon's
    rect (dorsal glove only) in the painter's coordinates."""
    w = rect.width()
    s = g.s * w
    lw = max(1.2, s * 0.034)
    tones = _glove_tones(colors)
    geo = glove_geometry(g, s)
    tr = glove_transform(rect, g)
    dorsal = g.side == "dorsal"
    icon_rect = None
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setTransform(tr, True)
    palm = geo["palm"]
    pg = QLinearGradient(QPointF(-0.32 * s, -0.72 * s), QPointF(0.30 * s, -0.10 * s))
    pg.setColorAt(0.0, tones["hi"])
    pg.setColorAt(0.45, tones["base"])
    pg.setColorAt(1.0, tones["shade"])
    if "shadow" in parts:
        p.save()
        p.translate(s * 0.03, s * 0.05)
        p.fillPath(geo["silhouette"], QColor(0, 0, 0, 38))
        p.restore()
    if "cuff" in parts:
        cuff = QPainterPath(QPointF(-0.25 * s, -0.07 * s))
        cuff.lineTo(0.25 * s, -0.07 * s)
        cuff.lineTo(0.23 * s, 0.17 * s)
        cuff.quadTo(QPointF(0, 0.21 * s), QPointF(-0.23 * s, 0.17 * s))
        cuff.closeSubpath()
        cg = QLinearGradient(QPointF(-0.25 * s, 0), QPointF(0.25 * s, 0))
        cg.setColorAt(0.0, tones["cuff_shade"])
        cg.setColorAt(0.3, tones["cuff_hi"])
        cg.setColorAt(0.65, tones["cuff"])
        cg.setColorAt(1.0, tones["cuff_shade"])
        p.setPen(_pen(tones["ink"], lw))
        p.setBrush(cg)
        p.drawPath(cuff)
        rim = QPainterPath()
        rim.addEllipse(QRectF(-0.28 * s, -0.12 * s, 0.56 * s, 0.11 * s))
        p.setBrush(Qt.NoBrush)
        p.setPen(_pen(tones["ink"], lw * 2.5))
        p.drawPath(rim)
        p.setPen(_pen(tones["cuff_hi"], lw * 1.1))
        p.drawPath(rim)
    if "fingers" in parts:
        for name, anchor, segs, fw, d in GLOVE_FINGERS:
            depth = {"pinky": 0.10, "middle": 0.04, "index": 0.0}[name]
            bent = max(0.0, sum(g.bend[name])) / 150.0
            _tube(p, geo["fingers"][name], fw * s, tones, lw, min(0.5, depth + bent * (0.35 if dorsal else 0.15)))
            if not dorsal and bent > 0.05:                   # palm side: the joint creases face the viewer
                c = QColor(tones["shade"])
                c.setAlpha(170)
                p.setPen(QPen(c, lw * 0.6, Qt.SolidLine, Qt.RoundCap))
                pts = geo["fingers"][name]
                for k in (1, 2):
                    a, q = pts[k - 1], pts[k]
                    dx, dy = q.x() - a.x(), q.y() - a.y()
                    n = math.hypot(dx, dy) or 1.0
                    nx, ny = -dy / n * fw * s * 0.25, dx / n * fw * s * 0.25
                    p.drawLine(QPointF(q.x() - nx, q.y() - ny), QPointF(q.x() + nx, q.y() + ny))
    if "palm" in parts:
        p.setPen(Qt.NoPen)
        p.setBrush(pg)
        p.drawPath(palm)
        dg = QLinearGradient(QPointF(0, -0.30 * s), QPointF(0, 0))
        c0, c1 = QColor(tones["deep"]), QColor(tones["deep"])
        c0.setAlpha(0)
        c1.setAlpha(100)
        dg.setColorAt(0.0, c0)
        dg.setColorAt(1.0, c1)
        p.setBrush(dg)
        p.drawPath(palm)
        if dorsal:
            for kx, ky in ((-0.15, -0.57), (0.0, -0.61), (0.16, -0.56)):      # knuckles catch the light
                rg = QRadialGradient(QPointF(kx * s, ky * s), 0.09 * s)
                rg.setColorAt(0.0, QColor(255, 255, 255, 160))
                rg.setColorAt(1.0, QColor(255, 255, 255, 0))
                p.setBrush(rg)
                p.drawEllipse(QPointF(kx * s, ky * s), 0.09 * s, 0.06 * s)
            p.setPen(QPen(tones["stitch"], lw * 0.7, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            for sx in (-0.10, 0.02, 0.14):
                sp = QPainterPath(QPointF(sx * s, -0.56 * s))
                sp.quadTo(QPointF((sx + 0.02) * s, -0.51 * s), QPointF(sx * s, -0.46 * s))
                p.drawPath(sp)
            if icon is not None:
                box = geo["icon_box"]
                p.save()
                p.setClipPath(palm, Qt.IntersectClip)
                if unflip_icon:
                    p.translate(box.center().x(), 0)
                    p.scale(-1, 1)
                    p.translate(-box.center().x(), 0)
                got = _draw_icon(p, box, icon)
                p.restore()
                if got is not None:
                    icon_rect = p.transform().mapRect(got)
        else:
            pad = QPainterPath()
            pad.addEllipse(QPointF(0.03 * s, -0.36 * s), 0.17 * s, 0.20 * s)
            rg = QRadialGradient(QPointF(0.06 * s, -0.33 * s), 0.22 * s)
            c, c2 = QColor(tones["shade"]), QColor(tones["shade"])
            c.setAlpha(120)
            c2.setAlpha(0)
            rg.setColorAt(0.0, c)
            rg.setColorAt(1.0, c2)
            p.setBrush(rg)
            p.setPen(Qt.NoPen)
            p.drawPath(pad)
            p.setPen(QPen(mix(tones["shade"], tones["ink"], 0.3), lw * 0.6, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            c1p = QPainterPath(QPointF(-0.18 * s, -0.47 * s))
            c1p.quadTo(QPointF(0.02 * s, -0.53 * s), QPointF(0.22 * s, -0.46 * s))
            p.drawPath(c1p)
            c2p = QPainterPath(QPointF(-0.10 * s, -0.20 * s))
            c2p.quadTo(QPointF(-0.08 * s, -0.33 * s), QPointF(-0.16 * s, -0.44 * s))
            p.drawPath(c2p)
        # the palm's outline, except where fingers / thumb leave it
        p.save()
        keep = QPainterPath()
        keep.addRect(QRectF(-3 * s, -3 * s, 6 * s, 6 * s))
        for name, anchor, segs, fw, d in GLOVE_FINGERS:
            st = QPainterPathStroker()
            st.setWidth(fw * s * 0.98)
            st.setCapStyle(Qt.RoundCap)
            keep = keep.subtracted(st.createStroke(_smooth_open(geo["fingers"][name])))
        st = QPainterPathStroker()                           # the thumb (and its web) always grow out of this edge
        st.setWidth(GLOVE_THUMB[2] * s * 0.98)
        st.setCapStyle(Qt.RoundCap)
        keep = keep.subtracted(st.createStroke(_smooth_open(geo["thumb"])))
        keep = keep.subtracted(geo["web"])
        p.setClipPath(keep, Qt.IntersectClip)
        p.setPen(_pen(tones["ink"], lw))
        p.setBrush(Qt.NoBrush)
        p.drawPath(palm)
        p.restore()
    if "thumb" in parts or "thumb_only" in parts:
        # the thumb grows out of the palm's edge: only what is outside the palm is drawn, so it has no
        # rounded end at its root and its outline stops where it meets the hand
        p.save()
        outside = QPainterPath()
        outside.addRect(QRectF(-3 * s, -3 * s, 6 * s, 6 * s))
        p.setClipPath(outside.subtracted(palm), Qt.IntersectClip)
        _tube(p, geo["thumb"], GLOVE_THUMB[2] * s, tones, lw)
        # the web of skin joining it to the hand, over the thumb's root
        web = geo["web"]
        p.setPen(Qt.NoPen)
        p.setBrush(pg)
        p.drawPath(web)
        wc = QColor(tones["deep"])
        wc.setAlpha(70)
        p.setBrush(wc)
        p.drawPath(web)
        p.setPen(_pen(tones["ink"], lw))
        p.setBrush(Qt.NoBrush)
        p.drawPath(geo["web_edge"])                        # only its part outside the palm shows
        p.restore()
    p.restore()
    return icon_rect


def _hands_fx(p: QPainter, rect: QRectF, k: float, tones: dict) -> None:
    """The hit: a shock ring that expands and fades, and short lines bursting out above it."""
    w = rect.width()
    c = QPointF(rect.left() + HANDS_CONTACT.x() * w, rect.top() + HANDS_CONTACT.y() * w)
    k = clamp01(k)
    fade = 1.0 - ease_in_quad(k)
    ring_col = mix(tones["cuff_hi"], QColor("#ffffff"), 0.5)
    for scale, alpha, width in ((1.0, 0.95, 0.050), (0.72, 0.55, 0.032)):
        r = w * (0.14 + 0.30 * ease_out_cubic(k)) * scale
        col = QColor(ring_col)
        col.setAlphaF(alpha * fade)
        p.setPen(QPen(col, max(1.4, w * width * (1.0 - 0.5 * k))))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(c, r, r * 0.82)
    col = QColor(ring_col)
    col.setAlphaF(fade)
    p.setPen(QPen(col, max(1.4, w * 0.028), Qt.SolidLine, Qt.RoundCap))
    top = QPointF(c.x(), c.y() - w * 0.05)
    for a in (-150, -120, -90, -60, -30):
        r = math.radians(a)
        d0 = w * (0.13 + 0.12 * ease_out_cubic(k))
        d1 = d0 + w * 0.09 * (1.0 - 0.5 * k)
        p.drawLine(QPointF(top.x() + math.cos(r) * d0, top.y() + math.sin(r) * d0),
                   QPointF(top.x() + math.cos(r) * d1, top.y() + math.sin(r) * d1))


def _hands_streaks(p: QPainter, rect: QRectF, g: GlovePose, outward: float, strength: float, tones: dict) -> None:
    """Speed lines trailing a glove that swings in (``outward``: -1 = they trail to the left)."""
    w = rect.width()
    s = g.s * w
    tr = glove_transform(rect, g)
    col = QColor(tones["hi"])
    col.setAlphaF(0.85 * clamp01(strength))
    p.save()
    p.setPen(QPen(col, max(1.4, w * 0.022), Qt.SolidLine, Qt.RoundCap))
    for i, fy in enumerate((-0.80, -0.55, -0.30)):
        a = tr.map(QPointF(outward * 0.34 * s, fy * s))
        ln = w * (0.20 - 0.04 * i) * clamp01(strength)
        p.drawLine(a, QPointF(a.x() + outward * ln, a.y() + w * 0.02))
    p.restore()


def draw_hands(p: QPainter, rect: QRectF, pose: ClapPose, colors: "dict[str, QColor]",
               icon: "QImage | None" = None) -> "QRectF | None":
    """The two gloves for ``pose``.  Drawn from the traced 2D frames (hands2d.py) in the pose's look;
    the articulated vector gloves below are the fallback when the frame data is missing.
    ``pose.front == "left"`` mirrors the whole picture (the left hand ends up in front); the icon is
    never mirrored.  Returns the icon's rect on the back-of-hand glove."""
    if _traced():
        from . import hands2d
        return hands2d.draw(p, rect, pose.look, pose.age, pose.since_clap, pose.clap_ms, pose.clapped, pose.front,
                            colors, icon)
    hf = hands_frame(pose)
    tones = _glove_tones(colors)
    flip = pose.front == "left"
    cx = rect.center().x()
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    if flip:
        p.translate(cx, 0)
        p.scale(-1, 1)
        p.translate(-cx, 0)
    if hf.squash > 0:                                        # the hit: squash about the bottom middle
        p.translate(cx, rect.bottom())
        p.scale(1.0 + 0.05 * hf.squash, 1.0 - 0.06 * hf.squash)
        p.translate(-cx, -rect.bottom())
    if hf.fx >= 0:
        _hands_fx(p, rect, hf.fx, tones)
    if hf.swing > 0:
        _hands_streaks(p, rect, hf.left, -1.0, hf.swing, tones)
        _hands_streaks(p, rect, hf.right, 1.0, hf.swing, tones)
    everything = ("shadow", "cuff", "fingers", "thumb", "palm")
    if hf.clasped:
        # back to front: the palm glove, the back-of-hand glove (its thumb aside), the palm glove's thumb
        # over the front fingertips, and the front thumb over the root of that one
        draw_glove(p, rect, hf.left, colors, None, ("shadow", "cuff", "fingers", "palm"))
        icon_rect = draw_glove(p, rect, hf.right, colors, icon, ("shadow", "cuff", "fingers", "palm"), unflip_icon=flip)
        draw_glove(p, rect, hf.left, colors, None, ("thumb_only",))
        draw_glove(p, rect, hf.right, colors, None, ("thumb_only",))
    else:
        draw_glove(p, rect, hf.left, colors, None, everything)
        icon_rect = draw_glove(p, rect, hf.right, colors, icon, everything, unflip_icon=flip)
    p.restore()
    return icon_rect


def _traced() -> bool:
    from . import hands2d
    return hands2d.available()


def hands_bounds(rect: QRectF, pose: ClapPose) -> QRectF:
    """The area both gloves cover for a pose (for tests and layout checks)."""
    if _traced():
        from . import hands2d
        return hands2d.bounds(rect, pose.look, pose.age, pose.since_clap, pose.clap_ms, pose.clapped, pose.front)
    hf = hands_frame(pose)
    out = QRectF()
    for g in (hf.left, hf.right):
        sil = glove_geometry(g, g.s * rect.width())["silhouette"]
        cuff = QRectF(-0.28 * g.s * rect.width(), -0.12 * g.s * rect.width(), 0.56 * g.s * rect.width(), 0.33 * g.s * rect.width())
        path = QPainterPath(sil)
        path.addRect(cuff)
        out = out.united(glove_transform(rect, g).map(path).boundingRect())
    if pose.front == "left":
        cx = rect.center().x()
        out = QRectF(2 * cx - out.right(), out.top(), out.width(), out.height())
    return out


def clap_total_ms(style: str) -> float:
    """How long the clap state lasts for a style (the hands wind up first, so theirs is longer)."""
    return HANDS_CLAP_TOTAL_MS if style == "hands" else CLAP_TOTAL_MS


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
