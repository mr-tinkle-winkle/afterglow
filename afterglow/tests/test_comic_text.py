"""
Comic effects (scribblenado / unease wiggle lines / bulging anger vein) and the
Bounce in / Jiggle in-out text transitions: the renderer (what the preview and
the export draw), the Properties controls and the Text presets.

    QT_QPA_PLATFORM=offscreen python3 tests/test_comic_text.py
"""
import os
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="comic_home_")
os.environ["HOME"] = HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

app = QApplication([])
from afterglow import db
from afterglow.nle import comic, ops, render as R
from afterglow.nle.model import KIND_TEXT, Part, Segment, TextStyle, default_project

db.init_db()
FAILS = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)


W, H = 640, 360


def project_with(st, x=0.0, y=0.0, dur=4.0):
    p = default_project(W, H, 30)
    seg = Segment(parts=[Part(kind=KIND_TEXT, source="", src_in=0, src_out=dur, has_video=True, has_audio=False,
                              text=st)], name="t")
    seg.transform.x, seg.transform.y = x, y
    ops.place(p, seg, 1, 0.0, prefer=-1)
    return p


def drawn(img):
    """[(x, y)] of pixels that aren't the black background (sampled every 2 px)."""
    out = []
    for yy in range(0, img.height(), 2):
        for xx in range(0, img.width(), 2):
            c = img.pixelColor(xx, yy)
            if c.red() + c.green() + c.blue() > 40:
                out.append((xx, yy))
    return out


def centroid(pts):
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def same(a, b):
    return a.constBits().tobytes() == b.constBits().tobytes() if a.size() == b.size() else False


# ---------------------------------------------------------------- comic effects
for name in ("scribblenado", "wiggle_lines", "vein"):
    color = "#ffffff" if name != "vein" else ""          # light on the black canvas, the vein keeps its red
    st = TextStyle(text="", comic_effect=name, effect_size=0.5, effect_color=color)
    r = R.Renderer(project_with(st))
    a, b, a2 = r.frame(0.10, W, H), r.frame(0.55, W, H), r.frame(0.10, W, H)
    pts = drawn(a)
    check(len(pts) > 150, f"{name}: an effect-only element (no words) draws ({len(pts)} px)")
    c = centroid(pts)
    check(c is not None and abs(c[0] - W / 2) < W * 0.08 and abs(c[1] - H / 2) < H * 0.12,
          f"{name}: centred on the element's position ({c and tuple(round(v) for v in c)})")
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    check(max(ys) - min(ys) < H * 0.5 * 1.15 and max(ys) - min(ys) > H * 0.5 * 0.5,
          f"{name}: about effect_size tall ({max(ys) - min(ys)} px for 50% of {H})")
    check(not same(a, b), f"{name}: animates")
    check(same(a, a2), f"{name}: the same moment always renders the same (deterministic)")
    st.effect_speed = 0.0
    r2 = R.Renderer(project_with(st))
    check(same(r2.frame(0.1, W, H), r2.frame(0.9, W, H)), f"{name}: animation speed 0 = still")
    r.close(), r2.close()

vein = R.Renderer(project_with(TextStyle(text="", comic_effect="vein", effect_size=0.5)))
img = vein.frame(0.2, W, H)
reds = sum(1 for (x, y) in drawn(img) if img.pixelColor(x, y).red() > 150 and img.pixelColor(x, y).green() < 110)
check(reds > 100, f"vein: red by default ({reds} red px)")
check(img.pixelColor(W // 2, H // 2).red() < 40, "vein: the middle is the empty '+' gap")
vein.close()

# words + effect: the effect takes the picture's slot, above the words by default
lay_words = R.text_layout(TextStyle(text="GRR"), H)
lay_both = R.text_layout(TextStyle(text="GRR", comic_effect="scribblenado", effect_size=0.3), H)
check(lay_both["rect"].height() > lay_words["rect"].height() + H * 0.25 and lay_both["image_rect"].bottom()
      <= lay_both["rect"].top() + H * 0.3 + 1, "with words, the effect sits above them and the element grows")
check(R.text_layout(TextStyle(text="x", comic_effect="bogus"), H)["effect"] == "", "an unknown effect name is ignored")

# ---------------------------------------------------------------- bounce in
base_st = dict(text="BOING", font_size=0.12, color="#ffffff")
rest = R.Renderer(project_with(TextStyle(**base_st)))
rest_img = rest.frame(2.0, W, H)
rest_c = centroid(drawn(rest_img))
for side, sign in (("down", (0, 1)), ("up", (0, -1)), ("left", (-1, 0)), ("right", (1, 0))):
    r = R.Renderer(project_with(TextStyle(**base_st, bounce_in=0.8, bounce_from=side)))
    first = drawn(r.frame(0.0, W, H))
    check(not first, f"bounce from {side}: starts far off-screen (nothing drawn at t=0)")
    # the overshoot: some moment carries it PAST its spot, away from where it came from
    past = 0.0
    for i in range(1, 40):
        c = centroid(drawn(r.frame(0.8 * i / 40, W, H)))
        if c:
            d = ((c[0] - rest_c[0]) * sign[0] + (c[1] - rest_c[1]) * sign[1])
            past = min(past, d)
    check(past < -4, f"bounce from {side}: overshoots past its spot ({past:.1f} px) before landing")
    check(same(r.frame(1.2, W, H), rest.frame(1.2, W, H)), f"bounce from {side}: lands exactly in place")
    r.close()

# ---------------------------------------------------------------- jiggle in / out
r = R.Renderer(project_with(TextStyle(**base_st, bounce_in=0.5, jiggle_in=0.8, jiggle_out=0.6)))
during = [r.frame(0.5 + 0.8 * i / 8, W, H) for i in range(1, 7)]
check(sum(not same(f, rest.frame(1.0, W, H)) for f in during) >= 5, "jiggle in: wobbles after the bounce lands")
check(same(r.frame(1.6, W, H), rest.frame(1.6, W, H)), "jiggle in: settled exactly into place afterwards")
check(not same(r.frame(3.8, W, H), rest.frame(3.8, W, H)), "jiggle out: wobbling again near the end")
jig_only = R.Renderer(project_with(TextStyle(**base_st, jiggle_in=1.0)))
check(not same(jig_only.frame(0.2, W, H), rest.frame(0.2, W, H)), "jiggle in without a bounce starts right away")
r.close(), jig_only.close()

# jiggle rotates (not just shifts): the text's bounding box changes shape
r = R.Renderer(project_with(TextStyle(**base_st, jiggle_in=1.0)))
pts_r = drawn(r.frame(0.08, W, H))
pts_0 = drawn(rest_img)
h_r = max(p[1] for p in pts_r) - min(p[1] for p in pts_r)
h_0 = max(p[1] for p in pts_0) - min(p[1] for p in pts_0)
check(h_r > h_0 + 3, f"jiggle tilts the text (height {h_0} -> {h_r} px)")
r.close()

# everything is scaled to fit short elements, like the other in/out effects
w = comic.motion_windows(TextStyle(bounce_in=2, jiggle_in=2, jiggle_out=2), 3.0)
check(abs(w["bi"] + w["ji"] + w["jo"] - 3.0) < 1e-9, "bounce + jiggles are fitted into a too-short element")

# saved and loaded with the project
from afterglow.nle.model import Project
p = project_with(TextStyle(text="", comic_effect="vein", effect_color="#00ff00", bounce_in=0.4, bounce_from="left",
                           jiggle_in=0.3, jiggle_out=0.2, effect_size=0.3, effect_speed=2.0))
q = Project.from_dict(p.to_dict())
st2 = next(iter(q.all_segments())).parts[0].text
check((st2.comic_effect, st2.effect_color, st2.bounce_from, st2.jiggle_out, st2.effect_speed)
      == ("vein", "#00ff00", "left", 0.2, 2.0), "effect + bounce/jiggle settings survive save/load")

# ---------------------------------------------------------------- Properties + presets
from afterglow.gui.advanced_editor.controller import EditorController, TEXT_PRESETS
from afterglow.gui.advanced_editor.properties import PropertiesPanel
from afterglow.gui.advanced_editor.browser import BrowserPanel

ctl = EditorController()
ctl.set_project(default_project(W, H, 30), "import", "/nonexistent.mp4")
props = PropertiesPanel(ctl)
for preset in ("Scribblenado", "Unease lines", "Anger vein"):
    check(preset in TEXT_PRESETS and TEXT_PRESETS[preset].comic_effect, f"Text preset '{preset}' exists")
seg = ctl.add_text("Anger vein")
app.processEvents()
check(seg is not None and seg.name == "Anger vein", "adding the Anger vein preset names the element after it")
check(props._fields["comic_effect"].currentData() == "vein", "Properties > Comic Effect shows the effect")
props._fields["effect_size"].setValue(40)
props._end_edit()
S = lambda: ctl.project.find_segment(seg.id)[1].parts[0].text
check(abs(S().effect_size - 0.4) < 1e-9, "effect size edits the element")
i = props._fields["comic_effect"].findData("wiggle_lines")
props._fields["comic_effect"].setCurrentIndex(i)
app.processEvents()
check(S().comic_effect == "wiggle_lines", "switching the effect in Properties")
props._fields["bounce_in"].setValue(0.6)
props._end_edit()
props._fields["bounce_from"].setCurrentIndex(props._fields["bounce_from"].findData("right"))
props._fields["jiggle_in"].setValue(0.4)
props._end_edit()
props._fields["jiggle_out"].setValue(0.25)
props._end_edit()
check((S().bounce_in, S().bounce_from, S().jiggle_in, S().jiggle_out) == (0.6, "right", 0.4, 0.25),
      "Text Transitions: Bounce in / from, Jiggle in / out edit the element")
ctl.undo()
check(S().jiggle_out == 0.0, "…each an undo step")
br = BrowserPanel(ctl)
items = [br.text_list.item(k).text() for k in range(br.text_list.count())]
check("Anger vein" not in items and "POW!" not in items, "expressions / sound effects aren't in the Text list...")
citems = [br.comic_list.item(k).text() for k in range(br.comic_list.count())]
check(all(comic.EFFECTS[k][0] in citems for k in comic.EFFECTS) and "POW!" in citems and "Onomatopoeia" in citems
      and citems.index("Anger") < citems.index("Anger vein"),
      f"...they're on the Comic tab, grouped by feeling, with every effect + the onomatopoeia ({len(citems)} rows)")

# =========================================================================
# round 15: every expression's own in/out + idle; Animate in/out + Idle; lettering
# =========================================================================
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter


def fx_img(name, t, g, out=False, animated=True):
    img = QImage(220, 220, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    comic.draw_comic_effect(p, name, QRectF(30, 30, 160, 160), t, QColor(comic.DEFAULT_EFFECT_COLOR[name]),
                            animated, g, out)
    p.end()
    return img


def inked(img):
    return sum(1 for yy in range(0, img.height(), 3) for xx in range(0, img.width(), 3)
               if img.pixelColor(xx, yy).alpha() > 30)


no_idle, no_trans = [], []
for name in comic.EFFECTS:
    full = fx_img(name, 0.7, 1.0)
    if inked(full) < 8:
        FAILS.append(f"{name} draws nothing")
    if inked(fx_img(name, 0.7, 0.0)) != 0:
        FAILS.append(f"{name} draws at g=0")
    if same(fx_img(name, 0.7, 0.45), full) or same(fx_img(name, 0.7, 0.45, True), full):
        no_trans.append(name)
    idle_frames = [fx_img(name, t_, 1.0) for t_ in (0.13, 0.4, 0.71, 1.1)]
    if all(same(idle_frames[0], f_) for f_ in idle_frames[1:]):
        no_idle.append(name)
    if not same(fx_img(name, 0.4, 1.0), fx_img(name, 0.4, 1.0)):
        FAILS.append(f"{name} not deterministic")
check(not no_trans, f"all {len(comic.EFFECTS)} expressions have their own in AND out animation ({no_trans or 'all'})")
check(not no_idle, f"...and an idle loop ({no_idle or 'all'})")
check(not [f for f in FAILS if "draws" in f or "determin" in f], "...each draws, is gone at g=0, and is deterministic")
check(same(fx_img("vein", 0.4, 1.0, animated=False), fx_img("vein", 1.3, 1.0, animated=False)),
      "idle speed 0 = no idle (still)")

# the vein: four thick bent strokes like the reference -- empty middle, ink in all four quadrants
v = fx_img("vein", 0.0, 1.0)
quads = [sum(1 for yy in range(y0, y0 + 110, 3) for xx in range(x0, x0 + 110, 3) if v.pixelColor(xx, yy).alpha() > 30)
         for x0, y0 in ((0, 0), (110, 0), (0, 110), (110, 110))]
check(min(quads) > 30 and v.pixelColor(110, 110).alpha() == 0, f"vein: four arms around an empty middle {quads}")

# the element's effect_in / effect_out drive it in the timeline render
r = R.Renderer(project_with(TextStyle(text="", comic_effect="scribblenado", effect_size=0.5, effect_color="#ffffff",
                                      effect_in=1.0, effect_out=1.0)))
n_early, n_mid, n_late = (len(drawn(r.frame(t_, W, H))) for t_ in (0.15, 2.0, 3.92))
check(n_early < n_mid * 0.6 and n_late < n_mid * 0.8, f"effect in/out: winds up, full, winds down ({n_early}/{n_mid}/{n_late})")
r.close()

# Animate in / out styles and Idle loops
plain = R.Renderer(project_with(TextStyle(**base_st)))
for kind in [k for _l, k in comic.ANIM_IN if k]:
    r = R.Renderer(project_with(TextStyle(**base_st, anim_in=kind, anim_in_dur=0.8)))
    moving = sum(not same(r.frame(t_, W, H), plain.frame(t_, W, H)) for t_ in (0.05, 0.2, 0.4, 0.6))
    check(moving >= 3 and same(r.frame(1.5, W, H), plain.frame(1.5, W, H)),
          f"Animate in '{kind}': animates, then sits exactly in place")
    r.close()
for kind in [k for _l, k in comic.ANIM_OUT if k]:
    r = R.Renderer(project_with(TextStyle(**base_st, anim_out=kind, anim_out_dur=0.8)))
    moving = sum(not same(r.frame(t_, W, H), plain.frame(t_, W, H)) for t_ in (3.4, 3.6, 3.8))
    gone = len(drawn(r.frame(3.995, W, H))) < len(drawn(plain.frame(3.995, W, H))) * 0.5
    check(moving >= 2 and same(r.frame(1.5, W, H), plain.frame(1.5, W, H)) and gone,
          f"Animate out '{kind}': in place until it leaves, then (nearly) gone")
    r.close()
for kind in [k for _l, k in comic.IDLES if k]:
    r = R.Renderer(project_with(TextStyle(**base_st, idle=kind)))
    frames = [r.frame(t_, W, H) for t_ in (1.0, 1.23, 1.61)]
    check(not same(frames[0], frames[1]) or not same(frames[1], frames[2]), f"Idle '{kind}' keeps it moving")
    r.close()
r = R.Renderer(project_with(TextStyle(**base_st, idle="pulse", idle_amount=0.0)))
check(same(r.frame(1.0, W, H), r.frame(1.3, W, H)), "idle strength 0 = no idle")
r.close()

# lettering: gradient / 3D / slant / jumble all change the look; backdrop draws behind
for field_, value, *more in (("fill2", "#ff0000"), ("extrude", 20.0, "#ff0000"), ("skew", -20.0), ("jumble", 0.8),
                             ("backdrop", "burst"), ("backdrop", "splat"), ("backdrop", "flash")):
    extra = {"extrude_color": more[0]} if more else {}
    r = R.Renderer(project_with(TextStyle(**base_st, **{field_: value}, **extra)))
    check(not same(r.frame(1.0, W, H), plain.frame(1.0, W, H)), f"lettering: {field_}={value} changes the look")
    r.close()
r = R.Renderer(project_with(TextStyle(**base_st, backdrop="burst", backdrop_fill="#00ff00")))
img = r.frame(1.0, W, H)
greens = sum(1 for (x, y) in drawn(img) if img.pixelColor(x, y).green() > 200 and img.pixelColor(x, y).red() < 80)
check(greens > 300, f"the starburst backdrop is drawn behind the words in its fill ({greens} px)")
r.close()
# per-letter: the backdrop builds up with letters popping in, and goes with an explode
r = R.Renderer(project_with(TextStyle(**base_st, backdrop="burst", backdrop_fill="#00ff00", anim_in="letters",
                                      anim_in_dur=1.0, anim_out="explode", anim_out_dur=1.0)))
g_at = lambda t_: sum(1 for (x, y) in drawn(r.frame(t_, W, H)) if r.frame(t_, W, H).pixelColor(x, y).green() > 200)   # noqa: E731
first, settled, last = g_at(0.03), g_at(2.0), g_at(3.97)
check(first < settled * 0.3 and last < settled * 0.3, f"backdrop pops in with the letters and out with the explosion ({first}/{settled}/{last})")
r.close()
plain.close()

# onomatopoeia presets: each renders with its word and animates in
for name in ("POW!", "BOOM!", "ZAP!", "WHOOSH"):
    seg = ctl.add_text(name)
    st_ = ctl.project.find_segment(seg.id)[1].parts[0].text
    check(st_.text == name and st_.font_family == "Bangers" and (st_.anim_in or st_.bounce_in),
          f"onomatopoeia preset {name}: comic font, its own entrance")

# Properties: the new controls edit the element
seg = ctl.add_text("Plain text")
app.processEvents()
S = lambda: ctl.project.find_segment(seg.id)[1].parts[0].text   # noqa: E731
f = props._fields
f["anim_in"].setCurrentIndex(f["anim_in"].findData("slam"))
f["anim_out"].setCurrentIndex(f["anim_out"].findData("explode"))
f["idle"].setCurrentIndex(f["idle"].findData("wave"))
app.processEvents()
f["idle_amount"].setValue(150)
props._end_edit()
f["extrude"].setValue(12)
props._end_edit()
f["jumble"].setValue(40)
props._end_edit()
f["backdrop"].setCurrentIndex(f["backdrop"].findData("boom"))
app.processEvents()
check((S().anim_in, S().anim_out, S().idle, S().idle_amount, S().extrude, S().jumble, S().backdrop)
      == ("slam", "explode", "wave", 1.5, 12.0, 0.4, "boom"), "Properties: Animate in/out, Idle, 3D, Jumble, Backdrop")
i = f["comic_effect"].findData("thumbs_up")
f["comic_effect"].setCurrentIndex(i)
app.processEvents()
f = props._fields
f["effect_in"].setValue(0.6)
props._end_edit()
f["effect_out"].setValue(0.4)
props._end_edit()
check((S().comic_effect, S().effect_in, S().effect_out) == ("thumbs_up", 0.6, 0.4), "Properties: effect in/out lengths")

# =========================================================================
# round 16 feedback
# =========================================================================
check(not any(k in comic.EFFECTS for k in ("speed_lines", "gloom", "impact")), "speed lines / gloom / impact removed")
from afterglow.gui.advanced_editor.controller import SFX_PRESETS, TEXT_PRESETS as TP
check("OOF" not in SFX_PRESETS and "BRUH" not in SFX_PRESETS and "OOF" not in TP, "OOF / BRUH removed")
check(R.text_layout(TextStyle(text="", comic_effect="gloom"), H)["effect"] == "", "an old project's removed effect just isn't drawn")


def ink_rows(img):
    ys = [yy for yy in range(img.height()) for xx in range(0, img.width(), 4) if img.pixelColor(xx, yy).alpha() > 30]
    return (min(ys), max(ys)) if ys else (None, None)


top_half, bot_half = ink_rows(fx_img("scribblenado", 0.0, 0.3))
_, bot_full = ink_rows(fx_img("scribblenado", 0.0, 1.0))
check(top_half is not None and top_half > 60 and abs(bot_half - bot_full) < 25,
      f"scribblenado grows from the bottom (partial ink rows {top_half}-{bot_half}, full bottom {bot_full})")


def mirror_lr_score(img):
    """How left-right symmetric the 4-fold vein is when NOT rotated: compare with its 90-degree turn."""
    from PySide6.QtGui import QTransform
    rot = img.transformed(QTransform().rotate(90))
    return sum(1 for yy in range(0, 220, 4) for xx in range(0, 220, 4)
               if (img.pixelColor(xx, yy).alpha() > 30) != (rot.pixelColor(xx, yy).alpha() > 30))


full_v, half_v = fx_img("vein", 0.0, 1.0), fx_img("vein", 0.0, 0.5)
from PySide6.QtGui import QTransform
scaled_full = full_v
check(inked(half_v) > 0 and mirror_lr_score(half_v) < 40, "vein pops in without rotating (still 4-fold symmetric mid-pop)")
ex = fx_img("exclaim", 0.0, 1.0)
cols = [xx for xx in range(0, 220, 2) if any(ex.pixelColor(xx, yy).alpha() > 30 for yy in range(0, 220, 2))]
check(max(cols) - min(cols) < 220 * 0.4, f"! has no side lines (ink {max(cols) - min(cols)} px wide)")
check(not same(fx_img("interrobang", 0.30, 1.0), fx_img("interrobang", 0.42, 1.0)), "!? wobbles fast")
for v in ("separate", "covered", "plain"):
    img = QImage(220, 220, QImage.Format_ARGB32)
    img.fill(0)
    pp = QPainter(img)
    comic.draw_comic_effect(pp, "heartbeat", QRectF(40, 20, 140, 180), 0.5, QColor("#ff0000"), True, 1.0, False,
                            variant=v, color2="#00ff00")
    pp.end()
    reds = sum(1 for yy in range(0, 220, 2) for xx in range(0, 220, 2) if img.pixelColor(xx, yy).red() > 200
               and img.pixelColor(xx, yy).green() < 60)
    greens = sum(1 for yy in range(0, 220, 2) for xx in range(0, 220, 2) if img.pixelColor(xx, yy).green() > 200
                 and img.pixelColor(xx, yy).red() < 60)
    want = {"separate": (reds > 100 and greens > 30), "covered": (reds < 5 and greens > 100), "plain": (reds > 100 and greens < 5)}[v]
    check(want, f"beating heart '{v}': heart color / shirt color where they belong (red {reds}, shirt {greens})")
up, down = fx_img("thumbs_up", 0.0, 1.0), fx_img("thumbs_down", 0.0, 1.0)
check(up.mirrored(False, True).constBits().tobytes() == down.constBits().tobytes() or
      sum(1 for yy in range(0, 220, 3) for xx in range(0, 220, 3)
          if (up.mirrored(False, True).pixelColor(xx, yy).alpha() > 30) != (down.pixelColor(xx, yy).alpha() > 30)) < 40,
      "thumbs down is the thumbs up mirrored top-to-bottom")
whites = sum(1 for yy in range(0, 220, 3) for xx in range(0, 220, 3) if up.pixelColor(xx, yy).lightness() > 240
             and up.pixelColor(xx, yy).alpha() > 200)
check(whites > 300, "thumbs: white glove by default")
for name in ("steam", "blush"):
    imgs = []
    for side in ("", "left"):
        img = QImage(440, 220, QImage.Format_ARGB32)
        img.fill(0)
        pp = QPainter(img)
        hh = 100
        ww = hh * comic.effect_aspect(name, side)
        comic.draw_comic_effect(pp, name, QRectF(220 - ww / 2, 60, ww, hh), 0.6, QColor(comic.DEFAULT_EFFECT_COLOR[name]),
                                True, 1.0, False, side=side)
        pp.end()
        imgs.append(img)
    a_, b_ = inked(imgs[0]), inked(imgs[1])
    check(0 < b_ < a_ * 0.75, f"{name}: Side = left draws one side only ({b_} vs {a_})")

# Split into left + right: two elements, each where its half was, keyframes shifted, own timing
p2 = project_with(TextStyle(text="", comic_effect="steam", effect_size=0.3), x=0.1, y=-0.2)
seg0 = next(iter(p2.all_segments()))
seg0.keyframes["x"] = [model_kf(0.0, 0.1), model_kf(2.0, 0.3)] if False else []
from afterglow.nle.model import Keyframe
seg0.keyframes["x"] = [Keyframe(0.0, 0.1), Keyframe(2.0, 0.3)]
pair = R.Renderer(p2).frame(1.0, W, H)
left, right = ops.split_pair(p2, seg0.id)
check(left.parts[0].text.effect_side == "left" and right.parts[0].text.effect_side == "right" and left.id != right.id
      and left.transform.x < 0.1 < right.transform.x and abs((left.transform.x + right.transform.x) / 2 - 0.1) < 1e-9,
      "split: a left element and a right element, either side of where the pair was")
check(abs(left.keyframes["x"][1].value - (0.3 - (0.1 - left.transform.x))) < 1e-9 and
      abs(right.keyframes["x"][0].value - right.transform.x) < 1e-9, "…their X keyframes shifted with them")
check(p2.find_segment(left.id)[0] is not p2.find_segment(right.id)[0], "…on separate tracks, each its own element")
split_img = R.Renderer(p2).frame(1.0, W, H)
diff_px = sum(1 for yy in range(0, H, 3) for xx in range(0, W, 3) if pair.pixelColor(xx, yy) != split_img.pixelColor(xx, yy))
check(diff_px < 200, f"…and together they look like the pair did ({diff_px} sampled px differ)")
right.keyframes["y"] = [Keyframe(0.0, -0.2), Keyframe(2.0, 0.2)]
check(left.keyframes.get("y") in (None, []) and right.keyframes["y"][1].value == 0.2, "each side keyframes on its own")
check(ops.split_pair(p2, left.id) == [], "an already-split side isn't split again")
seg = ctl.add_text("Blush")
app.processEvents()
f = props._fields
check("split_pair_btn" in f and "effect_side" in f, "Properties: Side + Split for paired effects")
f["split_pair_btn"].click()
app.processEvents()
sides = sorted(ctl.project.find_segment(i)[1].parts[0].text.effect_side for i in ctl.selection)
check(sides == ["left", "right"], f"the Split button makes the two elements and selects them ({sides})")
ctl.undo()
check(ctl.project.find_segment(seg.id)[1].parts[0].text.effect_side == "", "…undoable")
seg = ctl.add_text("Beating heart")
app.processEvents()
f = props._fields
check("effect_variant" in f and "effect_color2_btn" in f, "Properties: beating heart Style + Shirt color")
f["effect_variant"].setCurrentIndex(f["effect_variant"].findData("covered"))
app.processEvents()
check(ctl.project.find_segment(seg.id)[1].parts[0].text.effect_variant == "covered", "…Style edits the element")

print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
