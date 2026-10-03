"""
Painting a stack of captures (the model's frames) with the shared drawing code.  Used by the real
overlay surface (surface.py) and by the Settings preview (gui/indicator_preview.py), so what the
preview shows is what the screen shows.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen

from . import draw, layout


class StackPainter:
    def __init__(self, anchor: str, size: float, pad_x: float, pad_y: float, surface: "tuple[float, float]"):
        self.anchor, self.size = anchor, float(size)
        self.pad_x, self.pad_y = float(pad_x), float(pad_y)
        self.surface = surface
        self._colors_cache: dict = {}
        self.bypass = False          # the processing throttle is bypassed (shows "THROTTLING = OFF")

    def colors(self, style) -> "dict[str, QColor]":
        k = tuple(sorted(style.colors.items()))
        c = self._colors_cache.get(k)
        if c is None:
            c = self._colors_cache[k] = draw.resolve_colors(style.colors)
        return c

    def paint_stack(self, p: QPainter, model, key, now: float) -> None:
        for fr in model.frames(key, now):
            ind = model._inds.get(fr.id)
            if ind is not None:
                self.paint_frame(p, ind.style, fr, now)
        over = model.overflow(key)
        if over > 0:
            st = model.style_of(key)
            if st is not None:
                draw.draw_badge(p, layout.badge_rect(self.anchor, self.size, self.pad_x, self.pad_y, self.surface),
                                over, self.colors(st))

    def element_rect(self, style, fr) -> "QRectF | None":
        """Where the processing element of a capture is (the loading circle, or the item itself in
        "stay" mode) when it can be clicked to toggle the throttle bypass, else None."""
        from .model import PROCESSING_STATES
        upload = getattr(style, "kind", "capture") == "upload"
        if upload:
            if fr.state not in ("circle_in", "circle") or fr.circle_alpha < 0.3:
                return None                     # an upload circle is clickable while it runs: shows its Studio window
        elif not getattr(style, "throttle", False) or fr.state not in PROCESSING_STATES:
            return None
        rect = layout.slot_rect(self.anchor, self.size, self.pad_x, self.pad_y, fr.slot, self.surface)
        if fr.state == "stay":
            if style.style == "hands":
                return QRectF(rect)
            from PySide6.QtGui import QTransform
            c = rect.center()                     # the clapper sits tilted: its corners reach past the rect
            t = QTransform().translate(c.x(), c.y()).rotate(draw.clapper_tilt(-1)).translate(-c.x(), -c.y())
            return t.mapRect(QRectF(rect))
        if fr.circle_alpha < 0.3:
            return None
        dia = draw.circle_diameter(self.size)
        c = rect.center()
        return QRectF(c.x() + fr.circle_dx - dia / 2, c.y() - dia / 2, dia, dia)

    def click_rects(self, model, key, now: float) -> "list[QRectF]":
        return [r for r, _id in self.click_targets(model, key, now)]

    def click_targets(self, model, key, now: float) -> "list[tuple[QRectF, str]]":
        """(rect, capture id) of every clickable element."""
        out = []
        for fr in model.frames(key, now):
            ind = model._inds.get(fr.id)
            if ind is not None:
                r = self.element_rect(ind.style, fr)
                if r is not None:
                    out.append((r, fr.id))
        return out

    @staticmethod
    def paint_play_glyph(p: QPainter, centre: QPointF, dia: float, opacity: float, alpha: float) -> None:
        """A small white play triangle inside an upload circle: it reads as "YouTube", not "clip"."""
        a = max(0.0, min(1.0, alpha)) * max(0.35, min(1.0, opacity + 0.25))
        if a <= 0.01:
            return
        s = dia * 0.22
        path = QPainterPath()
        path.moveTo(centre.x() - s * 0.42, centre.y() - s * 0.5)
        path.lineTo(centre.x() + s * 0.58, centre.y())
        path.lineTo(centre.x() - s * 0.42, centre.y() + s * 0.5)
        path.closeSubpath()
        p.save()
        p.setOpacity(p.opacity() * a)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255))
        p.drawPath(path)
        p.restore()

    def paint_throttle_label(self, p: QPainter, r: QRectF) -> None:
        """"THROTTLING = OFF" just above a processing element."""
        f = QFont()
        f.setBold(True)
        f.setPixelSize(max(10, int(round(self.size * 0.085))))
        text = "THROTTLING = OFF"
        path = QPainterPath()
        from PySide6.QtGui import QFontMetricsF
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(text)
        x = r.center().x() - w / 2
        y = r.top() - max(6.0, self.size * 0.06) - fm.descent()
        x = min(max(2.0, x), self.surface[0] - w - 2.0)
        y = max(fm.ascent() + 2.0, y)
        path.addText(QPointF(x, y), f, text)
        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(QColor(0, 0, 0, 220), max(2.0, f.pixelSize() * 0.22), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ffd34d"))
        p.drawPath(path)
        p.restore()

    def paint_frame(self, p: QPainter, style, fr, now: float) -> None:
        W, H = self.surface
        rect = layout.slot_rect(self.anchor, self.size, self.pad_x, self.pad_y, fr.slot, self.surface)
        colors = self.colors(style)
        if fr.anim is not None:
            phase, kind, t = fr.anim
            space = draw.Space.for_rect(self.anchor, rect, W, H)
            xf = draw.animate(phase, kind, t, self.anchor, space)
            if fr.idle_t >= 0:
                xf.dy += draw.idle_bob(fr.idle_t, rect.height())
            # ready to clap (stick up / hands apart) until the clap; shut afterwards
            if fr.clap_ms >= 0:
                pose = draw.clap_pose(fr.clap_ms)
                pose.clap_ms = fr.clap_ms
            else:
                pose = draw.ClapPose(open=0.0 if fr.clapped else 1.0)
            pose.age, pose.clapped = fr.age, fr.clapped
            pose.front = getattr(style, "hands_front", "right")
            pose.look = getattr(style, "hands_look", "retro")
            pose.since_clap = getattr(fr, "since_clap", -1.0)
            icon = draw.load_icon(style.icon)
            p.save()
            draw.apply_xform(p, rect, xf)
            draw.draw_item(p, rect, style.style, pose, colors, icon, opacity=style.opacity)
            p.restore()
        if fr.circle_alpha > 0.003:
            gray = QColor(style.circle_color)
            if not gray.isValid():
                gray = QColor(draw.DEFAULT_CIRCLE_COLOR)
            purple = QColor(style.overlay_circle_color)
            if not purple.isValid():
                purple = QColor(draw.DEFAULT_OVERLAY_CIRCLE_COLOR)
            col = draw.mix(gray, purple, fr.circle_mix)
            is_upload = getattr(style, "kind", "capture") == "upload"
            if is_upload:
                col = QColor(getattr(style, "upload_color", "") or "#ff0033")
                if not col.isValid():
                    col = QColor("#ff0033")
            if fr.circle_red > 0:
                col = draw.mix(col, QColor(draw.FAIL_RED), fr.circle_red)
            c = rect.center()
            centre = QPointF(c.x() + fr.circle_dx, c.y())
            dia = draw.circle_diameter(self.size)
            draw.draw_circle(p, centre, dia * draw.pulse_scale(fr.circle_pulse),
                             fr.circle_angle, col, style.circle_opacity, fr.circle_alpha)
            draw.draw_halo(p, centre, dia, col, fr.circle_pulse, style.circle_opacity, fr.circle_alpha)
            if is_upload:
                self.paint_play_glyph(p, centre, dia, style.circle_opacity, fr.circle_alpha)
        if self.bypass and getattr(style, "kind", "capture") != "upload":
            r = self.element_rect(style, fr)
            if r is not None:
                self.paint_throttle_label(p, r)
