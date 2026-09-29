"""
Small vector icons for the Advanced Editor, drawn with QPainter so they
stay crisp at any size and pick up the theme's text color. Two uses:
- draw_* functions paint straight into the timeline at a given spot;
- icon(name, color) renders one to a cached QPixmap for CustomButton.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPolygonF


def _pen(color, w):
    pen = QPen(QColor(color), w)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def draw_lock(p: QPainter, c: QPointF, size: float, color) -> None:
    w = size * 0.62
    body = QRectF(c.x() - w / 2, c.y() - size * 0.05, w, size * 0.5)
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(body, size * 0.08, size * 0.08)
    p.setPen(_pen(color, max(1.5, size * 0.1)))
    p.setBrush(Qt.NoBrush)
    arc = QRectF(c.x() - w * 0.33, c.y() - size * 0.42, w * 0.66, size * 0.62)
    p.drawArc(arc, 0, 180 * 16)
    p.drawLine(QPointF(arc.left(), c.y() - size * 0.11), QPointF(arc.left(), c.y() - size * 0.05))
    p.drawLine(QPointF(arc.right(), c.y() - size * 0.11), QPointF(arc.right(), c.y() - size * 0.05))
    p.restore()


def draw_speaker(p: QPainter, c: QPointF, size: float, color, muted: bool = True) -> None:
    s = size
    poly = QPolygonF([
        QPointF(c.x() - s * 0.45, c.y() - s * 0.15), QPointF(c.x() - s * 0.25, c.y() - s * 0.15),
        QPointF(c.x() + s * 0.02, c.y() - s * 0.4), QPointF(c.x() + s * 0.02, c.y() + s * 0.4),
        QPointF(c.x() - s * 0.25, c.y() + s * 0.15), QPointF(c.x() - s * 0.45, c.y() + s * 0.15),
    ])
    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPolygon(poly)
    p.setPen(_pen(color, max(1.2, s * 0.09)))
    if muted:
        p.drawLine(QPointF(c.x() + s * 0.15, c.y() - s * 0.18), QPointF(c.x() + s * 0.45, c.y() + s * 0.18))
        p.drawLine(QPointF(c.x() + s * 0.45, c.y() - s * 0.18), QPointF(c.x() + s * 0.15, c.y() + s * 0.18))
    else:
        p.drawArc(QRectF(c.x() - s * 0.1, c.y() - s * 0.25, s * 0.4, s * 0.5), -60 * 16, 120 * 16)
    p.restore()


def draw_eye(p: QPainter, c: QPointF, size: float, color, slashed: bool = True) -> None:
    s = size
    path = QPainterPath()
    path.moveTo(c.x() - s * 0.48, c.y())
    path.quadTo(c.x(), c.y() - s * 0.5, c.x() + s * 0.48, c.y())
    path.quadTo(c.x(), c.y() + s * 0.5, c.x() - s * 0.48, c.y())
    p.save()
    p.setPen(_pen(color, max(1.2, s * 0.09)))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setBrush(QColor(color))
    p.drawEllipse(c, s * 0.12, s * 0.12)
    if slashed:
        p.drawLine(QPointF(c.x() - s * 0.4, c.y() + s * 0.4), QPointF(c.x() + s * 0.4, c.y() - s * 0.4))
    p.restore()


def draw_trash(p: QPainter, c: QPointF, size: float, color) -> None:
    s = size
    p.save()
    p.setPen(_pen(color, max(1.3, s * 0.09)))
    p.setBrush(Qt.NoBrush)
    body = QPainterPath()
    body.moveTo(c.x() - s * 0.3, c.y() - s * 0.22)
    body.lineTo(c.x() - s * 0.22, c.y() + s * 0.42)
    body.lineTo(c.x() + s * 0.22, c.y() + s * 0.42)
    body.lineTo(c.x() + s * 0.3, c.y() - s * 0.22)
    p.drawPath(body)
    p.drawLine(QPointF(c.x() - s * 0.42, c.y() - s * 0.3), QPointF(c.x() + s * 0.42, c.y() - s * 0.3))
    p.drawLine(QPointF(c.x() - s * 0.12, c.y() - s * 0.42), QPointF(c.x() + s * 0.12, c.y() - s * 0.42))
    for dx in (-0.1, 0.1):
        p.drawLine(QPointF(c.x() + s * dx, c.y() - s * 0.1), QPointF(c.x() + s * dx, c.y() + s * 0.3))
    p.restore()


def draw_handle_lines(p: QPainter, c: QPointF, size: float, color) -> None:
    p.save()
    p.setPen(_pen(color, max(1.5, size * 0.12)))
    for dy in (-0.28, 0.0, 0.28):
        y = c.y() + dy * size
        p.drawLine(QPointF(c.x() - size * 0.38, y), QPointF(c.x() + size * 0.38, y))
    p.restore()


def draw_chevron(p: QPainter, c: QPointF, size: float, color, expanded: bool) -> None:
    p.save()
    p.setPen(_pen(color, max(1.5, size * 0.13)))
    if expanded:
        pts = [QPointF(c.x() - size * 0.3, c.y() - size * 0.12), QPointF(c.x(), c.y() + size * 0.18),
               QPointF(c.x() + size * 0.3, c.y() - size * 0.12)]
    else:
        pts = [QPointF(c.x() - size * 0.12, c.y() - size * 0.3), QPointF(c.x() + size * 0.18, c.y()),
               QPointF(c.x() - size * 0.12, c.y() + size * 0.3)]
    p.drawPolyline(QPolygonF(pts))
    p.restore()


def draw_transition(p: QPainter, c: QPointF, size: float, color) -> None:
    p.save()
    p.setPen(_pen(color, max(1.2, size * 0.1)))
    p.setBrush(Qt.NoBrush)
    r = size * 0.28
    p.drawEllipse(QPointF(c.x() - r * 0.6, c.y()), r, r)
    p.drawEllipse(QPointF(c.x() + r * 0.6, c.y()), r, r)
    p.restore()


# ---- toolbar icons ---------------------------------------------------------

def _paint_icon(name: str, p: QPainter, s: float, color: QColor) -> None:
    c = QPointF(s / 2, s / 2)
    pen = _pen(color, s * 0.08)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    if name in ("undo", "redo"):
        sign = -1 if name == "undo" else 1
        path = QPainterPath()
        path.moveTo(c.x() - sign * s * 0.28, c.y() + s * 0.25)
        path.cubicTo(c.x() - sign * s * 0.32, c.y() - s * 0.12, c.x() + sign * s * 0.1, c.y() - s * 0.2,
                     c.x() + sign * s * 0.3, c.y() - s * 0.05)
        p.drawPath(path)
        tip = QPointF(c.x() + sign * s * 0.3, c.y() - s * 0.05)
        p.drawLine(tip, QPointF(tip.x() - sign * s * 0.03, tip.y() - s * 0.18))
        p.drawLine(tip, QPointF(tip.x() - sign * s * 0.18, tip.y() + s * 0.05))
    elif name == "split":
        for dy in (-1, 1):
            p.drawEllipse(QPointF(c.x() - s * 0.22, c.y() + dy * s * 0.18), s * 0.1, s * 0.1)
            p.drawLine(QPointF(c.x() - s * 0.13, c.y() + dy * s * 0.14), QPointF(c.x() + s * 0.35, c.y() - dy * s * 0.2))
    elif name == "combine":
        p.drawRoundedRect(QRectF(s * 0.12, s * 0.32, s * 0.34, s * 0.36), s * 0.06, s * 0.06)
        p.drawRoundedRect(QRectF(s * 0.54, s * 0.32, s * 0.34, s * 0.36), s * 0.06, s * 0.06)
        p.drawLine(QPointF(s * 0.42, s * 0.5), QPointF(s * 0.58, s * 0.5))
    elif name == "magnet":
        path = QPainterPath()
        path.moveTo(s * 0.26, s * 0.2)
        path.lineTo(s * 0.26, s * 0.5)
        path.arcTo(QRectF(s * 0.26, s * 0.26, s * 0.48, s * 0.48), 180, 180)
        path.lineTo(s * 0.74, s * 0.2)
        p.drawPath(path)
        p.drawLine(QPointF(s * 0.2, s * 0.32), QPointF(s * 0.32, s * 0.32))
        p.drawLine(QPointF(s * 0.68, s * 0.32), QPointF(s * 0.8, s * 0.32))
    elif name in ("zoom_in", "zoom_out", "zoom_fit"):
        p.drawEllipse(QPointF(s * 0.42, s * 0.42), s * 0.24, s * 0.24)
        p.drawLine(QPointF(s * 0.6, s * 0.6), QPointF(s * 0.82, s * 0.82))
        if name != "zoom_fit":
            p.drawLine(QPointF(s * 0.31, s * 0.42), QPointF(s * 0.53, s * 0.42))
        if name == "zoom_in":
            p.drawLine(QPointF(s * 0.42, s * 0.31), QPointF(s * 0.42, s * 0.53))
        if name == "zoom_fit":
            p.drawRect(QRectF(s * 0.33, s * 0.35, s * 0.18, s * 0.14))
    elif name == "play":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(s * 0.32, s * 0.22), QPointF(s * 0.78, s * 0.5), QPointF(s * 0.32, s * 0.78)]))
    elif name == "pause":
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(s * 0.28, s * 0.22, s * 0.15, s * 0.56), s * 0.03, s * 0.03)
        p.drawRoundedRect(QRectF(s * 0.57, s * 0.22, s * 0.15, s * 0.56), s * 0.03, s * 0.03)
    elif name in ("step_back", "step_fwd"):
        sign = -1 if name == "step_back" else 1
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        x0 = c.x() - sign * s * 0.18
        p.drawPolygon(QPolygonF([QPointF(x0, s * 0.28), QPointF(x0 + sign * s * 0.36, s * 0.5), QPointF(x0, s * 0.72)]))
        p.drawRect(QRectF(c.x() + sign * s * 0.2 - s * 0.04, s * 0.28, s * 0.08, s * 0.44))
    elif name in ("to_start", "to_end"):
        sign = -1 if name == "to_start" else 1
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        for off in (-0.14, 0.14):
            x0 = c.x() + off * s - sign * s * 0.14
            p.drawPolygon(QPolygonF([QPointF(x0, s * 0.3), QPointF(x0 + sign * s * 0.26, s * 0.5), QPointF(x0, s * 0.7)]))
    elif name == "import":
        p.drawLine(QPointF(c.x(), s * 0.16), QPointF(c.x(), s * 0.6))
        p.drawLine(QPointF(c.x() - s * 0.16, s * 0.45), QPointF(c.x(), s * 0.61))
        p.drawLine(QPointF(c.x() + s * 0.16, s * 0.45), QPointF(c.x(), s * 0.61))
        p.drawPolyline(QPolygonF([QPointF(s * 0.2, s * 0.62), QPointF(s * 0.2, s * 0.82),
                                  QPointF(s * 0.8, s * 0.82), QPointF(s * 0.8, s * 0.62)]))
    elif name == "save":
        p.drawRoundedRect(QRectF(s * 0.2, s * 0.2, s * 0.6, s * 0.6), s * 0.06, s * 0.06)
        p.drawRect(QRectF(s * 0.32, s * 0.2, s * 0.36, s * 0.18))
        p.drawRect(QRectF(s * 0.32, s * 0.54, s * 0.36, s * 0.26))
    elif name == "crop":
        p.drawPolyline(QPolygonF([QPointF(s * 0.3, s * 0.14), QPointF(s * 0.3, s * 0.7), QPointF(s * 0.86, s * 0.7)]))
        p.drawPolyline(QPolygonF([QPointF(s * 0.14, s * 0.3), QPointF(s * 0.7, s * 0.3), QPointF(s * 0.7, s * 0.86)]))
    elif name == "keyframe":
        p.setBrush(color)
        p.drawPolygon(QPolygonF([QPointF(c.x(), s * 0.2), QPointF(s * 0.8, c.y()), QPointF(c.x(), s * 0.8), QPointF(s * 0.2, c.y())]))
    elif name == "lock":
        draw_lock(p, c, s * 0.8, color)
    elif name == "mute":
        draw_speaker(p, c, s * 0.8, color, muted=True)
    elif name == "eye":
        draw_eye(p, c, s * 0.8, color, slashed=True)
    elif name == "trash":
        draw_trash(p, c, s * 0.8, color)


_cache: dict = {}


def icon(name: str, color, size: int = 64) -> QPixmap:
    key = (name, QColor(color).name(QColor.HexArgb), size)
    pm = _cache.get(key)
    if pm is None:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        _paint_icon(name, p, float(size), QColor(color))
        p.end()
        _cache[key] = pm
    return pm
