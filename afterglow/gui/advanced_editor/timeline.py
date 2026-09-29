"""
The Advanced Editor's timeline: ruler, playhead, tracks and segments,
all custom-painted in one widget (TimelineView) with its scroll bars in
TimelinePanel.

Interaction summary (see HANDOFF.md for the spec each comes from):
- Ruler: click/drag moves the playhead (snaps lightly to segment edges).
- Empty lane: click moves the playhead and clears the selection; drag
  draws a selection rectangle (Ctrl adds to the selection).
- Segment: click selects (Ctrl toggles, Shift selects a range); drag
  moves the selection across time and tracks with snapping; dropping on
  an occupied spot kicks it to the nearest free track; dropping on the
  top/bottom buffer track makes a new track. Drag an edge to trim or
  reveal; drag the horizontal volume line (0-200%, 100% = middle) and
  type digits while holding it for an exact percent; drag the small
  round handles at the top corners to set fades. Click an internal split
  marker to jump the playhead there.
- Gap between segments: a faint line; hovering its middle shows a trash
  can that closes the gap.
- Track header: drag the 3-line handle to reorder, click the arrow to
  collapse. Header side follows Settings > General > Editor.
- Wheel: step the playhead one frame (Shift = 1 s); Ctrl = zoom around
  the cursor; over the headers (or with Alt) = scroll tracks; a
  horizontal wheel/trackpad scrolls time.
- Alt while dragging bypasses snapping; N toggles snapping.

Rendering: everything except the playhead and hover-only details is
drawn into a cached pixmap that is rebuilt only when something it shows
changes, so playback (which moves the playhead every frame) costs one
pixmap blit plus a line.
"""
from __future__ import annotations

import copy
import json
import math
import os

import numpy as np
from PySide6.QtCore import QMimeData, QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)
from PySide6.QtWidgets import QGridLayout, QMenu, QWidget, QToolTip

from ... import config as config_module
from ...nle import media, ops
from ...nle.model import EPS, KIND_GIF, KIND_IMAGE, KIND_TEXT, Project, Segment
from ..custom_scrollbar import CustomScrollBar
from ..rounded_rect import rounded_rect_path
from ..theme import Theme, contrast_text
from . import icons
from .controller import EditorController, probe_cached, seg_kind

MIME_ITEM = "application/x-afterglow-editor-item"

HEADER_W = 104
RULER_H = 30
COLLAPSED_H = 26
VISIBLE_TRACKS = 4
MIN_LANE_H = 44
EDGE_GRAB = 6
SNAP_PX = 10
DRAG_START_PX = 4
PLAYHEAD_COLOR = QColor("#ff4757")
SNAP_COLOR = QColor("#ffd43b")
MIN_PPS, MAX_PPS = 1.0, 3000.0

_TICK_STEPS = [1 / 60, 1 / 30, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600]


def format_time(t: float, fps: float = 0.0, frames: bool = False) -> str:
    t = max(0.0, t)
    m, s = divmod(t, 60)
    h, m = divmod(int(m), 60)
    if frames and fps > 0:
        f = int(round((s - int(s)) * fps))
        if f >= round(fps):
            f = 0
        base = f"{int(m)}:{int(s):02d}.{f:02d}"
    else:
        base = f"{int(m)}:{int(s):02d}"
    return f"{h}:{base.zfill(len(base) + (1 if m < 10 else 0))}" if h else base


class TimelineView(QWidget):
    view_changed = Signal()          # scroll/zoom/content extent changed -> scroll bars
    open_properties = Signal()

    def __init__(self, controller: EditorController, visuals, parent=None):
        super().__init__(parent)
        self.ctl = controller
        self.visuals = visuals
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self.header_side = getattr(appearance, "editor_track_handle_side", "left")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.setMinimumHeight(RULER_H + 2 * MIN_LANE_H)
        self.pps = 60.0                  # pixels per second
        self.scroll_t = 0.0              # seconds at the left edge of the lanes
        self.scroll_y = 0.0              # px scrolled down in the lanes
        self._cache: "QPixmap | None" = None
        self._cache_key = None
        self._rev = 0
        self._hover = None               # hit-test result under the cursor
        self._drag = None                # active gesture dict
        self._snap_line: "float | None" = None
        self._drop_ghost = None          # (track_index, t0, t1) while dragging files in
        self._typed = ""                 # digits typed during a volume drag
        self._fit_pending = True
        controller.changed.connect(self._invalidate)
        controller.selection_changed.connect(self._invalidate)
        controller.playhead_changed.connect(lambda _t: self.update())
        visuals.ready.connect(self._invalidate)

    # ================================================================ geometry
    def _invalidate(self) -> None:
        self._rev += 1
        self.update()
        self.view_changed.emit()

    @property
    def project(self) -> "Project | None":
        return self.ctl.project

    def lanes_left(self) -> float:
        return HEADER_W if self.header_side == "left" else 0.0

    def lanes_width(self) -> float:
        return max(10.0, self.width() - HEADER_W)

    def header_rect(self) -> QRectF:
        x = 0.0 if self.header_side == "left" else self.width() - HEADER_W
        return QRectF(x, RULER_H, HEADER_W, self.height() - RULER_H)

    def lane_h(self) -> float:
        return max(MIN_LANE_H, (self.height() - RULER_H) / VISIBLE_TRACKS)

    def track_height(self, i: int) -> float:
        p = self.project
        return COLLAPSED_H if p is not None and p.tracks[i].collapsed else self.lane_h()

    def track_top(self, i: int) -> float:
        y = RULER_H - self.scroll_y
        for j in range(i):
            y += self.track_height(j)
        return y

    def content_height(self) -> float:
        p = self.project
        return sum(self.track_height(i) for i in range(len(p.tracks))) if p else 0.0

    def track_at_y(self, y: float, clamp: bool = True) -> "int | None":
        p = self.project
        if p is None or not p.tracks:
            return None
        top = RULER_H - self.scroll_y
        for i in range(len(p.tracks)):
            h = self.track_height(i)
            if top <= y < top + h:
                return i
            top += h
        if not clamp:
            return None
        return 0 if y < RULER_H - self.scroll_y else len(p.tracks) - 1

    def t_to_x(self, t: float) -> float:
        return self.lanes_left() + (t - self.scroll_t) * self.pps

    def x_to_t(self, x: float) -> float:
        return self.scroll_t + (x - self.lanes_left()) / self.pps

    def visible_seconds(self) -> float:
        return self.lanes_width() / self.pps

    def content_seconds(self) -> float:
        d = self.project.duration if self.project else 0.0
        return max(d + max(10.0, d * 0.25), self.visible_seconds())

    def seg_rect(self, track_i: int, seg: Segment) -> QRectF:
        top = self.track_top(track_i)
        h = self.track_height(track_i)
        x0, x1 = self.t_to_x(seg.start), self.t_to_x(seg.end)
        return QRectF(x0, top + 3, max(1.0, x1 - x0), h - 6)

    # ================================================================ scroll / zoom API
    def set_scroll_t(self, t: float) -> None:
        t = max(0.0, min(t, max(0.0, self.content_seconds() - self.visible_seconds())))
        if abs(t - self.scroll_t) > 1e-9:
            self.scroll_t = t
            self._rev += 1
            self.update()
            self.view_changed.emit()

    def set_scroll_y(self, y: float) -> None:
        max_y = max(0.0, self.content_height() - (self.height() - RULER_H))
        y = max(0.0, min(y, max_y))
        if abs(y - self.scroll_y) > 1e-9:
            self.scroll_y = y
            self._rev += 1
            self.update()
            self.view_changed.emit()

    def set_zoom(self, pps: float, anchor_x: "float | None" = None) -> None:
        pps = max(MIN_PPS, min(MAX_PPS, pps))
        if anchor_x is None:
            anchor_x = self.t_to_x(self.ctl.playhead) if self._x_visible(self.t_to_x(self.ctl.playhead)) \
                else self.lanes_left() + self.lanes_width() / 2
        anchor_t = self.x_to_t(anchor_x)
        self.pps = pps
        self.visuals.forget()
        self.scroll_t = 0.0
        self.set_scroll_t(anchor_t - (anchor_x - self.lanes_left()) / pps)
        self._rev += 1
        self.update()
        self.view_changed.emit()

    def zoom_by(self, steps: float, anchor_x: "float | None" = None) -> None:
        self.set_zoom(self.pps * (1.25 ** steps), anchor_x)

    def zoom_to_fit(self) -> None:
        d = self.project.duration if self.project else 0.0
        self.pps = max(MIN_PPS, min(MAX_PPS, self.lanes_width() * 0.95 / max(d, 5.0)))
        self.scroll_t = 0.0
        self._rev += 1
        self.update()
        self.view_changed.emit()

    def request_fit(self) -> None:
        self._fit_pending = True
        self.update()

    def _x_visible(self, x: float) -> bool:
        return self.lanes_left() <= x <= self.lanes_left() + self.lanes_width()

    def ensure_playhead_visible(self) -> None:
        x = self.t_to_x(self.ctl.playhead)
        if x > self.lanes_left() + self.lanes_width() - 20:
            self.set_scroll_t(self.ctl.playhead - self.visible_seconds() * 0.1)
        elif x < self.lanes_left():
            self.set_scroll_t(self.ctl.playhead - self.visible_seconds() * 0.1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rev += 1
        self.set_scroll_y(self.scroll_y)
        self.view_changed.emit()

    # ================================================================ hit testing
    def hit(self, pos: QPointF):
        """What's under pos. Returns a dict with 'kind' and details, or None."""
        p = self.project
        if p is None:
            return None
        x, y = pos.x(), pos.y()
        hr = self.header_rect()
        if y < RULER_H:
            if hr.left() <= x < hr.right():
                return None
            return {"kind": "ruler", "t": self.x_to_t(x)}
        ti = self.track_at_y(y, clamp=False)
        if ti is None:
            return None
        track = p.tracks[ti]
        top = self.track_top(ti)
        h = self.track_height(ti)
        if hr.left() <= x < hr.right():
            handle_x = hr.left() + 14 if self.header_side == "left" else hr.right() - 14
            chevron_x = hr.left() + 34 if self.header_side == "left" else hr.right() - 34
            zone = "body"
            if abs(x - handle_x) <= 11:
                zone = "handle"
            elif abs(x - chevron_x) <= 10:
                zone = "collapse"
            return {"kind": "header", "track": ti, "zone": zone}
        t = self.x_to_t(x)
        # Segments, topmost-drawn last -> check in reverse draw order.
        for seg in sorted(track.segments, key=lambda s: s.start, reverse=True):
            r = self.seg_rect(ti, seg)
            if not r.adjusted(-EDGE_GRAB / 2, 0, EDGE_GRAB / 2, 0).contains(pos):
                continue
            zone = self._segment_zone(seg, r, pos, track.collapsed)
            return {"kind": "segment", "track": ti, "seg": seg, "zone": zone[0], "extra": zone[1], "t": t}
        # Gap trash can
        gap = self._gap_hover(ti, x)
        if gap is not None:
            return {"kind": "gap", "track": ti, "t": gap}
        return {"kind": "lane", "track": ti, "t": t}

    def _segment_zone(self, seg: Segment, r: QRectF, pos: QPointF, collapsed: bool):
        x, y = pos.x(), pos.y()
        wide = r.width() > 3 * EDGE_GRAB
        if not collapsed and wide and not seg.locked:
            # fade handles (top corners, inset by the fade length)
            for which, fx in (("fade_in", self.t_to_x(seg.start + seg.fade_in)),
                              ("fade_out", self.t_to_x(seg.end - seg.fade_out))):
                hx = min(max(fx, r.left() + 7), r.right() - 7)
                if (x - hx) ** 2 + (y - (r.top() + 7)) ** 2 <= 7 ** 2:
                    return which, None
        if wide and not seg.locked:
            if abs(x - r.left()) <= EDGE_GRAB:
                return "left", None
            if abs(x - r.right()) <= EDGE_GRAB:
                return "right", None
        if not collapsed:
            for mt in seg.split_markers():
                if abs(self.t_to_x(mt) - x) <= 3:
                    return "marker", mt
            if seg.has_audio and not seg.locked:
                vy = self._volume_y(seg, r)
                if abs(y - vy) <= 4:
                    return "volume", None
        return "body", None

    def _volume_y(self, seg: Segment, r: QRectF) -> float:
        v = max(0.0, min(2.0, seg.volume))
        return r.bottom() - (v / 2.0) * r.height()

    def _gaps(self, ti: int) -> list:
        track = self.project.tracks[ti]
        out = []
        prev_end = 0.0
        for s in track.sorted_segments():
            if s.start - prev_end > EPS:
                out.append((prev_end, s.start))
            prev_end = max(prev_end, s.end)
        return out

    def _gap_hover(self, ti: int, x: float) -> "float | None":
        for g0, g1 in self._gaps(ti):
            gx0, gx1 = self.t_to_x(g0), self.t_to_x(g1)
            if gx1 - gx0 < 14:
                continue
            mid = (gx0 + gx1) / 2
            if abs(x - mid) <= 14:
                return (g0 + g1) / 2
        return None

    # ================================================================ painting
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        if self._fit_pending and self.project is not None and self.width() > HEADER_W + 50:
            self._fit_pending = False
            self.zoom_to_fit()
        key = (self._rev, self.width(), self.height(), self.devicePixelRatioF(),
               self._hover_key(), self._drop_ghost, self._snap_line)
        if self._cache is None or key != self._cache_key:
            self._cache = self._render_static()
            self._cache_key = key
        p.drawPixmap(0, 0, self._cache)
        if self.project is not None:
            self._paint_playhead(p)
        p.end()

    def _hover_key(self):
        h = self._hover
        if h is None:
            return None
        return (h["kind"], h.get("track"), getattr(h.get("seg"), "id", None), h.get("zone"),
                round(h.get("t", 0), 3) if h["kind"] == "gap" else None)

    def _render_static(self) -> QPixmap:
        dpr = self.devicePixelRatioF()
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        theme = self._theme
        bg = theme.library_background()
        pm.fill(bg)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        proj = self.project
        if proj is None:
            p.setPen(contrast_text(bg))
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Open a clip from the Library (Edit), or use Import to edit any file.")
            p.end()
            return pm
        lanes_clip = QRectF(self.lanes_left(), RULER_H, self.lanes_width(), self.height() - RULER_H)
        for i, track in enumerate(proj.tracks):
            top, h = self.track_top(i), self.track_height(i)
            if top > self.height() or top + h < RULER_H:
                continue
            self._paint_lane(p, i, track, top, h, lanes_clip)
        self._paint_headers(p)
        self._paint_ruler(p)
        if self._snap_line is not None:
            x = self.t_to_x(self._snap_line)
            p.setPen(QPen(SNAP_COLOR, 1.5, Qt.DashLine))
            p.drawLine(QPointF(x, RULER_H), QPointF(x, self.height()))
        if self._drop_ghost is not None:
            ti, g0, g1 = self._drop_ghost
            if 0 <= ti < len(proj.tracks):
                top, h = self.track_top(ti), self.track_height(ti)
                r = QRectF(self.t_to_x(g0), top + 3, max(4.0, (g1 - g0) * self.pps), h - 6)
                p.setPen(QPen(theme.accent().lighter(150), 2, Qt.DashLine))
                p.setBrush(QColor(255, 255, 255, 30))
                p.drawRoundedRect(r, 6, 6)
        p.end()
        return pm

    def _paint_lane(self, p: QPainter, i: int, track, top: float, h: float, clip: QRectF) -> None:
        theme = self._theme
        proj = self.project
        base = theme.library_background()
        lane_color = base.lighter(118) if i % 2 == 0 else base.lighter(110)
        is_buffer = (i == 0 or i == len(proj.tracks) - 1) and track.is_empty()
        p.save()
        p.setClipRect(clip)
        lane = QRectF(self.lanes_left(), top, self.lanes_width(), h)
        p.fillRect(lane, base.lighter(104) if is_buffer else lane_color)
        p.setPen(QPen(base.darker(130), 1))
        p.drawLine(QPointF(lane.left(), lane.bottom() - 0.5), QPointF(lane.right(), lane.bottom() - 0.5))
        if is_buffer and not track.collapsed:
            p.setPen(QColor(255, 255, 255, 50))
            f = QFont(self.font())
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.9))
            p.setFont(f)
            p.drawText(lane.adjusted(10, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft,
                       "Drop here to add a new track")
        # gaps
        text_c = QColor(self._appearance.card_text_color)
        for g0, g1 in self._gaps(i):
            gx0, gx1 = self.t_to_x(g0), self.t_to_x(g1)
            if gx1 < clip.left() or gx0 > clip.right() or gx1 - gx0 < 6:
                continue
            mid = (gx0 + gx1) / 2
            hovered = (self._hover is not None and self._hover["kind"] == "gap" and self._hover["track"] == i
                       and abs(self._hover["t"] - (g0 + g1) / 2) < 1e-6)
            # A horizontal line across the gap with a break in the middle;
            # the trash can appears in that break on hover.
            c = QColor(text_c)
            c.setAlpha(150 if hovered else 60)
            p.setPen(QPen(c, 1.5))
            y = top + h / 2
            brk = 17 if gx1 - gx0 >= 40 else 0
            if brk:
                p.drawLine(QPointF(gx0 + 4, y), QPointF(mid - brk, y))
                p.drawLine(QPointF(mid + brk, y), QPointF(gx1 - 4, y))
            else:
                p.drawLine(QPointF(gx0 + 3, y), QPointF(gx1 - 3, y))
            if hovered:
                badge = QRectF(mid - 14, y - 14, 28, 28)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(200, 60, 60, 220))
                p.drawRoundedRect(badge, 8, 8)
                icons.draw_trash(p, badge.center(), 20, QColor("white"))
        # segments
        for seg in track.sorted_segments():
            r = self.seg_rect(i, seg)
            if r.right() < clip.left() - 2 or r.left() > clip.right() + 2:
                continue
            self._paint_segment(p, i, track, seg, r)
        p.restore()

    # ---------------------------------------------------------------- segments
    def _corner_flags(self, track, seg: Segment):
        left = right = True
        for o in track.segments:
            if o.id == seg.id:
                continue
            if abs(o.end - seg.start) < 1e-4:
                left = False
            if abs(o.start - seg.end) < 1e-4:
                right = False
        return left, right

    def _seg_colors(self, seg: Segment):
        theme = self._theme
        kind = seg_kind(seg)
        if kind == "audio":
            return theme.turquoise().darker(160)
        if kind == KIND_TEXT:
            c = QColor(theme.accent())
            c.setAlpha(70)
            return c
        if kind == KIND_IMAGE:
            c = QColor(theme.accent()).lighter(115)
            c.setAlpha(110)
            return c
        return theme.accent().darker(115)

    def _paint_segment(self, p: QPainter, ti: int, track, seg: Segment, r: QRectF) -> None:
        theme = self._theme
        left_round, right_round = self._corner_flags(track, seg)
        radius = min(self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 3,
                     10, r.height() / 3, r.width() / 2)
        path = rounded_rect_path(r, radius, top_left=left_round, bottom_left=left_round,
                                 top_right=right_round, bottom_right=right_round)
        selected = seg.id in self.ctl.selection
        text_c = QColor(self._appearance.card_text_color)
        p.save()
        p.fillPath(path, self._seg_colors(seg))
        p.setClipPath(path)
        kind = seg_kind(seg)
        collapsed = track.collapsed
        if not collapsed:
            if kind == KIND_IMAGE:
                self._paint_element_thumb(p, seg, r)
            elif kind == "av" and seg.has_video:
                strip = QRectF(r)
                if seg.has_audio:
                    strip.setBottom(r.top() + r.height() * 0.62)
                self._paint_filmstrip(p, seg, strip)
            if seg.has_audio:
                wave = QRectF(r)
                if seg.has_video:
                    wave.setTop(r.top() + r.height() * 0.62)
                self._paint_waveform(p, seg, wave)
            if kind == KIND_TEXT:
                self._paint_text_preview(p, seg, r)
            self._paint_fades(p, seg, r)
            if seg.transition_in is not None:
                self._paint_transition_badge(p, seg, r)
            # split markers
            pen = QPen(QColor(255, 255, 255, 200), 1.2, Qt.DashLine)
            p.setPen(pen)
            for mt in seg.split_markers():
                mx = self.t_to_x(mt)
                p.drawLine(QPointF(mx, r.top()), QPointF(mx, r.bottom()))
            if seg.has_audio:
                self._paint_volume_line(p, seg, r)
            if selected and seg.keyframes:
                self._paint_keyframes(p, seg, r)
        # name label
        self._paint_label(p, seg, r, collapsed)
        if not seg.visible:
            p.fillRect(r, QColor(0, 0, 0, 120))
        if seg.locked:
            p.fillRect(r, QColor(128, 128, 128, 120))
        p.restore()
        # state icons (top-right)
        ix = r.right() - 12
        iy = r.top() + (r.height() / 2 if collapsed else 12)
        if r.width() > 40:
            if not seg.visible:
                icons.draw_eye(p, QPointF(ix, iy), 14, text_c, slashed=True)
                ix -= 18
            if seg.muted:
                icons.draw_speaker(p, QPointF(ix, iy), 14, text_c, muted=True)
        if seg.locked:
            icons.draw_lock(p, r.center(), min(26.0, r.height() * 0.6), QColor(230, 230, 230))
        # border
        if seg.locked:
            p.setPen(QPen(QColor(150, 150, 150), 2))
        elif selected:
            p.setPen(QPen(text_c.lighter(130), 2.2))
        else:
            p.setPen(QPen(theme.accent().darker(170), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _paint_label(self, p: QPainter, seg: Segment, r: QRectF, collapsed: bool) -> None:
        name = seg.name or ("Text" if seg_kind(seg) == KIND_TEXT else "Segment")
        if seg.parts and seg.parts[0].speed != 1.0:
            name += f"  {seg.parts[0].speed:g}x"
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.85))
        p.setFont(f)
        fm = QFontMetricsF(f)
        avail = r.width() - 12
        if avail < 16:
            return
        # Start after the transition badge (if any) so the name never hides it.
        lead = 4.0
        if seg_kind(seg) == KIND_IMAGE and not collapsed:
            part = next((pt for pt in seg.parts if pt.has_video), None)
            info = probe_cached(part.source) if part else {}
            lead += (r.height() - 6) * (info.get("width") or 16) / max(info.get("height") or 9, 1) + 4
        if seg.transition_in is not None and not collapsed:
            lead += min(max(14.0, seg.transition_in.duration * self.pps), r.width())
        avail = r.width() - lead - 8
        if avail < 16:
            return
        text = fm.elidedText(name, Qt.ElideRight, avail)
        tw = fm.horizontalAdvance(text)
        box = QRectF(r.left() + lead, r.top() + (r.height() - fm.height()) / 2 - 1 if collapsed else r.top() + 3,
                     tw + 8, fm.height() + 2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 140))
        p.drawRoundedRect(box, 4, 4)
        p.setPen(QColor(self._appearance.card_text_color))
        p.drawText(box, Qt.AlignCenter, text)

    def _paint_filmstrip(self, p: QPainter, seg: Segment, strip: QRectF) -> None:
        part0 = next((pt for pt in seg.parts if pt.has_video and pt.kind != KIND_TEXT), None)
        if part0 is None:
            return
        info = probe_cached(part0.source)
        aspect = (info.get("width") or 16) / max(info.get("height") or 9, 1)
        th = max(8, int(strip.height()))
        tw = max(8.0, th * aspect)
        clip = QRectF(self.lanes_left(), strip.top(), self.lanes_width(), strip.height()).intersected(strip)
        if clip.isEmpty():
            return
        first = max(0, int((clip.left() - strip.left()) // tw))
        last = int((clip.right() - strip.left()) // tw)
        dpr = self.devicePixelRatioF()
        for i in range(first, last + 1):
            x0 = strip.left() + i * tw
            center_t = seg.start + (i + 0.5) * tw / self.pps
            local = min(center_t - seg.start, seg.duration - 1e-4)
            part = next((pt for pt in seg.parts if pt.has_video and pt.kind != KIND_TEXT and pt.active_at(local)), None)
            if part is None:
                continue
            st = part.source_time(local)
            if part.kind == KIND_GIF and part.source_duration > EPS:
                st = st % part.source_duration
            h_px = int(th * dpr)
            img = self.visuals.thumbnail(part.source, st, h_px)
            if img is None:
                img = self.visuals.nearest_thumbnail(part.source, st, h_px)
            target = QRectF(x0, strip.top(), tw, strip.height())
            if img is not None:
                p.drawImage(target, img)
            p.setPen(QPen(QColor(0, 0, 0, 90), 1))
            p.drawLine(QPointF(x0, strip.top()), QPointF(x0, strip.bottom()))

    def _paint_element_thumb(self, p: QPainter, seg: Segment, r: QRectF) -> None:
        """Pictures/GIFs: transparent segment, one small thumbnail at the
        start (the name label sits next to it)."""
        part = next((pt for pt in seg.parts if pt.has_video), None)
        if part is None:
            return
        h = max(8, int((r.height() - 6) * self.devicePixelRatioF()))
        img = self.visuals.thumbnail(part.source, 0.0, h) or self.visuals.nearest_thumbnail(part.source, 0.0, h)
        if img is None:
            return
        th = r.height() - 6
        tw = th * img.width() / max(img.height(), 1)
        x = max(r.left() + 3, self.lanes_left() + 3)
        if x + tw > r.right() - 3:
            return
        p.drawImage(QRectF(x, r.top() + 3, tw, th), img)

    def _paint_waveform(self, p: QPainter, seg: Segment, wave: QRectF) -> None:
        clip_l = max(wave.left(), self.lanes_left())
        clip_r = min(wave.right(), self.lanes_left() + self.lanes_width())
        if clip_r - clip_l < 1:
            return
        n = int(clip_r - clip_l)
        xs = clip_l + np.arange(n) + 0.5
        times = self.scroll_t + (xs - self.lanes_left()) / self.pps
        local = times - seg.start
        amp = np.zeros(n)
        dt = 1.0 / self.pps
        for part in seg.parts:
            if not part.has_audio or part.kind not in ("av", KIND_GIF):
                continue
            pk = self.visuals.peaks(part.source)
            if pk is None or pk.size == 0:
                continue
            mask = (local >= part.offset) & (local < part.end)
            if not mask.any():
                continue
            s0 = part.src_in + (local[mask] - part.offset) * part.speed
            s1 = s0 + dt * part.speed
            b0 = np.clip((s0 * media.PEAK_RATE).astype(np.int64), 0, pk.size - 1)
            b1 = np.clip((s1 * media.PEAK_RATE).astype(np.int64) + 1, 1, pk.size)
            # max over each pixel's [b0, b1) bin range (b0 is non-decreasing)
            if part.speed > 0 and b0.size > 1:
                pk_ext = np.append(pk, np.float32(0))
                bounds = np.append(b0, max(int(b1[-1]), int(b0[-1]) + 1))
                vals = np.maximum.reduceat(pk_ext, bounds)[:-1]
            else:
                vals = pk[b0]
            amp[mask] = np.maximum(amp[mask], vals * part.gain)
        vol = 0.0 if seg.muted else seg.volume
        amp = np.clip(amp * vol, 0, 1.0)
        mid = wave.center().y()
        half = wave.height() / 2 - 1
        top = QPolygonF([QPointF(x, mid - a * half) for x, a in zip(xs, amp)])
        bot = QPolygonF([QPointF(x, mid + a * half) for x, a in zip(xs[::-1], amp[::-1])])
        poly = QPolygonF(top)
        poly += bot
        c = self._theme.turquoise().lighter(140) if seg.has_video else self._theme.turquoise().lighter(115)
        c.setAlpha(200)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawPolygon(poly)

    def _paint_volume_line(self, p: QPainter, seg: Segment, r: QRectF) -> None:
        y = self._volume_y(seg, r)
        c = QColor(self._appearance.card_text_color)
        hovered = (self._hover is not None and self._hover.get("seg") is seg and self._hover.get("zone") == "volume")
        dragging = self._drag is not None and self._drag.get("mode") == "volume" and self._drag.get("seg_id") == seg.id
        c.setAlpha(255 if (hovered or dragging) else 170)
        p.setPen(QPen(c, 2.4 if (hovered or dragging) else 1.4))
        if "volume" in seg.keyframes:
            from ...nle.render import eval_keyframes
            pts = []
            steps = max(2, int(r.width() // 4))
            for k in range(steps + 1):
                x = r.left() + r.width() * k / steps
                lt = (x - r.left()) / self.pps
                v = eval_keyframes(seg.keyframes["volume"], lt, seg.volume)
                pts.append(QPointF(x, r.bottom() - max(0, min(2, v)) / 2 * r.height()))
            p.drawPolyline(QPolygonF(pts))
        else:
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
        if hovered or dragging:
            txt = (self._typed + "%") if (dragging and self._typed) else f"{round(seg.volume * 100)}%"
            f = QFont(self.font())
            f.setBold(True)
            p.setFont(f)
            fm = QFontMetricsF(f)
            anchor_x = self.mapFromGlobal(QCursor.pos()).x()
            box = QRectF(min(max(anchor_x + 8, r.left() + 2), r.right() - fm.horizontalAdvance(txt) - 12),
                         max(r.top() + 1, y - fm.height() - 4), fm.horizontalAdvance(txt) + 10, fm.height() + 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 190))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor("white"))
            p.drawText(box, Qt.AlignCenter, txt)

    def _paint_fades(self, p: QPainter, seg: Segment, r: QRectF) -> None:
        shade = QColor(0, 0, 0, 90)
        p.setPen(Qt.NoPen)
        p.setBrush(shade)
        if seg.fade_in > EPS:
            fx = self.t_to_x(seg.start + seg.fade_in)
            p.drawPolygon(QPolygonF([QPointF(r.left(), r.top()), QPointF(fx, r.top()), QPointF(r.left(), r.bottom())]))
        if seg.fade_out > EPS:
            fx = self.t_to_x(seg.end - seg.fade_out)
            p.drawPolygon(QPolygonF([QPointF(r.right(), r.top()), QPointF(fx, r.top()), QPointF(r.right(), r.bottom())]))
        if seg.locked or r.width() <= 3 * EDGE_GRAB:
            return
        hovered_seg = self._hover is not None and self._hover.get("seg") is seg
        for which, fx in (("fade_in", self.t_to_x(seg.start + seg.fade_in)),
                          ("fade_out", self.t_to_x(seg.end - seg.fade_out))):
            hx = min(max(fx, r.left() + 7), r.right() - 7)
            active = hovered_seg and self._hover.get("zone") == which
            if not (hovered_seg or seg.id in self.ctl.selection or (which == "fade_in" and seg.fade_in > EPS)
                    or (which == "fade_out" and seg.fade_out > EPS)):
                continue
            p.setBrush(QColor("white") if active else QColor(255, 255, 255, 170))
            p.setPen(QPen(QColor(0, 0, 0, 160), 1))
            p.drawEllipse(QPointF(hx, r.top() + 7), 4.5, 4.5)

    def _paint_transition_badge(self, p: QPainter, seg: Segment, r: QRectF) -> None:
        tr = seg.transition_in
        w = max(14.0, tr.duration * self.pps)
        badge = QRectF(r.left(), r.top(), min(w, r.width()), min(18.0, r.height()))
        c = QColor(self._theme.turquoise())
        c.setAlpha(210)
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRect(badge)
        icons.draw_transition(p, QPointF(badge.left() + 9, badge.center().y()), 14, contrast_text(c))
        if badge.width() > 60:
            p.setPen(contrast_text(c))
            f = QFont(self.font())
            f.setPointSizeF(max(7.0, f.pointSizeF() * 0.8))
            p.setFont(f)
            p.drawText(badge.adjusted(18, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft, tr.kind.capitalize())

    def _paint_keyframes(self, p: QPainter, seg: Segment, r: QRectF) -> None:
        p.setPen(QPen(QColor(0, 0, 0, 180), 1))
        p.setBrush(SNAP_COLOR)
        y = r.bottom() - 7
        for lst in seg.keyframes.values():
            for k in lst:
                x = self.t_to_x(seg.start + k.t)
                p.drawPolygon(QPolygonF([QPointF(x, y - 5), QPointF(x + 5, y), QPointF(x, y + 5), QPointF(x - 5, y)]))

    def _paint_text_preview(self, p: QPainter, seg: Segment, r: QRectF) -> None:
        part = next((pt for pt in seg.parts if pt.kind == KIND_TEXT and pt.text), None)
        if part is None:
            return
        f = QFont(part.text.font_family)
        f.setPixelSize(max(9, int(r.height() * 0.32)))
        f.setBold(part.text.bold)
        f.setItalic(part.text.italic)
        p.setFont(f)
        area = r.adjusted(8, 14, -8, 0)
        text = part.text.text.replace("\n", " ")
        if part.text.bubble:
            # Bubble text is usually dark-on-white: show it on a small bubble
            # so it stays readable on the dark lane.
            fm = QFontMetricsF(f)
            tw = min(fm.horizontalAdvance(text) + 16, area.width())
            pill = QRectF(area.left() - 4, area.center().y() - fm.height() / 2 - 2, tw, fm.height() + 4)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(part.text.bubble_fill))
            p.drawRoundedRect(pill, pill.height() / 2, pill.height() / 2)
            area = pill.adjusted(8, 0, -4, 0)
        p.setPen(QColor(part.text.color))
        p.drawText(area, Qt.AlignVCenter | Qt.AlignLeft, text)

    # ---------------------------------------------------------------- headers / ruler / playhead
    def _paint_headers(self, p: QPainter) -> None:
        theme = self._theme
        hr = self.header_rect()
        p.save()
        p.setClipRect(hr)
        p.fillRect(hr, theme.card_background())
        proj = self.project
        text_c = QColor(self._appearance.card_text_color)
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.9))
        p.setFont(f)
        n = len(proj.tracks)
        for i, track in enumerate(proj.tracks):
            top, h = self.track_top(i), self.track_height(i)
            if top > self.height() or top + h < RULER_H:
                continue
            row = QRectF(hr.left(), top, hr.width(), h)
            hovered = self._hover is not None and self._hover["kind"] == "header" and self._hover["track"] == i
            if hovered:
                p.fillRect(row, theme.card_background().lighter(120))
            if self._drag is not None and self._drag.get("mode") == "track" and self._drag.get("from") == i:
                p.fillRect(row, theme.accent().darker(140))
            left = self.header_side == "left"
            handle_x = hr.left() + 14 if left else hr.right() - 14
            chevron_x = hr.left() + 34 if left else hr.right() - 34
            cy = top + (COLLAPSED_H / 2 if track.collapsed else min(h / 2, 18))
            icons.draw_handle_lines(p, QPointF(handle_x, cy), 14, text_c)
            icons.draw_chevron(p, QPointF(chevron_x, cy), 12, text_c, expanded=not track.collapsed)
            is_buffer = (i == 0 or i == n - 1) and track.is_empty()
            label = track.name or ("+" if is_buffer else f"Track {i}")
            c = QColor(text_c)
            if is_buffer:
                c.setAlpha(110)
            p.setPen(c)
            label_rect = QRectF(hr.left() + 46, cy - 10, hr.width() - 52, 20) if left \
                else QRectF(hr.left() + 6, cy - 10, hr.width() - 52, 20)
            p.drawText(label_rect, Qt.AlignVCenter | (Qt.AlignLeft if left else Qt.AlignRight), label)
            p.setPen(QPen(theme.card_background().darker(140), 1))
            p.drawLine(QPointF(row.left(), row.bottom() - 0.5), QPointF(row.right(), row.bottom() - 0.5))
        if self._drag is not None and self._drag.get("mode") == "track" and self._drag.get("to") is not None:
            to = self._drag["to"]
            y = self.track_top(to) + (self.track_height(to) if to > self._drag["from"] else 0)
            p.setPen(QPen(SNAP_COLOR, 3))
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        p.restore()
        # divider between headers and lanes
        x = hr.right() if self.header_side == "left" else hr.left()
        p.setPen(QPen(theme.card_background().darker(150), 2))
        p.drawLine(QPointF(x, RULER_H), QPointF(x, self.height()))

    def _paint_ruler(self, p: QPainter) -> None:
        theme = self._theme
        rr = QRectF(0, 0, self.width(), RULER_H)
        p.fillRect(rr, theme.card_background().darker(115))
        proj = self.project
        text_c = QColor(self._appearance.card_text_color)
        major = next((s for s in _TICK_STEPS if s * self.pps >= 90), _TICK_STEPS[-1])
        minor = major / (5 if major not in (0.25, 15, 30) else (5 if major == 0.25 else 3))
        if minor * self.pps < 6:
            minor = major
        t0 = max(0.0, self.scroll_t - major)
        t1 = self.x_to_t(self.lanes_left() + self.lanes_width())
        f = QFont(self.font())
        f.setPointSizeF(max(7.0, f.pointSizeF() * 0.82))
        p.setFont(f)
        p.save()
        p.setClipRect(QRectF(self.lanes_left(), 0, self.lanes_width(), RULER_H))
        k = math.floor(t0 / minor)
        faint = QColor(text_c)
        faint.setAlpha(110)
        while k * minor <= t1 + minor:
            t = k * minor
            x = self.t_to_x(t)
            is_major = abs(t / major - round(t / major)) < 1e-6
            p.setPen(QPen(text_c if is_major else faint, 1))
            p.drawLine(QPointF(x, RULER_H - (12 if is_major else 6)), QPointF(x, RULER_H))
            if is_major:
                p.drawText(QRectF(x + 3, 2, 90, RULER_H - 12), Qt.AlignLeft | Qt.AlignVCenter,
                           format_time(t, proj.fps, frames=major < 1))
            k += 1
        p.restore()
        # corner box above the headers: timecode lives in the toolbar, keep this plain
        hr = self.header_rect()
        p.fillRect(QRectF(hr.left(), 0, hr.width(), RULER_H), theme.card_background().darker(125))

    def _paint_playhead(self, p: QPainter) -> None:
        x = self.t_to_x(self.ctl.playhead)
        if not (self.lanes_left() - 10 <= x <= self.lanes_left() + self.lanes_width() + 10):
            return
        p.setRenderHint(QPainter.Antialiasing)
        p.save()
        p.setClipRect(QRectF(self.lanes_left() - 10, 0, self.lanes_width() + 20, self.height()))
        p.setPen(QPen(PLAYHEAD_COLOR, 2))
        p.drawLine(QPointF(x, RULER_H - 4), QPointF(x, self.height()))
        head = QPolygonF([QPointF(x - 8, 3), QPointF(x + 8, 3), QPointF(x + 8, RULER_H - 12),
                          QPointF(x, RULER_H - 3), QPointF(x - 8, RULER_H - 12)])
        p.setPen(QPen(PLAYHEAD_COLOR.darker(140), 1))
        p.setBrush(PLAYHEAD_COLOR)
        p.drawPolygon(head)
        p.restore()

    # ================================================================ mouse
    def _snap_threshold(self) -> float:
        return SNAP_PX / self.pps

    def _snapping(self, event) -> bool:
        return self.ctl.snapping and not (event.modifiers() & Qt.AltModifier)

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if self.project is None:
            return
        pos = event.position()
        h = self.hit(pos)
        if event.button() == Qt.RightButton:
            return  # contextMenuEvent handles it
        if event.button() != Qt.LeftButton or h is None:
            return
        mods = event.modifiers()
        base = {"press": pos, "moved": False, "mods": mods}
        kind = h["kind"]
        if kind == "ruler":
            self._drag = {**base, "mode": "playhead"}
            self.ctl.set_playhead(h["t"], snap=self._snapping(event), threshold=self._snap_threshold())
            return
        if kind == "header":
            if h["zone"] == "collapse":
                track = self.project.tracks[h["track"]]
                tid, new = track.id, not track.collapsed
                self.ctl.perform("Collapse track", lambda p: ops.set_track_collapsed(p, tid, new))
                self.set_scroll_y(self.scroll_y)
                return
            if h["zone"] in ("handle", "body"):
                self._drag = {**base, "mode": "track", "from": h["track"], "to": None}
            return
        if kind == "gap":
            track_id = self.project.tracks[h["track"]].id
            self.ctl.close_gap(track_id, h["t"])
            self._hover = None
            return
        if kind == "lane":
            # The playhead jumps on PRESS (not release) so a click feels instant.
            self.ctl.set_playhead(h["t"], snap=self._snapping(event), threshold=self._snap_threshold())
            self._drag = {**base, "mode": "lane", "t": h["t"], "track": h["track"],
                          "initial_sel": list(self.ctl.selection)}
            return
        if kind == "segment":
            seg = h["seg"]
            zone = h["zone"]
            if zone == "marker":
                self.ctl.set_playhead(h["extra"])
                return
            if zone in ("left", "right", "volume", "fade_in", "fade_out"):
                if seg.id not in self.ctl.selection:
                    self.ctl.set_selection([seg.id], anchor=seg.id)
                self._drag = {**base, "mode": {"left": "trim_l", "right": "trim_r"}.get(zone, zone),
                              "seg_id": seg.id}
                if zone == "volume":
                    self._typed = ""
                    self._drag["base"] = self.project.to_dict()
                    self.ctl.begin("Volume")
                return
            ctrl = bool(mods & Qt.ControlModifier)
            shift = bool(mods & Qt.ShiftModifier)
            if ctrl or shift or seg.id not in self.ctl.selection:
                self.ctl.click_select(seg.id, ctrl, shift)
                self._drag = {**base, "mode": "move", "seg_id": seg.id, "track": h["track"],
                              "reselect": False}
            else:
                # Clicking an already-selected segment: keep the group for a
                # drag; a plain click without dragging selects just it.
                self._drag = {**base, "mode": "move", "seg_id": seg.id, "track": h["track"],
                              "reselect": True}

    def mouseDoubleClickEvent(self, event) -> None:
        h = self.hit(event.position())
        if h is not None and h["kind"] == "segment":
            self.ctl.set_selection([h["seg"].id], anchor=h["seg"].id)
            self.open_properties.emit()
        elif h is not None and h["kind"] in ("lane", "ruler"):
            self.ctl.set_playhead(h["t"])

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        d = self._drag
        if d is None:
            self._update_hover(pos)
            return
        if not d["moved"]:
            if (pos - d["press"]).manhattanLength() < DRAG_START_PX and d["mode"] not in ("playhead", "volume"):
                return
            d["moved"] = True
            self._start_gesture(d)
        self._continue_gesture(d, pos, event)

    def _start_gesture(self, d: dict) -> None:
        mode = d["mode"]
        if mode in ("move", "trim_l", "trim_r", "fade_in", "fade_out"):
            d["base"] = self.project.to_dict()
            self.ctl.begin({"move": "Move", "trim_l": "Trim", "trim_r": "Trim",
                            "fade_in": "Fade in", "fade_out": "Fade out"}[mode])
            if mode == "move":
                d["ids"] = list(self.ctl.selection) if d["seg_id"] in self.ctl.selection else [d["seg_id"]]
                d["base_track"] = d["track"]

    def _restore_base(self, d: dict) -> None:
        restored = Project.from_dict(copy.deepcopy(d["base"]))
        self.project.__dict__.update(restored.__dict__)

    def _continue_gesture(self, d: dict, pos: QPointF, event) -> None:
        mode = d["mode"]
        snap = self._snapping(event)
        thr = self._snap_threshold()
        if mode == "playhead":
            self.ctl.set_playhead(self.x_to_t(pos.x()), snap=snap, threshold=thr)
            self._autoscroll(pos)
            return
        if mode == "lane":
            d["rubber"] = QRectF(d["press"], pos).normalized()
            self._apply_rubber(d, event)
            self.update()
            return
        if mode == "track":
            to = self.track_at_y(pos.y())
            d["to"] = to
            self._rev += 1
            self.update()
            return
        if mode == "move":
            dt = (pos.x() - d["press"].x()) / self.pps
            snapped = None

            def fn(p):
                nonlocal dt, snapped
                self._restore_base(d)
                target_track = self.track_at_y(pos.y())
                dtrack = (target_track - d["base_track"]) if target_track is not None else 0
                if snap:
                    dt, snapped = ops.snap_move(p, d["ids"], dt, self.ctl.playhead, thr)
                ops.move_segments(p, d["ids"], dt, dtrack, prefer=1 if dtrack > 0 else -1)
            self.ctl.live(fn)
            self._snap_line = snapped
            self._autoscroll(pos)
            return
        if mode in ("trim_l", "trim_r"):
            t = self.x_to_t(pos.x())
            snapped = None
            if snap:
                targets = [x for x in self._edge_targets(exclude={d["seg_id"]})] + [self.ctl.playhead]
                t, snapped = ops.snap_time(t, targets, thr)

            def fn(p):
                self._restore_base(d)
                if mode == "trim_l":
                    ops.trim_start(p, d["seg_id"], t)
                else:
                    ops.trim_end(p, d["seg_id"], t)
            self.ctl.live(fn)
            self._snap_line = snapped
            self._autoscroll(pos)
            return
        if mode in ("fade_in", "fade_out"):
            _, seg = self.project.find_segment(d["seg_id"])
            if seg is None:
                return
            t = self.x_to_t(pos.x())
            val = (t - seg.start) if mode == "fade_in" else (seg.end - t)
            val = max(0.0, val)

            def fn(p):
                self._restore_base(d)
                if mode == "fade_in":
                    ops.set_fades(p, [d["seg_id"]], fade_in=val)
                else:
                    ops.set_fades(p, [d["seg_id"]], fade_out=val)
            self.ctl.live(fn)
            return
        if mode == "volume":
            ti, seg = self.project.find_segment(d["seg_id"])
            if seg is None:
                return
            r = self.seg_rect(self.project.track_index(ti.id), seg)
            vol = max(0.0, min(2.0, 2.0 * (r.bottom() - pos.y()) / max(r.height(), 1)))
            if abs(vol - 1.0) < 0.04 and not (event.modifiers() & Qt.AltModifier):
                vol = 1.0             # a gentle detent at 100%
            self._typed = ""
            self._set_volume_live(d, vol)

    def _set_volume_live(self, d: dict, vol: float) -> None:
        sid = d["seg_id"]

        def fn(p):
            _, s = p.find_segment(sid)
            if s is None:
                return
            if "volume" in s.keyframes:
                ops.set_keyframe(p, sid, "volume", self.ctl.local_time(s), vol)
            else:
                ops.set_volume(p, [sid], vol)
        self.ctl.live(fn)

    def _edge_targets(self, exclude: set) -> list:
        return self.project.edge_times(exclude=exclude)

    def _apply_rubber(self, d: dict, event) -> None:
        rect = d["rubber"]
        ids = []
        for i, track in enumerate(self.project.tracks):
            for s in track.segments:
                if self.seg_rect(i, s).intersects(rect):
                    ids.append(s.id)
        if event.modifiers() & Qt.ControlModifier:
            ids = list(d["initial_sel"]) + [i for i in ids if i not in d["initial_sel"]]
        self.ctl.set_selection(ids)

    def _autoscroll(self, pos: QPointF) -> None:
        left = self.lanes_left()
        right = left + self.lanes_width()
        if pos.x() > right - 20:
            self.set_scroll_t(self.scroll_t + 20 / self.pps)
        elif pos.x() < left + 20 and self.scroll_t > 0:
            self.set_scroll_t(self.scroll_t - 20 / self.pps)

    def mouseReleaseEvent(self, event) -> None:
        d = self._drag
        self._drag = None
        self._snap_line = None
        if d is None:
            return
        mode = d["mode"]
        if mode == "lane" and not d["moved"]:
            self.ctl.set_selection([])
        elif mode == "track" and d["moved"] and d.get("to") is not None and d["to"] != d["from"]:
            a, b = d["from"], d["to"]
            self.ctl.perform("Reorder tracks", lambda p: ops.move_track(p, a, b))
        elif mode == "move" and not d["moved"] and d.get("reselect"):
            self.ctl.set_selection([d["seg_id"]], anchor=d["seg_id"])
        elif d["moved"] and mode in ("move", "trim_l", "trim_r", "fade_in", "fade_out"):
            self.ctl.end()
        elif mode == "volume":
            if self._typed:
                self._commit_typed(d)
            self.ctl.end()
            self._typed = ""
        self._rev += 1
        self.update()
        self._update_hover(event.position())

    def _commit_typed(self, d: dict) -> None:
        try:
            pct = float(self._typed)
        except ValueError:
            return
        self._set_volume_live(d, max(0.0, min(200.0, pct)) / 100.0)

    def keyPressEvent(self, event) -> None:
        d = self._drag
        if d is not None and d.get("mode") == "volume":
            txt = event.text()
            if txt.isdigit() or (txt == "." and "." not in self._typed):
                self._typed = (self._typed + txt)[:6]
                self._commit_typed(d)
                self._rev += 1
                self.update()
                return
            if event.key() == Qt.Key_Backspace:
                self._typed = self._typed[:-1]
                if self._typed:
                    self._commit_typed(d)
                self._rev += 1
                self.update()
                return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._commit_typed(d)
                return
        if event.key() == Qt.Key_Escape and d is not None:
            self._drag = None
            self.ctl.cancel()
            self._rev += 1
            self.update()
            return
        super().keyPressEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()
        super().leaveEvent(event)

    def _update_hover(self, pos: QPointF) -> None:
        h = self.hit(pos)
        old = self._hover_key()
        self._hover = h
        cursor = Qt.ArrowCursor
        if h is not None:
            if h["kind"] == "segment":
                z = h["zone"]
                cursor = {"left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor, "volume": Qt.SizeVerCursor,
                          "fade_in": Qt.SizeHorCursor, "fade_out": Qt.SizeHorCursor,
                          "marker": Qt.PointingHandCursor}.get(z, Qt.OpenHandCursor)
                if h["seg"].locked:
                    cursor = Qt.ForbiddenCursor if z == "body" else Qt.ArrowCursor
            elif h["kind"] == "header" and h["zone"] in ("handle", "body"):
                cursor = Qt.SizeVerCursor if h["zone"] == "handle" else Qt.ArrowCursor
            elif h["kind"] in ("gap", ) or (h["kind"] == "header" and h["zone"] == "collapse"):
                cursor = Qt.PointingHandCursor
            elif h["kind"] == "ruler":
                cursor = Qt.IBeamCursor
        self.setCursor(cursor)
        if self._hover_key() != old or (h is not None and h.get("zone") == "volume"):
            self.update()
        if h is not None and h["kind"] == "gap":
            self.setToolTip("Close gap")
        elif h is not None and h["kind"] == "segment" and h["zone"] == "marker":
            self.setToolTip("Split marker -- click to move the playhead here")
        else:
            self.setToolTip("")

    def wheelEvent(self, event) -> None:
        if self.project is None:
            return
        pos = event.position()
        ad = event.angleDelta()
        mods = event.modifiers()
        in_header = self.header_rect().contains(pos)
        if ad.x() and not ad.y():
            # horizontal wheel / trackpad: scroll time
            self.set_scroll_t(self.scroll_t - ad.x() / 120 * self.visible_seconds() * 0.1)
            return
        steps = ad.y() / 120.0
        if mods & Qt.ControlModifier:
            self.zoom_by(steps, anchor_x=pos.x())
        elif in_header or mods & Qt.AltModifier:
            self.set_scroll_y(self.scroll_y - steps * self.lane_h() * 0.5)
        elif mods & Qt.ShiftModifier:
            self.ctl.step_playhead(seconds=math.copysign(1.0, steps) * max(1, round(abs(steps))))
            self.ensure_playhead_visible()
        else:
            n = int(math.copysign(max(1, round(abs(steps))), steps))
            self.ctl.step_playhead(frames=n)
            self.ensure_playhead_visible()
        event.accept()

    # ================================================================ context menu
    def contextMenuEvent(self, event) -> None:
        menu = self.build_context_menu(QPointF(event.pos()))
        if menu is not None:
            menu.exec(event.globalPos())

    def build_context_menu(self, pos: QPointF) -> "QMenu | None":
        if self.project is None:
            return None
        from ..video_card import _menu_stylesheet
        h = self.hit(pos)
        menu = QMenu(self)
        menu.setStyleSheet(_menu_stylesheet(self._appearance))
        ctl = self.ctl
        if h is not None and h["kind"] == "segment":
            seg = h["seg"]
            if seg.id not in ctl.selection:
                ctl.set_selection([seg.id], anchor=seg.id)
            segs = ctl.selected_segments()
            menu.addAction("Split at Playhead\tS", ctl.split)
            a = menu.addAction("Combine\tC", ctl.combine)
            a.setEnabled(len(segs) >= 2)
            menu.addSeparator()
            menu.addAction(("Unlock" if all(s.locked for s in segs) else "Lock") + "\tL", lambda: ctl.toggle("locked"))
            menu.addAction(("Unmute" if all(s.muted for s in segs) else "Mute") + "\tM", lambda: ctl.toggle("muted"))
            menu.addAction(("Show" if not any(s.visible for s in segs) else "Hide") + "\tV",
                           lambda: ctl.toggle("visible"))
            menu.addSeparator()
            menu.addAction("Copy\tCtrl+C", ctl.copy)
            a = menu.addAction("Paste at Playhead\tCtrl+V", ctl.paste)
            a.setEnabled(bool(ctl.clipboard))
            menu.addAction("Duplicate\tCtrl+D", ctl.duplicate)
            if any(s.has_video and s.has_audio for s in segs):
                menu.addAction("Detach Audio", ctl.detach_audio)
            menu.addSeparator()
            tmenu = menu.addMenu("Transition In")
            tmenu.setStyleSheet(_menu_stylesheet(self._appearance))
            for label, kind in (("None", None), ("Crossfade", "crossfade"), ("Blur / Focus", "blur"),
                                ("Slide (push) from left", "slide"), ("Fade (wipe) from left", "fade")):
                tmenu.addAction(label, lambda k=kind: ctl.apply_transition(list(ctl.selection), k))
            menu.addAction("Properties…", self.open_properties.emit)
            menu.addSeparator()
            menu.addAction("Delete\tDel", ctl.delete)
            menu.addAction("Ripple Delete\tShift+Del", ctl.ripple_delete)
        elif h is not None and h["kind"] == "header":
            ti = h["track"]
            track = self.project.tracks[ti]
            tid = track.id
            menu.addAction("Expand" if track.collapsed else "Collapse",
                           lambda: ctl.perform("Collapse track",
                                               lambda p: ops.set_track_collapsed(p, tid, not track.collapsed)))
            menu.addAction("Add Track Above", lambda: ctl.perform("Add track", lambda p: ops.add_track(p, ti)))
            menu.addAction("Add Track Below", lambda: ctl.perform("Add track", lambda p: ops.add_track(p, ti + 1)))
            a = menu.addAction("Move Up", lambda: ctl.perform("Reorder tracks", lambda p: ops.move_track(p, ti, ti - 1)))
            a.setEnabled(ti > 0)
            a = menu.addAction("Move Down", lambda: ctl.perform("Reorder tracks", lambda p: ops.move_track(p, ti, ti + 1)))
            a.setEnabled(ti < len(self.project.tracks) - 1)
            menu.addSeparator()
            menu.addAction("Select All on Track", lambda: ctl.set_selection([s.id for s in track.segments]))
            menu.addAction("Delete Track", lambda: ctl.perform("Delete track", lambda p: ops.remove_track(p, tid)))
        elif h is not None and h["kind"] in ("lane", "gap"):
            t = h["t"]
            tid = self.project.tracks[h["track"]].id
            a = menu.addAction("Paste Here", lambda: (ctl.set_playhead(t), ctl.paste()))
            a.setEnabled(bool(ctl.clipboard))
            if ops.gap_at(self.project, tid, t) is not None:
                menu.addAction("Close Gap", lambda: ctl.close_gap(tid, t))
            menu.addAction("Add Text Here", lambda: ctl.add_text("Plain text", t=t, track_index=h["track"]))
            menu.addSeparator()
            menu.addAction("Select All\tCtrl+A", ctl.select_all)
        else:
            return None
        return menu

    # ================================================================ drag & drop in
    def _drop_payload(self, mime: QMimeData):
        if mime.hasFormat(MIME_ITEM):
            try:
                return json.loads(bytes(mime.data(MIME_ITEM)).decode())
            except Exception:
                return None
        if mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile() and os.path.isfile(u.toLocalFile())]
            if paths:
                return {"type": "files", "paths": paths}
        return None

    def dragEnterEvent(self, event) -> None:
        if self.project is not None and self._drop_payload(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        payload = self._drop_payload(event.mimeData())
        if payload is None or self.project is None:
            return
        pos = event.position()
        ti = self.track_at_y(pos.y())
        t = max(0.0, self.x_to_t(pos.x()))
        if payload["type"] == "transition":
            h = self.hit(pos)
            self._drop_ghost = None
            if h is not None and h["kind"] == "segment":
                event.acceptProposedAction()
            else:
                event.ignore()
            self._rev += 1
            self.update()
            return
        length = 5.0
        if payload["type"] == "files":
            length = sum(max(0.5, probe_cached(p_).get("duration", 5.0) or 5.0) for p_ in payload["paths"][:8])
        elif payload["type"] == "file":
            length = probe_cached(payload["path"]).get("duration", 5.0) or 5.0
        self._drop_ghost = (ti, t, t + length)
        self._rev += 1
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._drop_ghost = None
        self._rev += 1
        self.update()

    def dropEvent(self, event) -> None:
        payload = self._drop_payload(event.mimeData())
        self._drop_ghost = None
        self._rev += 1
        self.update()
        if payload is None or self.project is None:
            return
        pos = event.position()
        ti = self.track_at_y(pos.y())
        t = max(0.0, self.x_to_t(pos.x()))
        typ = payload["type"]
        if typ in ("files", "file"):
            paths = payload["paths"] if typ == "files" else [payload["path"]]
            for path in paths:
                seg = self.ctl.add_file(path, t=t, track_index=ti)
                if seg is not None:
                    t = seg.end
        elif typ == "text":
            self.ctl.add_text(payload.get("preset", "Plain text"), t=t, track_index=ti)
        elif typ == "transition":
            h = self.hit(pos)
            if h is not None and h["kind"] == "segment":
                self.ctl.apply_transition([h["seg"].id], payload.get("kind"), payload.get("duration", 0.5),
                                          payload.get("target", "both"), payload.get("direction", "left"))
        event.acceptProposedAction()


class TimelinePanel(QWidget):
    """TimelineView plus its two scroll bars."""

    def __init__(self, controller: EditorController, visuals, parent=None):
        super().__init__(parent)
        self.view = TimelineView(controller, visuals)
        self.hbar = CustomScrollBar(Qt.Horizontal)
        self.vbar = CustomScrollBar(Qt.Vertical)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        grid.addWidget(self.view, 0, 0)
        grid.addWidget(self.vbar, 0, 1)
        grid.addWidget(self.hbar, 1, 0)
        self._syncing = False
        self.view.view_changed.connect(self._sync_bars)
        self.hbar.valueChanged.connect(self._on_h)
        self.vbar.valueChanged.connect(self._on_v)

    _H_UNITS = 1000.0     # scroll bar units per second (bars are ints)

    def _sync_bars(self) -> None:
        v = self.view
        self._syncing = True
        vis = v.visible_seconds()
        total = v.content_seconds()
        self.hbar.setRange(0, int(max(0.0, total - vis) * self._H_UNITS))
        self.hbar.setPageStep(int(vis * self._H_UNITS))
        self.hbar.setSingleStep(max(1, int(vis * self._H_UNITS / 20)))
        self.hbar.setValue(int(v.scroll_t * self._H_UNITS))
        lanes_h = v.height() - RULER_H
        max_y = max(0.0, v.content_height() - lanes_h)
        self.vbar.setRange(0, int(max_y))
        self.vbar.setPageStep(int(max(1, lanes_h)))
        self.vbar.setValue(int(v.scroll_y))
        self.vbar.setVisible(max_y > 0)
        self._syncing = False

    def _on_h(self, value: int) -> None:
        if not self._syncing:
            self.view.set_scroll_t(value / self._H_UNITS)

    def _on_v(self, value: int) -> None:
        if not self._syncing:
            self.view.set_scroll_y(float(value))
