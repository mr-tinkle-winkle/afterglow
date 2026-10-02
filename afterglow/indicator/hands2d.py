"""
The Hands style, drawn from traced 2D frames.

The gloves were posed and animated in 3D (so the two hands interlock without passing through each
other), rendered, and traced into flat vector shapes frame by frame: fills, cel-shadow shapes, the
stitching, the ink lines (with their line weights) and the outer silhouette.  That data ships as
``resources/hands_frames.json.gz`` (made by ``tools/hands_bake/bake_hands.py``); this module only reads
it and draws it, in one of two looks with the clip type's colours:

  * ``retro`` (default): cream gloves, black ink, red cuffs, a deep shadow shape and pie-cut shines.
  * ``cel``: the afterglow theme's pale gloves, one hard shadow shape, tapered ink, a heavier outline.

Sequences (all coordinates in item-box units: fractions of the item's WIDTH, origin at its top-left):
  ready  2 s idle loop while waiting for the clap (30 fps)
  clap   from the clap event to the settled clasp (60 fps): wind-up, swing, impact, settle
  rest   2 s idle loop of the clasp (30 fps), phase-locked to the end of ``clap``
"""
from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPolygonF, QTransform

DATA_PATH = Path(__file__).resolve().parent / "resources" / "hands_frames.json.gz"
LOOKS = ("retro", "cel")
DEFAULT_LOOK = "retro"
CROSSFADE_MS = 80.0          # the clap starts from the idle's current frame: fade it out over this long
RING_MS = 500.0              # the shock ring's life, from contact

# the default glove colours of each look (the clapper's colours and the cel look follow the afterglow theme)
RETRO_COLORS = {"glove": "#fff7e8", "glove_outline": "#000000", "cuff": "#e0533c", "stitches": "#e0533c"}

_DATA: "dict | None" = None
_FRAMES: "dict[tuple[str, int], _Frame]" = {}


def available() -> bool:
    return DATA_PATH.exists()


def data() -> dict:
    global _DATA
    if _DATA is None:
        with gzip.open(DATA_PATH, "rb") as f:
            _DATA = json.loads(f.read().decode("utf-8"))
    return _DATA


def timing() -> dict:
    """Milliseconds from the clap event: ``windup_ms``, ``contact_ms`` (the hands meet), ``closed_ms``,
    ``settled_ms`` (the clasp is at rest)."""
    return data()["timing"]


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t, a.alphaF() + (b.alphaF() - a.alphaF()) * t)


def _points(flat, q):
    return [(flat[i] / q, flat[i + 1] / q) for i in range(0, len(flat) - 1, 2)]


def _smooth_path(contours) -> QPainterPath:
    """Closed contours through the midpoints of their (simplified) points, with quadratic corners:
    smooth curves from few points.  Odd-even fill, so holes stay holes."""
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    for pts in contours:
        n = len(pts)
        if n < 3:
            continue
        mid = lambda a, b: QPointF((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)   # noqa: E731
        path.moveTo(mid(pts[-1], pts[0]))
        for i in range(n):
            path.quadTo(QPointF(*pts[i]), mid(pts[i], pts[(i + 1) % n]))
        path.closeSubpath()
    return path


def _tapered(pts, widths, wscale: float, taper: float = 0.18) -> QPainterPath:
    """A drawn ink line: a filled shape whose width follows the traced line weight and thins toward
    open ends, with round ends."""
    path = QPainterPath()
    n = len(pts)
    if n < 2:
        return path
    seg = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(n - 1)]
    total = sum(seg) or 1e-9
    s = [0.0]
    for d in seg:
        s.append(s[-1] + d)
    closed = math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-3
    left, right = [], []
    for i in range(n):
        u = s[i] / total
        prof = 1.0 if closed else max(0.0, min(1.0, min(u, 1.0 - u) / taper)) ** 0.6
        w = widths[i] * wscale * (0.35 + 0.65 * prof)
        a = pts[max(0, i - 1)]
        b = pts[min(n - 1, i + 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(tx, ty) or 1e-9
        nx, ny = -ty / ln, tx / ln
        left.append(QPointF(pts[i][0] + nx * w / 2, pts[i][1] + ny * w / 2))
        right.append(QPointF(pts[i][0] - nx * w / 2, pts[i][1] - ny * w / 2))
    path.addPolygon(QPolygonF(left + right[::-1]))
    path.closeSubpath()
    if not closed:
        for i in (0, n - 1):
            w = widths[i] * wscale * 0.35
            path.addEllipse(QPointF(*pts[i]), w / 2, w / 2)
    path.setFillRule(Qt.WindingFill)
    return path


class _Frame:
    """One traced frame; its paths are built on first use (in box units) and kept."""

    def __init__(self, d: dict, q: float):
        self.d, self.q = d, q
        self._fills: "dict[str, QPainterPath]" = {}
        self._sil: "QPainterPath | None" = None
        self._lines: "dict[float, QPainterPath]" = {}
        self._clip: "QPainterPath | None" = None

    def fill(self, name: str) -> "QPainterPath | None":
        if name not in self._fills:
            cs = self.d.get("fills", {}).get(name)
            self._fills[name] = _smooth_path([_points(c, self.q) for c in cs]) if cs else None
        return self._fills[name]

    def silhouette(self) -> QPainterPath:
        if self._sil is None:
            self._sil = _smooth_path([_points(c, self.q) for c in self.d["sil"]])
        return self._sil

    def lines(self, wscale: float) -> QPainterPath:
        if wscale not in self._lines:
            path = QPainterPath()
            path.setFillRule(Qt.WindingFill)
            for ln in self.d.get("lines", []):
                pts = _points(ln["p"], self.q)
                ws = [w / self.q for w in ln["w"]]
                path.addPath(_tapered(pts, ws, wscale))
            self._lines[wscale] = path
        return self._lines[wscale]

    def icon_clip(self) -> "QPainterPath | None":
        ic = self.d.get("icon")
        if not ic:
            return None
        if self._clip is None:
            self._clip = _smooth_path([_points(c, self.q) for c in ic["clip"]])
        return self._clip

    @property
    def ink(self) -> float:
        return float(self.d.get("ink", 0.02))


def frame(seq: str, i: int) -> _Frame:
    key = (seq, i)
    fr = _FRAMES.get(key)
    if fr is None:
        D = data()
        frames = D[seq]["frames"]
        fr = _FRAMES[key] = _Frame(frames[max(0, min(len(frames) - 1, i))], float(D["q"]))
    return fr


def frame_for(age: float, since_clap: float, clap_ms: float, clapped: bool) -> "tuple[str, int, float]":
    """Which frame to show: (sequence, index, ms since the clap or -1).  ``since_clap`` (seconds since
    the clap began, -1 before) keeps the clasp's idle phase-locked to the end of the clap; without it
    (older callers) ``clap_ms`` is used while clapping and the rest loop runs on ``age``."""
    D = data()
    t_ms = clap_ms if clap_ms >= 0 else (since_clap * 1000.0 if since_clap >= 0 else -1.0)
    if t_ms < 0 and not clapped:
        n = len(D["ready"]["frames"])
        return "ready", int(age * D["ready"]["fps"]) % n, -1.0
    if t_ms < 0:
        n = len(D["rest"]["frames"])
        return "rest", int(age * D["rest"]["fps"]) % n, -1.0
    clap = D["clap"]
    i = int(t_ms / 1000.0 * clap["fps"])
    if i < len(clap["frames"]):
        return "clap", i, t_ms
    n = len(D["rest"]["frames"])
    return "rest", int(t_ms / 1000.0 * D["rest"]["fps"]) % n, t_ms


# ------------------------------------------------------------------ the two looks

def look_tones(look: str, colors: "dict[str, QColor]") -> dict:
    g, ink, cuff, st = colors["glove"], colors["glove_outline"], colors["cuff"], colors["stitches"]
    white = QColor("#ffffff")
    if look == "cel":
        return {"base": g, "shadow": _mix(g, ink, 0.17), "deep": None, "cuff": cuff, "cuff_shadow": _mix(cuff, ink, 0.30),
                "rim": _mix(cuff, white, 0.25), "rim_shadow": _mix(cuff, ink, 0.10), "cap": _mix(cuff, ink, 0.55),
                "stitch": st, "ink": ink, "line_w": 2.0, "sil_w": 2.5, "shines": None,
                "ring": _mix(_mix(cuff, white, 0.4), white, 0.5)}
    return {"base": g, "shadow": None, "deep": _mix(g, QColor(196, 140, 70), 0.28), "cuff": cuff,
            "cuff_shadow": _mix(cuff, ink, 0.28), "rim": _mix(cuff, white, 0.18), "rim_shadow": None,
            "cap": _mix(cuff, ink, 0.50), "stitch": st, "ink": ink, "line_w": 2.3, "sil_w": 3.6, "shines": white,
            "ring": _mix(_mix(cuff, white, 0.4), white, 0.5)}


def _draw_frame(p: QPainter, fr: _Frame, tones: dict, icon: "QImage | None", flip: bool) -> None:
    """Draw one frame in box units (the painter is already scaled so that 1 = the item's width)."""
    p.setPen(Qt.NoPen)
    p.setBrush(tones["base"])
    p.drawPath(fr.silhouette())
    order = (("glove_shadow", "shadow"), ("glove_deep", "deep"), ("cuff", "cuff"),
             ("cuff_shadow" if tones["shadow"] is not None else "cuff_deep", "cuff_shadow"),
             ("rim", "rim"), ("rim_shadow", "rim_shadow"), ("cap", "cap"), ("stitch", "stitch"))
    for name, tone in order:
        col = tones.get(tone)
        path = fr.fill(name) if col is not None else None
        if path is not None:
            p.setBrush(col)
            p.drawPath(path)
    if icon is not None:
        _draw_icon(p, fr, icon, flip)
    p.setBrush(tones["ink"])
    p.drawPath(fr.lines(tones["line_w"]))
    pen = QPen(tones["ink"], fr.ink * tones["sil_w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(fr.silhouette())
    if tones["shines"] is not None:
        for cx, cy, r in fr.d.get("shines", []):
            p.setPen(QPen(tones["shines"], r * 0.35, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(QRectF(cx - r * 0.6, cy - r * 0.6, r * 1.2, r * 1.2), 100 * 16, 70 * 16)


def _draw_icon(p: QPainter, fr: _Frame, icon: QImage, flip: bool) -> None:
    """The custom icon on the back of the right glove, clipped to what of it shows.  When the picture
    is mirrored (the left hand in front) the icon is mapped so that it still reads unmirrored."""
    ic = fr.d.get("icon")
    clip = fr.icon_clip()
    if not ic or clip is None or icon.width() <= 0 or icon.height() <= 0:
        return
    ox, oy, ux, uy, vx, vy = ic["quad"]          # top-left, top-right, bottom-left of the icon square
    if flip:                                     # the painter mirrors x: swap left / right so it reads right
        (ox, oy), (ux, uy) = (ux, uy), (ox, oy)
        vx, vy = vx + (ox - ic["quad"][0]), vy + (oy - ic["quad"][1])
    # keep the image's aspect inside the square
    iw, ih = float(icon.width()), float(icon.height())
    sx, sy = (1.0, ih / iw) if iw >= ih else (iw / ih, 1.0)
    ax, ay = (ux - ox), (uy - oy)
    bx, by = (vx - ox), (vy - oy)
    cx0 = ox + ax * (1 - sx) / 2 + bx * (1 - sy) / 2
    cy0 = oy + ay * (1 - sx) / 2 + by * (1 - sy) / 2
    tr = QTransform(ax * sx / iw, ay * sx / iw, bx * sy / ih, by * sy / ih, cx0, cy0)
    p.save()
    p.setClipPath(clip, Qt.IntersectClip)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    p.setTransform(tr, True)
    p.drawImage(0, 0, icon)
    p.restore()


def _ring(p: QPainter, centre: QPointF, k: float, col: QColor) -> None:
    """The shock ring and the short lines bursting out above it (box units)."""
    k = max(0.0, min(1.0, k))
    ease = 1 - (1 - k) ** 3
    fade = 1 - k * k
    for scale, alpha, width in ((1.0, 0.95, 0.050), (0.72, 0.55, 0.032)):
        r = (0.14 + 0.30 * ease) * scale
        c = QColor(col)
        c.setAlphaF(alpha * fade)
        p.setPen(QPen(c, width * (1.0 - 0.5 * k)))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(centre, r, r * 0.82)
    c = QColor(col)
    c.setAlphaF(fade)
    p.setPen(QPen(c, 0.028, Qt.SolidLine, Qt.RoundCap))
    top = QPointF(centre.x(), centre.y() - 0.05)
    for a in (-150, -120, -90, -60, -30):
        r = math.radians(a)
        d0 = 0.13 + 0.12 * ease
        d1 = d0 + 0.09 * (1.0 - 0.5 * k)
        p.drawLine(QPointF(top.x() + math.cos(r) * d0, top.y() + math.sin(r) * d0),
                   QPointF(top.x() + math.cos(r) * d1, top.y() + math.sin(r) * d1))


def _streaks(p: QPainter, st: dict) -> None:
    k = float(st["k"])
    if k <= 0.05:
        return
    col = QColor("#ffffff")
    col.setAlphaF(0.8 * k)
    p.setPen(QPen(col, 0.020, Qt.SolidLine, Qt.RoundCap))
    for gi, (px, py) in enumerate(st["palms"]):
        out = -1.0 if gi == 0 else 1.0
        for j, dy in enumerate((-0.16, 0.0, 0.16)):
            x0 = px + out * 0.22
            ln = (0.16 - 0.03 * j) * k
            p.drawLine(QPointF(x0, py + dy), QPointF(x0 + out * ln, py + dy + 0.015))


def draw(p: QPainter, rect: QRectF, look: str, age: float, since_clap: float, clap_ms: float, clapped: bool,
         front: str, colors: "dict[str, QColor]", icon: "QImage | None" = None) -> "QRectF | None":
    """The hands for this moment, in ``rect`` (the item's resting rect).  Returns the icon's bounding
    rect when it shows (for tests), else None."""
    look = look if look in LOOKS else DEFAULT_LOOK
    tones = look_tones(look, colors)
    seq, i, t_ms = frame_for(age, since_clap, clap_ms, clapped)
    fr = frame(seq, i)
    flip = front == "left"
    w = rect.width()
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    p.translate(rect.left(), rect.top())
    p.scale(w, w)
    if flip:
        p.translate(0.5, 0)
        p.scale(-1, 1)
        p.translate(-0.5, 0)
    sq = float(fr.d.get("squash", 0.0))
    if sq > 0:                                     # the hit: squash about the bottom middle
        bottom = data().get("aspect", 0.86)
        p.translate(0.5, bottom)
        p.scale(1.0 + 0.05 * sq, 1.0 - 0.06 * sq)
        p.translate(-0.5, -bottom)
    contact = timing()["contact_ms"]
    if t_ms >= contact and t_ms - contact < RING_MS:
        rx, ry = data()["fx"]["ring"]
        _ring(p, QPointF(rx, ry), (t_ms - contact) / RING_MS, tones["ring"])
    if "streak" in fr.d:
        _streaks(p, fr.d["streak"])
    _draw_frame(p, fr, tones, icon, flip)
    icon_rect = None
    if icon is not None and fr.d.get("icon"):
        icon_rect = p.transform().map(fr.icon_clip()).boundingRect()
    p.restore()
    # the clap begins from the pose the idle was in: fade that frame out over the first few frames
    if seq == "clap" and 0 <= t_ms < CROSSFADE_MS:
        ready = frame("ready", frame_for(age - t_ms / 1000.0, -1, -1, False)[1])
        _layered(p, rect, ready, tones, icon, flip, 1.0 - t_ms / CROSSFADE_MS)
    return icon_rect


def _layered(p: QPainter, rect: QRectF, fr: _Frame, tones: dict, icon, flip: bool, opacity: float) -> None:
    """Draw a frame on its own layer at ``opacity`` (so its parts do not show through each other)."""
    if opacity <= 0.003:
        return
    dpr = max(1.0, float(p.device().devicePixelRatioF())) if p.device() is not None else 1.0
    m = rect.width() * 0.35
    box = rect.adjusted(-m, -m, m, m)
    layer = QImage(int(math.ceil(box.width() * dpr)), int(math.ceil(box.height() * dpr)), QImage.Format_ARGB32_Premultiplied)
    layer.setDevicePixelRatio(dpr)
    layer.fill(Qt.transparent)
    q = QPainter(layer)
    q.setRenderHint(QPainter.Antialiasing, True)
    q.translate(rect.left() - box.left(), rect.top() - box.top())
    q.scale(rect.width(), rect.width())
    if flip:
        q.translate(0.5, 0)
        q.scale(-1, 1)
        q.translate(-0.5, 0)
    _draw_frame(q, fr, tones, icon, flip)
    q.end()
    p.save()
    p.setOpacity(p.opacity() * opacity)
    p.drawImage(box.topLeft(), layer)
    p.restore()


def bounds(rect: QRectF, look: str, age: float, since_clap: float, clap_ms: float, clapped: bool, front: str) -> QRectF:
    """The area the gloves cover for a moment (for tests and layout checks)."""
    seq, i, _ = frame_for(age, since_clap, clap_ms, clapped)
    x0, y0, x1, y1 = frame(seq, i).d["bounds"]
    w = rect.width()
    if front == "left":
        x0, x1 = 1.0 - x1, 1.0 - x0
    return QRectF(rect.left() + x0 * w, rect.top() + y0 * w, (x1 - x0) * w, (y1 - y0) * w)
