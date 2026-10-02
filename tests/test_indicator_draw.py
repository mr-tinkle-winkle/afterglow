"""
Clip indicator drawing + geometry, rendered for real into offscreen images: every style,
colours, the icon (fit / position / not mirrored), the loading circle, the clap timeline,
and EVERY enter / exit / fail animation for EVERY anchor (boundary values, continuity, and
staying inside the overlay surface except through the anchor's own edge).

    QT_QPA_PLATFORM=offscreen python3 tests/test_indicator_draw.py
"""
import math
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QRectF, QPointF
from PySide6.QtGui import QImage, QPainter, QColor, QPainterPath, QTransform
from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from afterglow.indicator import draw, layout, ANCHORS, ENTER_ANIMATIONS, EXIT_ANIMATIONS, MAX_VISIBLE, STACK_GAP

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


TMP = Path(tempfile.mkdtemp(prefix="indicator_draw_"))


def canvas(w, h):
    img = QImage(int(w), int(h), QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    return img


def rgba(img, x, y):
    """The pixel WITH its alpha (QColor(int) would silently force alpha to 255)."""
    return QColor.fromRgba(img.pixel(int(x), int(y)))


def count_color(img, color, tol=6):
    c = QColor(color)
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            q = rgba(img, x, y)
            if q.alpha() > 250 and abs(q.red() - c.red()) <= tol and abs(q.green() - c.green()) <= tol and abs(q.blue() - c.blue()) <= tol:
                n += 1
    return n


def px(img, x, y):
    return rgba(img, round(x), round(y))


def count_nonclear(img):
    return sum(1 for y in range(img.height()) for x in range(img.width()) if rgba(img, x, y).alpha() > 8)


def same(a, b, tol=6):
    return abs(a.red() - b.red()) <= tol and abs(a.green() - b.green()) <= tol and abs(a.blue() - b.blue()) <= tol


def solid_icon(path, w, h, color):
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(color))
    img.save(str(path))
    return str(path)


# ---------------------------------------------------------------- sizes / colours
sz = draw.item_size(200)
check(abs(sz.width() - 200) < 1e-6 and abs(sz.height() / sz.width() - draw.ASPECT) < 1e-6, "item box follows the size setting")
d = draw.resolve_colors({"board": "#123456", "stripe_a": "nonsense"})
check(d["board"].name() == "#123456" and d["stripe_a"].name() == draw.DEFAULT_COLORS["stripe_a"], "colours: overrides apply, junk falls back to defaults")
from afterglow import config as _cfg
ap = _cfg.AppearanceSettings()
check(draw.DEFAULT_COLORS == draw.afterglow_defaults() == draw.afterglow_defaults(ap), "defaults come from the afterglow theme")
_cel = draw.afterglow_defaults(ap, "cel")
check(draw.DEFAULT_COLORS["stripe_a"] == ap.afterglow_color_turquoise and draw.DEFAULT_COLORS["stripe_b"] == ap.afterglow_color_accent
      and draw.DEFAULT_COLORS["board"] == ap.afterglow_color_card_background and _cel["glove_outline"] == ap.afterglow_color_app_background,
      "...stripes in the theme's turquoise and accent, the board in its card colour, cel gloves outlined in its app colour")
from afterglow.indicator import hands2d as _h2
check(all(draw.DEFAULT_COLORS[k] == v for k, v in _h2.RETRO_COLORS.items()) and _cel["glove"] != draw.DEFAULT_COLORS["glove"]
      and all(_cel[k] == draw.DEFAULT_COLORS[k] for k in draw.CLAPPER_COLOR_KEYS),
      "the retro look (the default) has its own glove colours; the clapper's colours are the same for both looks")
ap2 = _cfg.AppearanceSettings()
ap2.afterglow_color_turquoise = "#ff8800"
ap2.afterglow_color_card_background = "#112233"
custom = draw.afterglow_defaults(ap2, "cel")
check(custom["stripe_a"] == "#ff8800" and custom["board"] == "#112233" and custom["cuff"] == "#ff8800",
      "...and follow a customised theme (the cel look's cuffs too)")
check(draw.afterglow_defaults(ap2)["cuff"] == _h2.RETRO_COLORS["cuff"] and draw.afterglow_defaults(ap2)["stripe_a"] == "#ff8800",
      "...the retro look keeps its red cuffs, the clapper still follows the theme")
ap2.afterglow_color_accent = "not a colour"
check(draw.afterglow_defaults(ap2)["stripe_b"] == "#152c4f", "...an invalid theme colour falls back to the built-in one")
check(set(draw.DEFAULT_COLORS) == set(draw.CLAPPER_COLOR_KEYS) | set(draw.HANDS_COLOR_KEYS), "every part has a default")
check(draw.DEFAULT_CIRCLE_COLOR == "#9a9a9a" and draw.DEFAULT_OVERLAY_CIRCLE_COLOR == "#9b5cff", "the circles stay gray and purple")

# ---------------------------------------------------------------- the clapper, colours applied
rect = QRectF(20, 40, 200, draw.item_size(200).height())
img = canvas(260, 260)
vivid = draw.resolve_colors({"stripe_a": "#ff0000", "stripe_b": "#00ff00", "hinge": "#ffff00", "board": "#0000ff",
                             "lines": "#ff00ff", "outline": "#00ffff"})
p = QPainter(img)
draw.draw_clapper(p, rect, draw.ClapPose(), vivid, None)
p.end()
for key in draw.CLAPPER_COLOR_KEYS:
    n = count_color(img, vivid[key])
    check(n > 12, f"clapper: the '{key}' colour is drawn ({n} px)")
g = draw.clapper_geometry(rect)
check(same(px(img, rect.left() + 12, rect.bottom() - 12), vivid["board"]), "clapper: the board is the board colour")
y1, y2 = g["line_y"]
check(same(px(img, rect.center().x() - 30, y1), vivid["lines"]), "clapper: chalk lines on the board")
# stripes alternate along the stick
row = [px(img, x, g["stick"].center().y()) for x in range(int(rect.left()) + 10, int(rect.right()) - 10)]
check(any(same(c, vivid["stripe_a"]) for c in row) and any(same(c, vivid["stripe_b"]) for c in row), "clapper: the stick is striped in two tones")

# closed vs open stick
img_c, img_o = canvas(260, 260), canvas(260, 260)
for im, pose in ((img_c, draw.ClapPose(open=0.0)), (img_o, draw.ClapPose(open=1.0))):
    p = QPainter(im)
    draw.draw_clapper(p, rect, pose, vivid, None)
    p.end()
def top_extent(im):
    for y in range(im.height()):
        if any(rgba(im, x, y).alpha() > 0 for x in range(im.width())):
            return y
check(top_extent(img_o) < top_extent(img_c) - 15, "clapper: the open stick rises well above the closed one (rotates about the hinge)")
# right tip rises, hinge (left end) stays
def has_stick_at(im, x, y):
    return rgba(im, x, y).alpha() > 0
check(not has_stick_at(img_o, rect.right() - 5, rect.top() + 5) and has_stick_at(img_c, rect.right() - 5, rect.top() + 5),
      "clapper: it opens from the right (the stick is hinged on the left)")

# impact lines only on impact
img_i = canvas(260, 260)
p = QPainter(img_i)
draw.draw_clapper(p, rect, draw.ClapPose(open=0, squash=0, impact=1.0), vivid, None)
p.end()
check(count_color(img_i, vivid["lines"]) > count_color(img_c, vivid["lines"]) + 15, "clapper: impact lines appear at the tip on impact")

# squash compresses the board
img_s = canvas(260, 260)
p = QPainter(img_s)
draw.draw_clapper(p, rect, draw.ClapPose(open=0, squash=3.0), vivid, None)
p.end()
def bottom_extent(im):
    for y in range(im.height() - 1, -1, -1):
        if any(rgba(im, x, y).alpha() > 0 for x in range(im.width())):
            return y
def top_of(im):
    return top_extent(im)
check(top_of(img_s) > top_of(img_c) and abs(bottom_extent(img_s) - bottom_extent(img_c)) <= 1, "clapper: 3 px squash is about the bottom edge")

# ---------------------------------------------------------------- the icon on the clapper
icon_path = solid_icon(TMP / "wide.png", 400, 100, "#ff00ff")
icon = draw.load_icon(icon_path)
img = canvas(260, 260)
p = QPainter(img)
ir = draw.draw_clapper(p, rect, draw.ClapPose(), draw.resolve_colors({}), icon)
p.end()
board = g["board"]
check(ir is not None and ir.top() >= y2, f"icon sits below the chalk lines ({ir.top():.1f} >= {y2:.1f})")
check(abs(ir.center().x() - rect.center().x()) < 1.0, "icon is centred horizontally on the board")
check(ir.height() <= board.height() * 0.45 + 0.5 and ir.width() <= rect.width() * 0.8 + 0.5, "icon fits inside ~45% of the board height")
check(abs(ir.width() / ir.height() - 4.0) < 0.02, "icon keeps its aspect (wide icon)")
check(same(px(img, ir.center().x(), ir.center().y()), QColor("#ff00ff")), "icon pixels are really drawn")
tall = draw.load_icon(solid_icon(TMP / "tall.png", 50, 300, "#00ff00"))
img = canvas(260, 260)
p = QPainter(img)
ir2 = draw.draw_clapper(p, rect, draw.ClapPose(), draw.resolve_colors({}), tall)
p.end()
check(ir2.height() <= board.height() * 0.45 + 0.5 and abs(ir2.height() / ir2.width() - 6.0) < 0.05, "tall icon: limited by the height, aspect kept")
check(draw.load_icon("") is None and draw.load_icon(None) is None and draw.load_icon("/nonexistent/x.png") is None, "no icon: empty / missing file -> None")
bad = TMP / "bad.png"
bad.write_text("not an image")
check(draw.load_icon(str(bad)) is None, "unreadable file -> None (no crash)")
img = canvas(260, 260)
p = QPainter(img)
check(draw.draw_clapper(p, rect, draw.ClapPose(), draw.resolve_colors({}), draw.load_icon(str(bad))) is None, "drawing with an unreadable icon just draws no icon")
p.end()
# cache follows file changes
cp = TMP / "swap.png"
solid_icon(cp, 40, 40, "#ff0000")
a = draw.load_icon(str(cp))
check(a is not None and QColor(a.pixel(5, 5)).red() == 255, "icon loads")
import time
time.sleep(0.02)
solid_icon(cp, 40, 40, "#0000ff")
os.utime(cp, (time.time() + 5, time.time() + 5))
b = draw.load_icon(str(cp))
check(QColor(b.pixel(5, 5)).blue() == 255, "icon cache refreshes when the file changes")

# ---------------------------------------------------------------- hands
hrect = QRectF(60, 60, 200, draw.item_size(200).height())
hv = draw.resolve_colors({"glove": "#ff0000", "glove_outline": "#0000ff", "cuff": "#ffff00", "stitches": "#00ff00"})
DEF = draw.resolve_colors({})


def near(img, color, tol=40):
    return count_color(img, color, tol)


def hands_img(pose, colors=hv, icon=None, w=320, h=300):
    img = canvas(w, h)
    p = QPainter(img)
    got = draw.draw_hands(p, hrect, pose, colors, icon)
    p.end()
    return img, got


READY, REST = draw.ClapPose(open=1.0), draw.ClapPose(clapped=True)
def clapping(ms, age=2.0):
    pz = draw.clap_pose(ms)
    pz.clap_ms, pz.age = ms, age
    return pz


img, _ = hands_img(REST)
for key in draw.HANDS_COLOR_KEYS:
    n = near(img, hv[key])
    check(n > 30, f"hands: the '{key}' colour is drawn ({n} px)")
check(near(img, hv["glove"]) > near(img, hv["cuff"]), "hands: the glove fill is the dominant colour")
img, _ = hands_img(READY, DEF)
lum = [rgba(img, x, y).lightness() for y in range(img.height()) for x in range(img.width())
       if rgba(img, x, y).alpha() > 250 and rgba(img, x, y).lightness() > 120]
check(len({v // 6 for v in lum}) >= 8 and max(lum) - min(lum) >= 45, f"hands: shaded round, not flat ({len({v // 6 for v in lum})} tone bands)")
check(len(draw.GLOVE_FINGERS) == 3, "hands: cartoon gloves -- three fingers and a thumb")
# the thumb sits on the palm's edge (the side facing the other hand), not on the back of the hand
palm_xs = [x for x, _ in draw.GLOVE_PALM]
_g = draw.GlovePose(0.5, 0.8, 0.0, 0.5, "palmar", {n: (0, 0, 0) for n in ("index", "middle", "pinky")}, 0.0)
_geo = draw.glove_geometry(_g, 100.0)
_path = draw._smooth_open(_geo["thumb"])
_exit = next((_path.pointAtPercent(i / 200.0) for i in range(201) if not _geo["palm"].contains(_path.pointAtPercent(i / 200.0))), None)
check(_exit is not None and _exit.x() < 0 and _exit.y() > -0.38 * 100, f"hands: the thumb leaves the palm low, on the side facing the other hand ({_exit})")
check(abs(draw.GLOVE_THUMB[3]) >= 50, "hands: the thumb sticks out from the hand by default, like a real thumb")
check(not _geo["web"].isEmpty() and _geo["silhouette"].contains(_geo["web"].boundingRect().center()), "hands: a web of skin joins the thumb to the hand")
# as big as the clapper: the gloves fill the item's box (along its limiting side -- the clasp is taller than it is
# wide) and are centred on it (the point animations turn about)
for name, pz in (("ready", READY), ("impact", clapping(draw.HANDS_CONTACT_MS + 20)), ("rest", REST)):
    b = draw.hands_bounds(hrect, pz)
    check(max(b.width() / hrect.width(), b.height() / hrect.height()) >= 0.85 and min(b.width() / hrect.width(), b.height() / hrect.height()) >= 0.6,
          f"hands ({name}): fill the item's box ({b.width():.0f}x{b.height():.0f} of {hrect.width():.0f}x{hrect.height():.0f})")
    check(abs(b.center().x() - hrect.center().x()) <= hrect.width() * 0.03 and abs(b.center().y() - hrect.center().y()) <= hrect.height() * 0.07,
          f"hands ({name}): centred on the box, so a spin turns them in place ({b.center().x() - hrect.center().x():+.1f}, {b.center().y() - hrect.center().y():+.1f})")
# ready: apart, fingers a little bent; idle: every finger moves on its own
fr0 = draw.hands_frame(draw.ClapPose(open=1.0, age=0.0))
fr1 = draw.hands_frame(draw.ClapPose(open=1.0, age=0.7))
check(not fr0.clasped and fr0.right.x - fr0.left.x > 0.5, "ready: the hands wait apart")
check(all(min(fr0.left.bend[n]) > 0 and min(fr0.right.bend[n]) > 0 for n in ("index", "middle", "pinky")), "ready: fingers slightly bent, not stiff")
deltas = [round(fr1.right.bend[n][0] - fr0.right.bend[n][0], 2) for n in ("index", "middle", "pinky")]
check(any(abs(d) > 0.5 for d in deltas) and len(set(deltas)) == 3, f"ready idle: each finger wobbles on its own rhythm {deltas}")
d2 = [fr1.right.bend["middle"][j] - fr0.right.bend["middle"][j] for j in range(3)]
check(all(x * d2[0] >= 0 for x in d2), "idle: a finger's joints bend together (a natural curl), not against each other")
rs0, rs1 = draw.hands_frame(draw.ClapPose(clapped=True, age=3.0)), draw.hands_frame(draw.ClapPose(clapped=True, age=3.8))
check(rs0.clasped and any(abs(rs1.right.bend[n][0] - rs0.right.bend[n][0]) > 0.3 for n in ("index", "middle", "pinky")),
      "rest: the clasp idles too")
# the clap: wind up, swing in (streaks), impact (clasp + fx), settle, rest -- clasped from contact on
wind = draw.hands_frame(clapping(draw.HANDS_WINDUP_MS))
check(wind.right.x - wind.left.x > fr0.right.x - fr0.left.x and wind.right.spread > fr0.right.spread and sum(wind.right.bend["middle"]) < sum(fr0.right.bend["middle"]),
      "wind-up: the hands pull further apart and the fingers open before the swing")
sw = draw.hands_frame(clapping(draw.HANDS_WINDUP_MS + draw.HANDS_SWING_MS * 0.6))
check(sw.swing > 0.4 and not sw.clasped, "swing: speed streaks while the hands come in")
hit = draw.hands_frame(clapping(draw.HANDS_CONTACT_MS + 10))
check(hit.clasped and hit.fx >= 0 and hit.squash > 0.5, "impact: clasped, squashed, ring + lines start")
check(hit.right.side == "dorsal" and hit.left.side == "palmar", "impact: the front hand shows its back, the other its palm (like the reference)")
check(hit.right.y < hit.left.y - 0.09, f"impact: the back-of-hand glove sits well above the palm glove, leaving room for the thumb below it ({hit.left.y - hit.right.y:.3f})")
check(all(sum(hit.right.bend[n]) > 80 for n in ("index", "middle", "pinky")), "impact: the front fingers bend over the other hand")
w = hrect.width()
def tips(g):
    geo = draw.glove_geometry(g, g.s * w)
    tr = draw.glove_transform(hrect, g)
    return {n: tr.map(geo["fingers"][n][-1]) for n in ("index", "middle", "pinky")}, tr.map(geo["silhouette"]), tr.map(geo["thumb"][-1])
ltips, lsil, lthumb = tips(hit.left)
rtips, rsil, _ = tips(hit.right)
check(sum(1 for t in ltips.values() if not rsil.contains(t)) >= 2 and max(t.x() for t in ltips.values()) > max(t.x() for t in rtips.values()),
      "impact: the back hand's fingers come out past the front hand's pinky side")
check(min(math.hypot(t.x() - lthumb.x(), t.y() - lthumb.y()) for t in rtips.values()) < w * 0.22 and min(t.x() for t in rtips.values()) < hrect.center().x(),
      "impact: the front hand's fingertips reach across to the other hand's thumb")
settle = draw.hands_frame(clapping(draw.HANDS_CLAP_TOTAL_MS))
check(settle.clasped and settle.squash < 0.05 and sum(settle.right.bend["middle"]) < sum(hit.right.bend["middle"]), "settle: the clasp relaxes a little")
check(draw.hands_frame(draw.ClapPose(clapped=True)).clasped, "after the clap (processing, exit): the hands stay clasped")
check(draw.clap_total_ms("hands") == draw.HANDS_CLAP_TOTAL_MS > draw.clap_total_ms("clapper") == draw.CLAP_TOTAL_MS,
      "the hands' clap lasts longer than the clapper's (the wind-up)")
# what gets drawn: streaks beside the hands in the swing, the ring above them on the hit, nothing extra at rest
mid_img, _ = hands_img(clapping(draw.HANDS_WINDUP_MS + draw.HANDS_SWING_MS * 0.7), DEF)
hit_img, _ = hands_img(clapping(draw.HANDS_CONTACT_MS + 60), DEF)
rest_img, _ = hands_img(REST, DEF)
def outside_hands(im, pz):
    b = draw.hands_bounds(hrect, pz)
    return sum(1 for y in range(im.height()) for x in range(im.width()) if rgba(im, x, y).alpha() > 60 and not b.adjusted(-2, -2, 2, 2).contains(QPointF(x, y)))
check(outside_hands(mid_img, clapping(draw.HANDS_WINDUP_MS + draw.HANDS_SWING_MS * 0.7)) > 30, "swing: streaks are drawn beside the hands")
o_hit, o_rest = outside_hands(hit_img, clapping(draw.HANDS_CONTACT_MS + 60)), outside_hands(rest_img, REST)
check(o_hit > 150 and o_hit > 3 * o_rest, f"impact: the ring and lines reach beyond the hands; at rest only the outline / shadow edge does ({o_hit} vs {o_rest})")
# front hand: right by default; "left" mirrors the picture, the icon is never flipped
icon = draw.load_icon(solid_icon(TMP / "sq.png", 200, 200, "#ff00ff"))
img, ir = hands_img(REST, DEF, icon)
check(ir is not None and ir.center().x() > hrect.center().x() - hrect.width() * 0.05, "icon: on the back of the front (right) glove")
check(count_color(img, QColor("#ff00ff")) > 30, "icon: visible")
check(all(hand.contains(pt) for hand in [tips(draw.hands_frame(REST).right)[1]] for pt in (ir.center(),)), "icon: inside the glove")
asym = QImage(100, 100, QImage.Format_ARGB32)
asym.fill(QColor("#0000ff"))
q = QPainter(asym)
q.fillRect(0, 0, 50, 100, QColor("#ff0000"))
q.end()
asym.save(str(TMP / "asym.png"))
ai = draw.load_icon(str(TMP / "asym.png"))
for front in ("right", "left"):
    pz = draw.ClapPose(clapped=True, front=front)
    img, ir = hands_img(pz, DEF, ai)
    c = ir.center()
    l, r = px(img, ir.left() + ir.width() * 0.3, c.y()), px(img, ir.right() - ir.width() * 0.3, c.y())
    check(l.red() > 150 and l.blue() < 120 and r.blue() > 150 and r.red() < 120, f"icon: not flipped (front={front})")
bl, br = draw.hands_bounds(hrect, draw.ClapPose(clapped=True, front="left")), draw.hands_bounds(hrect, REST)
check(abs((bl.center().x() - hrect.center().x()) + (br.center().x() - hrect.center().x())) < 1.0, "front=left: the mirror image of front=right")
img, ir = hands_img(clapping(draw.HANDS_CONTACT_MS + 30), DEF, icon)
check(ir is not None and count_color(img, QColor("#ff00ff")) > 30, "icon: stays on through the impact")
# opacity layer leaves room for the wind-up and the effects
for ms in (draw.HANDS_WINDUP_MS, draw.HANDS_CONTACT_MS + 40):
    b = draw.hands_bounds(hrect, clapping(ms))
    m = hrect.width() * 0.35
    check(hrect.adjusted(-m, -m, m, m).contains(b), f"hands at {ms:.0f} ms fit the opacity layer")

# ---------------------------------------------------------------- loading circle
for name, col in (("gray", draw.DEFAULT_CIRCLE_COLOR), ("purple", draw.DEFAULT_OVERLAY_CIRCLE_COLOR)):
    img = canvas(80, 80)
    p = QPainter(img)
    draw.draw_circle(p, QPointF(40, 40), 28, 0.0, QColor(col), 1.0, 1.0)
    p.end()
    ring = [rgba(img, x, y) for y in range(80) for x in range(80) if rgba(img, x, y).alpha() > 200]
    check(len(ring) > 100 and sum(1 for c in ring if abs(c.red() - QColor(col).red()) < 25 and abs(c.blue() - QColor(col).blue()) < 25) > 60,
          f"loading circle draws in the {name} colour")
img0, img1 = canvas(80, 80), canvas(80, 80)
for im, ang in ((img0, 0.0), (img1, 180.0)):
    p = QPainter(im)
    draw.draw_circle(p, QPointF(40, 40), 28, ang, QColor("#ffffff"), 1.0, 1.0)
    p.end()
check(any(img0.pixel(x, y) != img1.pixel(x, y) for x in range(80) for y in range(80)), "the circle's arc rotates")
imga = canvas(80, 80)
p = QPainter(imga)
draw.draw_circle(p, QPointF(40, 40), 28, 0.0, QColor("#ffffff"), 0.55, 1.0)
p.end()
mx = max(rgba(imga, x, y).alpha() for x in range(80) for y in range(80))
check(130 <= mx <= 150, f"circle is ~55% opaque by default (max alpha {mx})")
check(abs(draw.circle_angle(0.5) - 180) < 1e-6 and abs(draw.circle_angle(1.0)) < 1e-6, "rotates about one turn per second")
dx, red, a = draw.circle_fail(0.3)
check(red > 0.9 and abs(dx) <= 4.0 and draw.circle_fail(1.0)[2] < 1e-9 and draw.circle_fail(0.0)[2] == 1.0, "overlay_fail: flashes red, shakes, fades out")
check(draw.circle_diameter(96) == 29 and draw.circle_diameter(20) == 20, "circle is ~28 px at the default size")

# ---------------------------------------------------------------- the clap timeline
check(draw.clap_pose(0).open == 1.0 and draw.clap_pose(-5).open == 1.0, "clap: starts READY (stick already up / hands already apart), not shut")
check(draw.ClapPose().open == 0.0, "the neutral pose is shut")
check(draw.clap_pose(10).open > 0.95 and 0.4 < draw.clap_pose(draw.CLAP_SHUT_MS * 0.75).open < 0.6, "clap: accelerates shut (ease-in)")
check(abs(draw.clap_pose(draw.CLAP_SHUT_MS).open) < 0.12 and draw.clap_pose(draw.CLAP_SHUT_MS - 1).open < 0.1, "clap: shut within ~80 ms")
check(0 < draw.clap_pose(draw.CLAP_SHUT_MS + 20).squash <= 3.9 and draw.clap_pose(draw.CLAP_SHUT_MS + 20).impact > 0.5, "clap: a 2-3 px squash and impact lines on the snap")
check(0 < draw.clap_pose(draw.CLAP_SHUT_MS + 70).open < 0.12, "clap: a small rebound after the impact")
check(draw.clap_pose(draw.CLAP_TOTAL_MS).impact <= 0.01 and draw.clap_pose(draw.CLAP_TOTAL_MS).squash == 0 and draw.clap_pose(draw.CLAP_TOTAL_MS).open == 0,
      "clap: settled shut by the end")
check(max(draw.clap_pose(t).open for t in range(0, 380, 5)) <= 1.0, "clap: never opens beyond ready")
check(abs(draw.CLAPPER_OPEN_DEGREES - 28) < 1e-6, "clap: ~28 degrees open")
check(abs(draw.idle_bob(0.4, 100)) < 3 and max(abs(draw.idle_bob(i / 20, 100)) for i in range(40)) <= 2.3, "stay mode: a subtle idle bob")

# ---------------------------------------------------------------- animations: every anchor
def setup(anchor, size=96, pad=(32, 32), slot=0):
    surf = layout.surface_size(anchor, size, pad[0], pad[1])
    rect = layout.slot_rect(anchor, size, pad[0], pad[1], slot, surf)
    return surf, rect, draw.Space.for_rect(anchor, rect, *surf)


def ident(xf):
    return (abs(xf.dx) < 1e-6 and abs(xf.dy) < 1e-6 and abs(((xf.rot + 180) % 360) - 180) < 1e-6 and abs(xf.scale - 1) < 1e-6
            and abs(xf.sx - 1) < 1e-6 and abs(xf.sy - 1) < 1e-6 and abs(xf.skew) < 1e-9 and abs(xf.opacity - 1) < 1e-6)


bad_enter_end, bad_enter_start, bad_exit_start, bad_exit_end, bad_fail_start, bad_fail_end, jumpy, leaks = [], [], [], [], [], [], [], []
for anchor in ANCHORS:
    for slot in (0, 3):
        surf, rect, sp = setup(anchor, slot=slot)
        S = QRectF(0, 0, *surf)
        allowed_sides = set(layout.anchor_edges(anchor))
        if anchor == "center":                  # no screen edge of its own: edge animations fade through the bottom
            allowed_sides = {"bottom"}
        for phase, kinds in (("enter", ENTER_ANIMATIONS), ("exit", EXIT_ANIMATIONS), ("fail", ("fail",))):
            for kind in kinds:
                tag = f"{phase}/{kind}@{anchor}#{slot}"
                x0, x1 = draw.animate(phase, kind, 0.0, anchor, sp), draw.animate(phase, kind, 1.0, anchor, sp)
                if phase == "enter":
                    if not ident(x1):
                        bad_enter_end.append(tag)
                    if not draw.is_gone(rect, x0, S):
                        bad_enter_start.append(tag)
                else:
                    if not ident(x0):
                        (bad_exit_start if phase == "exit" else bad_fail_start).append(tag)
                    if not draw.is_gone(rect, x1, S):
                        (bad_exit_end if phase == "exit" else bad_fail_end).append(tag)
                # continuity + staying inside the surface except through the anchor's edge
                prev = None
                for i in range(0, 241):
                    t = i / 240
                    xf = draw.animate(phase, kind, t, anchor, sp)
                    mr = draw.mapped_rect(rect, xf)
                    c = mr.center()
                    if prev is not None:
                        dist = math.hypot(c.x() - prev[0].x(), c.y() - prev[0].y())
                        if dist > 0.22 * max(sp.edge_travel, sp.room_down, 3 * rect.height()) or abs(xf.rot - prev[1]) > 40 or abs(xf.opacity - prev[2]) > 0.3:
                            jumpy.append(f"{tag}@{t:.2f} ({dist:.0f}px, {abs(xf.rot - prev[1]):.0f} deg, {abs(xf.opacity - prev[2]):.2f})")
                    prev = (c, xf.rot, xf.opacity)
                    if xf.opacity > 0.15:
                        sides = set()
                        if mr.left() < -0.5: sides.add("left")
                        if mr.right() > surf[0] + 0.5: sides.add("right")
                        if mr.top() < -0.5: sides.add("top")
                        if mr.bottom() > surf[1] + 0.5: sides.add("bottom")
                        extra = sides - allowed_sides
                        # the fall / launch leave through the bottom; `drop` arrives from the top (fading) -- by design
                        if phase == "fail" or kind == "fall":
                            extra -= {"bottom"}
                        if kind == "drop":
                            extra -= {"top"}
                        if extra:
                            leaks.append(f"{tag}@{t:.2f} leaves via {sorted(extra)}")
                            break
check(not bad_enter_end, f"every enter animation ends at the identity transform ({len(bad_enter_end)} bad: {bad_enter_end[:3]})")
check(not bad_enter_start, f"every enter animation starts invisible / off-screen ({len(bad_enter_start)} bad: {bad_enter_start[:3]})")
check(not bad_exit_start, f"every exit animation starts at the identity transform ({bad_exit_start[:3]})")
check(not bad_exit_end, f"every exit animation ends gone ({len(bad_exit_end)} bad: {bad_exit_end[:3]})")
check(not bad_fail_start and not bad_fail_end, f"the fail launch starts in place and ends off the surface ({bad_fail_start[:2]} {bad_fail_end[:2]})")
check(not jumpy, f"no animation jumps between frames ({len(jumpy)}: {jumpy[:3]})")
check(not leaks, f"animations stay inside the surface except through the anchor's own edge ({len(leaks)}: {leaks[:4]})")
check(set(ENTER_ANIMATIONS) <= set(draw.DURATION_MS["enter"]) and set(EXIT_ANIMATIONS) <= set(draw.DURATION_MS["exit"]), "every selectable animation has a duration")
check(draw.duration_ms("enter", "slide") == 300 and draw.duration_ms("enter", "drop") == 450 and draw.duration_ms("exit", "bow") == 550
      and draw.duration_ms("fail", "fail") == 900, "durations match the spec")
try:
    draw.animate("enter", "nonsense", 0.5, "right", setup("right")[2])
    check(False, "unknown animation raises")
except ValueError:
    check(True, "unknown animation raises")

# directions: slide comes in from the anchor's edge
for anchor, axis, sign in (("right", "dx", +1), ("left", "dx", -1), ("bottom_right", "dy", +1), ("bottom_left", "dy", +1), ("top_left", "dy", -1),
                           ("top_right", "dy", -1), ("top", "dy", -1), ("bottom", "dy", +1), ("center", "dy", +1)):
    _, rect, sp = setup(anchor)
    xf = draw.animate("enter", "slide", 0.0, anchor, sp)
    v = getattr(xf, axis)
    check(v * sign > 0 and abs(v) >= 0.99 * sp.edge_travel, f"slide enters from the {anchor} anchor's edge ({axis}={v:.0f})")
    ex = draw.animate("exit", "slide", 1.0, anchor, sp)
    check(getattr(ex, axis) * sign > 0, f"slide exits through the {anchor} anchor's edge")
# the fall always falls DOWN, the launch rises first then falls
_, rect, sp = setup("bottom_right")
ys = [draw.animate("fail", "fail", i / 100, "bottom_right", sp).dy for i in range(101)]
check(min(ys) < -0.5 * rect.height() and ys[-1] > 0 and ys.index(min(ys)) < 60, "launch: up first, then gravity takes it down off the screen")
check(all(draw.animate("exit", "fall", i / 20, "bottom_right", sp).dy >= -1e-9 for i in range(21)), "fall: straight down")
# enter pop overshoots then settles
sc = [draw.animate("enter", "pop", i / 100, "right", sp).scale for i in range(101)]
check(max(sc) > 1.12 and min(sc[60:95]) < 1.0 and abs(sc[-1] - 1) < 1e-9, "pop: 0 -> 1.15 -> 0.95 -> 1")
# drop bounces twice
dys = [draw.animate("enter", "drop", i / 400, "bottom_right", sp).dy for i in range(401)]
ups = sum(1 for i in range(1, 400) if dys[i - 1] > dys[i] < dys[i + 1] and False)
bounces = sum(1 for i in range(220, 400) if dys[i] < dys[i - 1] and dys[i] <= dys[i + 1 if i < 400 else i] and False)
peaks = [i for i in range(221, 399) if dys[i] < dys[i - 1] and dys[i] <= dys[i + 1]]
check(len(peaks) == 2, f"drop: lands, then two small bounces ({len(peaks)} peaks)")
# flip squeezes horizontally
check(draw.animate("enter", "flip", 0.0, "right", sp).sx == 0 and draw.animate("exit", "flip", 1.0, "right", sp).sx == 0, "flip: horizontal scale 0 <-> 1")
# peek pauses half in
pk = [draw.animate("enter", "peek", t, "right", sp) for t in (0.3, 0.4, 0.5)]
check(abs(pk[0].dx - pk[1].dx) < 1e-6 and abs(pk[1].dx - pk[2].dx) < 1.5 and abs(pk[0].dx - (sp.edge_travel - sp.w / 2)) < 1.0,
      "peek: creeps half in, pauses a beat, then hops in")
# xform maths
r = QRectF(0, 0, 100, 80)
check(draw.mapped_rect(r, draw.Xform(dx=10, dy=-5)).topLeft() == QPointF(10, -5), "xform translate")
check(abs(draw.mapped_rect(r, draw.Xform(scale=2)).width() - 200) < 1e-6 and abs(draw.mapped_rect(r, draw.Xform(scale=2)).center().x() - 50) < 1e-6, "xform scales about the centre")
check(abs(draw.mapped_rect(r, draw.Xform(rot=90)).width() - 80) < 1e-6, "xform rotates about the centre")
rp = draw.mapped_rect(r, draw.Xform(rot=90, pivot=(0, -200)))
check(rp.center().x() < -100, "xform rotates about an off-centre pivot (swing)")

# ---------------------------------------------------------------- layout
for anchor in ANCHORS:
    for pad in ((32, 32), (0, 0), (80, 14)):
        surf = layout.surface_size(anchor, 96, *pad)
        it = draw.item_size(96)
        r0 = layout.slot_rect(anchor, 96, pad[0], pad[1], 0, surf)
        if anchor.endswith("right") or anchor == "right":
            ok_x = abs(surf[0] - r0.right() - pad[0]) < 1e-6
        elif anchor.endswith("left") or anchor == "left":
            ok_x = abs(r0.left() - pad[0]) < 1e-6
        else:
            ok_x = abs(r0.center().x() - surf[0] / 2) < 1e-6
        if anchor.startswith("bottom"):
            ok_y = abs(surf[1] - r0.bottom() - pad[1]) < 1e-6
        elif anchor.startswith("top"):
            ok_y = abs(r0.top() - pad[1]) < 1e-6
        else:
            ok_y = abs(r0.center().y() - surf[1] / 2) < 1e-6
        check(ok_x and ok_y, f"layout {anchor} pad={pad}: slot 0 sits at the anchor with X/Y padding (X unused for top/bottom, Y for left/right)")
        r1 = layout.slot_rect(anchor, 96, pad[0], pad[1], 1, surf)
        gap = (r0.top() - r1.bottom()) if not anchor.startswith("top") else (r1.top() - r0.bottom())
        check(abs(gap - layout.stack_gap(96)) < 1e-6 and layout.stack_gap(96) > STACK_GAP + 96 * 0.3,
              f"layout {anchor}: the next slot is one clapper height + the gap (12 px + room for the open arm) further {'down' if anchor.startswith('top') else 'up'}")
        S = QRectF(0, 0, *surf)
        last = layout.slot_rect(anchor, 96, pad[0], pad[1], MAX_VISIBLE - 1, surf)
        badge = layout.badge_rect(anchor, 96, pad[0], pad[1], surf)
        check(S.contains(last) and S.contains(badge), f"layout {anchor}: five stacked slots and the +N badge fit in the surface")
        check(layout.anchor_edges(anchor) == ({"top"} if anchor == "top" else {"bottom"} if anchor == "bottom" else {"left"} if anchor == "left" else {"right"} if anchor == "right"
              else set() if anchor == "center" else set(anchor.split("_"))), f"layout {anchor}: anchored to the right screen edge(s)")
surf = layout.surface_size("bottom_right", 96, 32, 32)
check(abs(surf[0] - (32 + 3 * 96)) < 1e-6, "corner surface is ~3 clapper widths wide (+ padding)")
for _sz in (96, 156, 300):
    _s = layout.surface_size("bottom_right", _sz, 32, 32)
    _last = layout.slot_rect("bottom_right", _sz, 32, 32, MAX_VISIBLE - 1, _s)
    check(_last.top() >= 0 and layout.badge_rect("bottom_right", _sz, 32, 32, _s).top() >= 0, f"size {_sz}: the whole stack incl. the badge fits the surface")
check(layout.surface_size("left", 96, 32, 32, screen=(1920, 700))[1] == 700, "surface never exceeds the screen")
cs = layout.surface_size("center", 96, 32, 32)
cr = layout.slot_rect("center", 96, 32, 32, 0, cs)
check(abs(cr.center().x() - cs[0] / 2) < 1e-6 and abs(cr.center().y() - cs[1] / 2) < 1e-6, "center: slot 0 sits in the middle of the surface")
cx, cy = layout.surface_origin("center", cs, QRectF(0, 0, 1920, 1080))
check(abs(cx + cs[0] / 2 - 960) < 1 and abs(cy + cs[1] / 2 - 540) < 1, "center: the surface is centred on the screen")
check(layout.slot_rect("center", 96, 32, 32, 1, cs).bottom() + layout.stack_gap(96) <= cr.top() + 1e-6, "center: the stack grows upward from the middle")
scr = QRectF(1920, 0, 1920, 1080)
ox, oy = layout.surface_origin("bottom_right", surf, scr)
check(abs(ox + surf[0] - 3840) < 2 and abs(oy + surf[1] - 1080) < 2, "geometry placement (X11 fallback): flush with the corner")
lsurf = layout.surface_size("left", 96, 32, 32, screen=(1920, 1080))
ox, oy = layout.surface_origin("left", lsurf, scr)
check(ox == 1920 and abs(oy + lsurf[1] / 2 - 540) < 1, "geometry placement: left edge, centred vertically")


# ---------------------------------------------------------------- corners use the TOP / BOTTOM edge, never the side edges
for anchor in ("top_left", "top_right", "bottom_left", "bottom_right", "top", "bottom"):
    _, rect, sp = setup(anchor)
    for kind in ("slide", "spin", "toss", "peek"):
        x0 = draw.animate("enter", kind, 0.0, anchor, sp)
        check(abs(x0.dy) > rect.height() * 0.4 and abs(x0.dx) < rect.width() * 1.01, f"enter/{kind} at {anchor}: arrives vertically, not from the side")
    for kind in ("slide", "zip", "spin", "toss", "bow"):
        x1 = draw.animate("exit", kind, 1.0, anchor, sp)
        check(abs(x1.dy) > rect.height() * 0.4, f"exit/{kind} at {anchor}: leaves vertically")
check(draw.edge_vec("top_left") == (0, -1) and draw.edge_vec("bottom_right") == (0, 1) and draw.edge_vec("left") == (-1, 0)
      and draw.edge_vec("right") == (1, 0) and draw.edge_vec("center") == (0, 1), "edge vectors: corners top / bottom, sides left / right, centre bottom")
# a sideways arc goes toward the middle of the screen (never off the nearest side edge)
for anchor, sign in (("bottom_right", -1), ("top_right", -1), ("bottom_left", 1), ("top_left", 1)):
    _, rect, sp = setup(anchor)
    xs = [draw.animate("enter", "toss", i / 40, anchor, sp).dx for i in range(41)]
    check(all(x * sign >= -1e-6 for x in xs) and max(abs(x) for x in xs) > 0.3 * rect.width(), f"toss at {anchor}: the arc swings inward")
_, rect, sp = setup("bottom_right")
check(draw.animate("fail", "fail", 0.5, "bottom_right", sp).dx < 0, "the launch drifts toward the screen's middle")
_, rect, sp = setup("bottom_left")
check(draw.animate("fail", "fail", 0.5, "bottom_left", sp).dx > 0, "...from a left corner too")
# the centre: edge-bound animations fade while they travel
_, rect, sp = setup("center")
check(draw.animate("enter", "slide", 0.0, "center", sp).opacity == 0 and abs(draw.animate("enter", "slide", 0.5, "center", sp).opacity - 1) < 1e-6,
      "centre: slide fades in while it travels")
check(draw.animate("exit", "slide", 1.0, "center", sp).opacity == 0 and draw.animate("exit", "slide", 0.0, "center", sp).opacity == 1, "...and fades out")
check(draw.animate("enter", "fade", 0.0, "center", sp).opacity == 0 and draw.animate("enter", "pop", 1.0, "center", sp).scale == 1.0, "centre: fade / pop are unchanged")

# ---------------------------------------------------------------- clapper opacity: composed opaque, drawn at the opacity
rect_item = QRectF(30, 40, 200, draw.item_size(200).height())


def render_item(style, opacity, pose=None):
    im = canvas(260, 260)
    p = QPainter(im)
    draw.draw_item(p, rect_item, style, pose or draw.ClapPose(open=0.0), draw.resolve_colors({}), None, opacity=opacity)
    p.end()
    return im


def amax(im):
    return max(rgba(im, x, y).alpha() for y in range(0, im.height(), 2) for x in range(0, im.width(), 2))


for style in ("clapper", "hands"):
    full, half = render_item(style, 1.0), render_item(style, 0.5)
    check(amax(full) >= 250 and 120 <= amax(half) <= 135, f"{style}: 50% opacity makes the whole item ~50% ({amax(half)}/255)")
    alphas = {rgba(half, x, y).alpha() for y in range(60, 200, 4) for x in range(60, 200, 4) if rgba(half, x, y).alpha() > 100}
    check(not alphas or (max(alphas) - min(alphas)) <= 40, f"{style}: parts don't show through each other at 50% (alphas {sorted(alphas)[:5]})")
    check(amax(render_item(style, 0.0)) == 0, f"{style}: opacity 0 draws nothing")
a1, a2 = render_item("clapper", 1.0), canvas(260, 260)
p = QPainter(a2)
draw.draw_clapper(p, rect_item, draw.ClapPose(open=0.0), draw.resolve_colors({}), None)
p.end()
check(all(a1.pixel(x, y) == a2.pixel(x, y) for x in range(0, 260, 5) for y in range(0, 260, 5)), "full opacity is exactly the plain drawing")
im = canvas(260, 260)
p = QPainter(im)
p.setOpacity(0.5)
draw.draw_item(p, rect_item, "clapper", draw.ClapPose(open=0.0), draw.resolve_colors({}), None, opacity=0.5)
p.end()
check(55 <= amax(im) <= 72, f"a fading animation and the opacity setting multiply ({amax(im)})")

# ---------------------------------------------------------------- the ring's pulse
check(draw.pulse_scale(-1) == 1.0 and draw.pulse_scale(1.0) == 1.0 and draw.pulse_scale(0.0) == 1.0, "pulse: no bump outside its window")
check(1.2 < draw.pulse_scale(0.5) < 1.4, f"pulse: the ring swells ~30% at the middle ({draw.pulse_scale(0.5):.2f})")
img0, img1 = canvas(120, 120), canvas(120, 120)
for im, prog in ((img0, -1.0), (img1, 0.3)):
    p = QPainter(im)
    draw.draw_halo(p, QPointF(60, 60), 30, QColor("#ffffff"), prog, 1.0, 1.0)
    p.end()
check(count_nonclear(img0) == 0 and count_nonclear(img1) > 50, "halo: nothing outside the pulse, a thin ring during it")
img2 = canvas(120, 120)
p = QPainter(img2)
draw.draw_halo(p, QPointF(60, 60), 30, QColor("#ffffff"), 0.95, 1.0, 1.0)
p.end()
check(max(rgba(img2, x, y).alpha() for y in range(120) for x in range(120)) < max(rgba(img1, x, y).alpha() for y in range(120) for x in range(120)), "halo: fades as it expands")

# ---------------------------------------------------------------- the traced Hands (hands2d)
from afterglow.indicator import HANDS_CONTACT_MS as _HC, HANDS_LOOKS
D = _h2.data()
check(_h2.available() and all(len(D[s]["frames"]) > 10 for s in ("ready", "clap", "rest")), "traced frames: ready, clap and rest sequences ship with the app")
check(len(D["ready"]["frames"]) == D["ready"]["fps"] * 2 and len(D["rest"]["frames"]) == D["rest"]["fps"] * 2, "...the idles are 2 s loops")
check(abs(D["timing"]["contact_ms"] - _HC) < 1, f"...the hands meet when the clap sound plays ({D['timing']['contact_ms']} ms vs HANDS_CONTACT_MS {_HC})")
check(D["clap"]["frames"][int(_HC / 1000 * D["clap"]["fps"]) + 3].get("squash", 0) > 0, "...the impact squashes")
check(sum(1 for f in D["clap"]["frames"] if "streak" in f) >= 3, "...the swing has speed lines")
check(_h2.frame_for(0.5, -1, -1, False)[0] == "ready" and _h2.frame_for(0.5, 0.2, 200, False)[0] == "clap"
      and _h2.frame_for(9.0, 5.0, -1, True)[0] == "rest", "frame_for: idle before the clap, the clap, then the clasp's idle")
st_ms = D["timing"]["settled_ms"]
last = len(D["clap"]["frames"]) / D["clap"]["fps"]
seq, i, _ = _h2.frame_for(9.0, last + 0.001, -1, True)
check(seq == "rest" and i == int((last + 0.001) * D["rest"]["fps"]) % len(D["rest"]["frames"]),
      "...the clasp's idle runs on the time since the clap, so it continues where the clap ended")
check(_h2.frame_for(1.0, -1, -1, False)[1] != _h2.frame_for(1.5, -1, -1, False)[1], "...the ready idle moves")


def look_img(look, front="right", icon=None, pose=None):
    pz = pose or draw.ClapPose(open=1.0, age=0.4)
    pz.look, pz.front = look, front
    img = canvas(320, 300)
    p = QPainter(img)
    got = draw.draw_hands(p, hrect, pz, draw.resolve_colors(draw.afterglow_defaults(None, look)), icon)
    p.end()
    return img, got


r_img, _ = look_img("retro")
c_img, _ = look_img("cel")
check(near(r_img, QColor(_h2.RETRO_COLORS["glove"]), 12) > 600 and near(r_img, QColor(_h2.RETRO_COLORS["cuff"]), 30) > 100,
      "retro look: cream gloves and red cuffs")
check(near(c_img, QColor(draw.afterglow_defaults(None, "cel")["glove"]), 12) > 600 and near(c_img, QColor(_h2.RETRO_COLORS["glove"]), 6) < 50,
      "cel look: the theme's pale gloves (no cream)")
check(near(r_img, QColor("#000000"), 40) > 400, "retro look: black ink")
check(set(HANDS_LOOKS) == set(_h2.LOOKS) and _h2.DEFAULT_LOOK == "retro", "two looks, retro by default")
# mirrored for the left hand in front; the icon reads the same either way
b_r = draw.hands_bounds(hrect, draw.ClapPose(open=1.0, age=0.4, front="right"))
b_l = draw.hands_bounds(hrect, draw.ClapPose(open=1.0, age=0.4, front="left"))
check(abs((b_r.left() - hrect.left()) - (hrect.right() - b_l.right())) < 1.0, "front = left mirrors the picture")
ic = QImage(40, 40, QImage.Format_ARGB32)
ic.fill(QColor("#0000ff"))
pp = QPainter(ic)
pp.fillRect(0, 0, 20, 40, QColor("#ff0000"))
pp.end()
for front in ("right", "left"):
    im, rect_ = look_img("cel", front, ic, draw.ClapPose(open=1.0, age=0.4))
    reds = [(x, y) for y in range(300) for x in range(320) if same(rgba(im, x, y), QColor("#ff0000"), 40)]
    blues = [(x, y) for y in range(300) for x in range(320) if same(rgba(im, x, y), QColor("#0000ff"), 40)]
    check(rect_ is not None and len(reds) > 15 and len(blues) > 15, f"icon on the back of the glove ({front} in front)")
    if reds and blues:
        check(sum(x for x, _ in reds) / len(reds) < sum(x for x, _ in blues) / len(blues),
              f"...and not mirrored ({front} in front): its left half is still on the left")
# a frame mid-swing and mid-impact draw (and the clap's first frames cross-fade from the idle)
for ms in (5, 300, _HC + 10, _HC + 200, 2000):
    pz = clapping(ms)
    pz.since_clap = ms / 1000.0
    im, _ = look_img("retro", pose=pz)
    check(count_nonclear(im) > 2000, f"clap at {ms} ms draws")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("ALL PASS")
