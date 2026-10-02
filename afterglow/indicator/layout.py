"""
Pure geometry for the indicator's overlay surface (no Qt windows, testable anywhere).

The surface is anchored to the anchor's edge(s) with margin 0 -- NOT the padding.  The
padding is applied here, in where the slots sit inside the surface, so the enter / exit
animations can start and finish truly off-screen at the edge instead of popping out of thin
air at the padding line.  It is sized to hold the whole stack plus travel room; toss / launch
arcs are designed to stay inside it, and the fall is clipped at the bottom of the screen.

Stacking: slot 0 is at the anchor; each later slot sits one item height + STACK_GAP further
from the anchored edge -- upward for bottom anchors and `left` / `right`, downward for top
anchors.  Fractional slot numbers (the 200 ms "close the gap" slide) are fine.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF

from . import ANCHORS, MAX_VISIBLE, STACK_GAP
from .draw import item_size

BADGE_HEIGHT_FACTOR = 0.40        # the "+N" badge is this fraction of an item's height


def anchor_edges(anchor: str) -> "set[str]":
    """The screen edges the surface is anchored to (corners = two, edge anchors = one, the centre = none)."""
    if anchor not in ANCHORS:
        anchor = "bottom_right"
    edges = set()
    if anchor.startswith("top"):
        edges.add("top")
    if anchor.startswith("bottom"):
        edges.add("bottom")
    if anchor.endswith("left") or anchor == "left":
        edges.add("left")
    if anchor.endswith("right") or anchor == "right":
        edges.add("right")
    return edges


ARM_HEADROOM = 0.42      # the clapper waits with its arm open: this much of the width rises above the board


def stack_gap(size: float) -> float:
    """The space between two stacked items: STACK_GAP plus the headroom the open arm needs, so
    a waiting clapper's arm never reaches into the item next to it."""
    return STACK_GAP + float(size) * ARM_HEADROOM


def _stack_height(h: float, gap: float = STACK_GAP) -> float:
    n = MAX_VISIBLE
    return n * h + (n - 1) * gap + (gap + h * BADGE_HEIGHT_FACTOR)


def surface_size(anchor: str, size: float, pad_x: float, pad_y: float,
                 screen: "tuple[float, float] | None" = None) -> "tuple[float, float]":
    """(width, height) of the overlay surface in logical px.  Corner anchors: ~3 item widths
    (+ the padding) by the full stack + 2 item heights (+ padding).  Edge anchors: the same
    along that edge; top / bottom are centred, 4 item widths wide; left / right are centred
    and tall enough for the stack to grow up from the middle."""
    it = item_size(size)
    w, h = it.width(), it.height()
    stack = _stack_height(h, stack_gap(size))
    if anchor in ("top", "bottom"):
        W, H = 4 * w, pad_y + stack + 2 * h
    elif anchor == "center":
        W, H = 4 * w, 2 * (stack + 2 * h)
    elif anchor in ("left", "right"):
        W, H = pad_x + 3 * w, 2 * (stack + 2 * h)
    else:
        W, H = pad_x + 3 * w, pad_y + stack + 2 * h
    if screen:
        W, H = min(W, screen[0]), min(H, screen[1])
    return float(W), float(H)


def slot_rect(anchor: str, size: float, pad_x: float, pad_y: float, slot: float,
              surface: "tuple[float, float]") -> QRectF:
    """The resting rect of stack slot ``slot`` (0 = at the anchor) inside the surface."""
    it = item_size(size)
    w, h = it.width(), it.height()
    W, H = surface
    step = h + stack_gap(size)
    if anchor in ("top", "bottom", "center"):
        left = (W - w) / 2
    elif anchor.endswith("left") or anchor == "left":
        left = pad_x
    else:
        left = W - pad_x - w
    if anchor.startswith("top"):
        top = pad_y + slot * step
    elif anchor.startswith("bottom"):
        top = H - pad_y - h - slot * step
    else:                                   # left / right / centre: slot 0 centred, stack grows upward
        top = (H - h) / 2 - slot * step
    return QRectF(left, top, w, h)


def badge_rect(anchor: str, size: float, pad_x: float, pad_y: float, surface: "tuple[float, float]") -> QRectF:
    """Where the "+N" badge sits: just beyond the last visible slot."""
    it = item_size(size)
    last = slot_rect(anchor, size, pad_x, pad_y, MAX_VISIBLE - 1, surface)
    bh = it.height() * BADGE_HEIGHT_FACTOR
    gap = stack_gap(size)
    if anchor.startswith("top"):
        top = last.bottom() + gap
    else:
        top = last.top() - gap - bh
    return QRectF(last.left(), top, last.width(), bh)


def surface_origin(anchor: str, surface: "tuple[float, float]", screen_geo: QRectF) -> "tuple[float, float]":
    """Where the surface sits on a screen (screen_geo in the compositor's global space) when it
    is placed by geometry -- the X11 fallback.  Mirrors what layer-shell anchors do."""
    W, H = surface
    edges = anchor_edges(anchor)
    if "left" in edges:
        x = screen_geo.left()
    elif "right" in edges:
        x = screen_geo.right() - W + 1
    else:
        x = screen_geo.left() + (screen_geo.width() - W) / 2
    if "top" in edges:
        y = screen_geo.top()
    elif "bottom" in edges:
        y = screen_geo.bottom() - H + 1
    else:
        y = screen_geo.top() + (screen_geo.height() - H) / 2
    return x, y
