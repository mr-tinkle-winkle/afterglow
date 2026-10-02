"""
Prototype (references only): the clapping poses, built in 3D so the clasp is physically coherent.

The clasp follows the reference photo: the palms press together in one plane (seen so that the
left palm and the back of the right hand face the camera), the hands cross in that plane; then
every finger and thumb is CLOSED joint by joint until it touches the other hand -- so the right
fingers lie across the left palm and curl round its edge into the gap under the left thumb, the
left thumb comes down over them, the left fingers wrap round the right hand's pinky edge, and the
right thumb lies across the left heel and hooks round it.  Nothing can pass through anything.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from g3d import (FINGERS, PALM_CY, PALM_HX, PALM_HY, PALM_RHO, PALM_T, THUMB_R, THUMB_ROOT_R,
                 HandPose, glove_local, glove_prims, nrm, render, sd_round_rect)

# ------------------------------------------------------------------ geometry helpers


def world_chains(pose: HandPose):
    """name -> (world joints (list of 3-vectors), radius (world))."""
    M = pose.frame()
    O = np.asarray(pose.O, float)
    out = {}
    for name, (pts, zs, r) in glove_local(pose).items():
        out[name] = ([O + pose.s * (M @ p) for p in pts], r * pose.s)
    return out


def palm_dist(P, pose: HandPose):
    """Distance from points P (N, 3) to a glove's palm slab (negative inside)."""
    M = pose.frame()
    c = np.asarray(pose.O, float) + pose.s * (M @ np.array([0.0, PALM_CY, 0.0]))
    q = ((P - c) @ M) / pose.s
    d2 = sd_round_rect(q[:, 0], q[:, 1], PALM_HX, PALM_HY, PALM_RHO)
    return (np.sqrt(np.maximum(d2, 0.0) ** 2 + q[:, 2] ** 2) - PALM_T) * pose.s


def seg_dist(P, A, B):
    AB = B - A
    L2 = float(AB @ AB)
    t = np.clip(((P - A) @ AB) / max(L2, 1e-12), 0.0, 1.0)
    return np.linalg.norm(P - (A + t[:, None] * AB), axis=1)


def samples(pts, per=7):
    out = []
    for a, b in zip(pts[:-1], pts[1:]):
        for i in range(per):
            out.append(a + (b - a) * (i / (per - 1)))
    return np.array(out)


def obstacle_dist(P, other: HandPose, skip=()):
    """Distance from P to the other glove's surface (palm, fingers, thumb)."""
    d = palm_dist(P, other)
    for name, (pts, r) in world_chains(other).items():
        if name in skip:
            continue
        for i, (a, b) in enumerate(zip(pts[:-1], pts[1:])):
            rr = THUMB_ROOT_R * other.s if (name == "thumb" and i == 0) else r
            d = np.minimum(d, seg_dist(P, a, b) - rr)
    return d


def chain_clearances(pose, name, other, start_seg=0, skip=()):
    """Per obstacle of the other glove ('palm', 'index', ..., 'thumb', plus 'self' for a thumb against
    its own palm): the smallest clearance of the chain's segments from start_seg on."""
    pts, r = world_chains(pose)[name]
    P = samples(pts[start_seg:]) if len(pts[start_seg:]) > 1 else np.array(pts[-1:])
    out = {"palm": float(np.min(palm_dist(P, other) - r))}
    for oname, (opts, orad) in world_chains(other).items():
        if oname in skip:
            continue
        d = np.full(len(P), np.inf)
        for j, (a, b) in enumerate(zip(opts[:-1], opts[1:])):
            rr = THUMB_ROOT_R * other.s if (oname == "thumb" and j == 0) else orad
            d = np.minimum(d, seg_dist(P, a, b) - rr)
        out[oname] = float(np.min(d - r))
    if name == "thumb" and len(pts) > 2:
        Q = samples(pts[2:]) if len(pts) > 3 else np.array(pts[-1:])
        out["self"] = float(np.min(palm_dist(Q, pose) - r)) + 0.02 * pose.s
    return out


def blocked(now, base, s, palm_allow=0.022, slack=0.004):
    """Would this configuration push further into the other glove than allowed?  Pressing into a palm is
    allowed a little (soft gloves); an overlap with a finger that was already there may stay, not grow."""
    for key, c in now.items():
        if key == "palm":
            if c < -palm_allow * s:
                return True
        else:
            if c < min(base.get(key, 0.0), 0.0) - slack * s:
                return True
    return False


def chain_clearance(pose, name, other, start_seg=0, skip=()):
    pts, r = world_chains(pose)[name]
    P = samples(pts[start_seg:]) if len(pts[start_seg:]) > 1 else np.array(pts[-1:])
    c = float(np.min(obstacle_dist(P, other, skip) - r))
    if name == "thumb" and len(pts) > 2:
        # ...and the thumb must not sink into its own palm (beyond its root)
        Q = samples(pts[2:]) if len(pts) > 3 else np.array(pts[-1:])
        c = min(c, float(np.min(palm_dist(Q, pose) - r)) + 0.02 * pose.s)
    return c


def _flex_of(pose, name):
    if name == "thumb":
        return list(pose.thumb[2:])
    return list(pose.fingers.get(name, (0.0, 8.0, 10.0, 8.0))[1:])


def _set_flex(pose, name, flex):
    if name == "thumb":
        pose.thumb = tuple(pose.thumb[:2]) + tuple(flex)
    else:
        f = pose.fingers.get(name, (0.0, 8.0, 10.0, 8.0))
        pose.fingers[name] = (f[0],) + tuple(flex)


def close_chain(pose, name, other, max_flex=(90.0, 100.0, 85.0), step=1.5, slack=0.004, skip=()):
    """Curl a finger / thumb joint by joint (proximal first) until it touches the other glove.
    A segment that already overlaps a little at the start is allowed to stay as it is."""
    flex = _flex_of(pose, name)
    for j in range(len(flex)):
        base = chain_clearances(pose, name, other, j, skip)
        while flex[j] < max_flex[j]:
            trial = list(flex)
            trial[j] += step
            _set_flex(pose, name, trial)
            if blocked(chain_clearances(pose, name, other, j, skip), base, pose.s):
                _set_flex(pose, name, flex)
                break
            flex = trial
    _set_flex(pose, name, flex)
    return flex


def close_coupled(pose, name, other, weights, max_flex, step=0.02, slack=0.004, skip=(), first=0):
    """Curl joints first.. together (flex += lam * weights) until the chain touches the other glove."""
    flex0 = _flex_of(pose, name)
    base = chain_clearances(pose, name, other, first, skip)
    lam, best = 0.0, list(flex0)
    while lam < 1.5:
        lam += step
        trial = list(flex0)
        for j in range(first, len(trial)):
            trial[j] = min(max_flex[j], flex0[j] + lam * weights[j] * 100.0)
        _set_flex(pose, name, trial)
        if blocked(chain_clearances(pose, name, other, first, skip), base, pose.s):
            break
        best = trial
        if all(trial[j] >= max_flex[j] for j in range(first, len(trial))):
            break
    _set_flex(pose, name, best)
    return best


def settle_thumb(pose, other, lift_from, lift_to, step=1.5, skip=()):
    """Bring a lifted thumb down (its lift angle from lift_from toward lift_to) until it touches the
    other glove, starting from a pose that clears it."""
    beta = pose.thumb[0]
    rest = tuple(pose.thumb[2:])
    lift = lift_from
    pose.thumb = (beta, lift) + rest
    while chain_clearance(pose, "thumb", other, 1, skip) < 0 and abs(lift) < 89:
        lift += 3.0 if lift_from >= lift_to else -3.0          # start clear
        pose.thumb = (beta, lift) + rest
    direction = -1.0 if lift_from >= lift_to else 1.0
    while (lift - lift_to) * (-direction) > 0:
        trial = lift + direction * step
        pose.thumb = (beta, trial) + rest
        if chain_clearance(pose, "thumb", other, 1, skip) < -0.003 * pose.s:
            pose.thumb = (beta, lift) + rest
            break
        lift = trial
    pose.thumb = (beta, lift) + rest
    return lift


def settle_scan(pose, other, start, stop, step=2.0, tol=0.004, skip=()):
    """Scan the thumb's lift from ``start`` toward ``stop``: find where it first clears the other glove,
    then keep going while it stays clear -- it comes to rest on whatever is below."""
    beta, rest = pose.thumb[0], tuple(pose.thumb[2:])
    n = int(abs(stop - start) / step) + 1
    lifts = [start + (stop - start) * i / (n - 1) for i in range(n)]
    clr = []
    for lf in lifts:
        pose.thumb = (beta, lf) + rest
        clr.append(chain_clearance(pose, "thumb", other, 1, skip))
    ok = [c >= -tol * pose.s for c in clr]
    if any(ok):
        i = ok.index(True)
        while i + 1 < len(ok) and ok[i + 1]:
            i += 1
    else:
        i = int(np.argmax(clr))
    pose.thumb = (beta, lifts[i]) + rest
    return lifts[i], clr[i]


def wrap_finger(pose, name, other, max_flex=(90.0, 100.0, 85.0), skip=()):
    """A finger lying on the other hand: the knuckle closes until it lies flat on it, then the two
    outer joints curl together round whatever is there."""
    flex = _flex_of(pose, name)
    # knuckle: proximal-first closing of joint 0 only
    base = chain_clearances(pose, name, other, 0, skip)
    while flex[0] < max_flex[0]:
        trial = list(flex)
        trial[0] += 1.5
        _set_flex(pose, name, trial)
        if blocked(chain_clearances(pose, name, other, 0, skip), base, pose.s):
            _set_flex(pose, name, flex)
            break
        flex = trial
    _set_flex(pose, name, flex)
    return close_coupled(pose, name, other, (0.0, 1.0, 0.75), max_flex, skip=skip, first=1)


# ------------------------------------------------------------------ poses


def _plane(phi_deg):
    """The clasp plane: n = the left palm's normal (toward the right hand and the camera); u = up;
    h = the in-plane horizontal (screen right, a little away from the camera)."""
    phi = math.radians(phi_deg)
    n = np.array([math.cos(phi), 0.0, math.sin(phi)])
    u = np.array([0.0, 1.0, 0.0])
    h = np.cross(u, n)
    return n, u, h


def clasp_pose(phi=48.0, cross=46.0, s=0.55, shift=(-0.19, 0.52), cross_left=None, cross_right=None, lift_left_thumb=48.0, relax=0.0,
               right_fingers_spread=(0.0, 0.0, 0.0), left_fingers_spread=(5.0, 0.0, -7.0),
               left_finger_bend=(6.0, 24.0, 20.0), right_thumb=(50.0, 8.0), left_thumb_out=34.0, left_wrap=False):
    """Both gloves clasped.  ``shift`` moves the right hand in the plane (along u, h) relative to the
    left; ``relax`` opens every closed joint by that many degrees (the resting clasp)."""
    n, u, h = _plane(phi)
    thl = math.radians(cross if cross_left is None else cross_left)
    thr = math.radians(cross if cross_right is None else cross_right)
    YL = math.cos(thl) * u + math.sin(thl) * h
    YR = math.cos(thr) * u - math.sin(thr) * h
    # the palms touch: their front faces lie in the plane through the origin
    left = HandPose("left", O=(0, 0, 0), Y=YL, Z=n, s=s,
                    fingers={"index": (left_fingers_spread[0], 0, 4, 4), "middle": (left_fingers_spread[1], 0, 4, 4),
                             "pinky": (left_fingers_spread[2], 0, 4, 4)},
                    thumb=(left_thumb_out, lift_left_thumb, 0.0, 4.0, 4.0))
    right = HandPose("right", O=(0, 0, 0), Y=YR, Z=-n, s=s,
                     fingers={"index": (right_fingers_spread[0], 0, 4, 4), "middle": (right_fingers_spread[1], 0, 4, 4),
                              "pinky": (right_fingers_spread[2], 0, 4, 4)},
                     thumb=(right_thumb[0], right_thumb[1], 0.0, 4.0, 4.0))
    # place the palm centres: left palm centre a little behind the plane, right in front of it
    ML, MR = left.frame(), right.frame()
    cL = -n * PALM_T * s
    cR = n * PALM_T * s + u * shift[0] * s + h * shift[1] * s
    left.O = tuple(cL - s * (ML @ np.array([0.0, PALM_CY, 0.0])))
    right.O = tuple(cR - s * (MR @ np.array([0.0, PALM_CY, 0.0])))
    # close everything onto the other hand
    left.thumb = (left.thumb[0], 85.0) + tuple(left.thumb[2:])   # the left thumb held up out of the way...
    for name in ("index", "middle", "pinky"):
        wrap_finger(right, name, left)                            # ...while the right fingers wrap round the left hand
    for name in ("index", "middle", "pinky"):          # a natural bend, less only if it would go into the other hand
        if left_wrap:
            wrap_finger(left, name, right)
        else:
            close_chain(left, name, right, max_flex=left_finger_bend)
    # the left thumb comes down over the right fingers, then curls round them
    settle_scan(left, right, 85.0, -5.0, skip=("thumb",))
    close_coupled(left, "thumb", right, (0.2, 1.0, 0.9), (25.0, 75.0, 70.0), first=1, skip=("thumb",))
    # the right thumb lies on the left hand (lifted just clear of it), then curls round its edge
    settle_scan(right, left, -85.0, 40.0)
    close_coupled(right, "thumb", left, (0.2, 1.0, 0.9), (25.0, 75.0, 70.0), first=1)
    if relax:
        for hand in (left, right):
            for name in ("index", "middle", "pinky", "thumb"):
                fl = _flex_of(hand, name)
                _set_flex(hand, name, [max(0.0, f - relax) for f in fl])
    return left, right


def ready_pose(phi=48.0, s=0.53, apart=0.42, lean=12.0, thumb_out=60.0, thumb_lift=22.0, thumb_bend=(0.0, 14.0, 12.0),
               finger_bend=(8.0, 12.0, 8.0)):
    """Hands apart, palms facing each other across the gap (the clap brings them together along n)."""
    n, u, h = _plane(phi)
    th = math.radians(lean)
    YL = math.cos(th) * u + math.sin(th) * h
    YR = math.cos(th) * u - math.sin(th) * h
    fb = finger_bend
    left = HandPose("left", O=(0, 0, 0), Y=YL, Z=n, s=s,
                    fingers={"index": (4.0,) + fb, "middle": (0.0,) + fb, "pinky": (-5.0,) + fb},
                    thumb=(thumb_out, thumb_lift) + tuple(thumb_bend))
    right = HandPose("right", O=(0, 0, 0), Y=YR, Z=-n, s=s,
                     fingers={"index": (4.0,) + fb, "middle": (0.0,) + fb, "pinky": (-5.0,) + fb},
                     thumb=(thumb_out, thumb_lift) + tuple(thumb_bend))
    ML, MR = left.frame(), right.frame()
    cL = -n * apart * s
    cR = n * apart * s
    left.O = tuple(cL - s * (ML @ np.array([0.0, PALM_CY, 0.0])))
    right.O = tuple(cR - s * (MR @ np.array([0.0, PALM_CY, 0.0])))
    return left, right


# ------------------------------------------------------------------ framing + rendering


def prim_bounds(prims):
    lo = np.array([np.inf, np.inf])
    hi = -lo
    for pr in prims:
        pts, r = [], 0.0
        if pr["k"] == "sph":
            pts, r = [pr["C"]], pr["r"]
        elif pr["k"] in ("cyl",):
            pts, r = [pr["A"], pr["B"]], pr["r"]
        elif pr["k"] == "cone":
            pts, r = [pr["A"], pr["B"]], max(pr["rA"], pr["rB"])
        elif pr["k"] == "ecyl":
            pts = [pr["T"] + pr["a"] * t + pr["ex"] * pr["rx"] * cx + pr["ez"] * pr["rz"] * cz
                   for t in (0, pr["L"]) for cx in (-1, 1) for cz in (-1, 1)]
        else:
            continue
        for p in pts:
            lo = np.minimum(lo, p[:2] - r)
            hi = np.maximum(hi, p[:2] + r)
    return lo, hi


def place(hands, centre=(0.0, 0.0)):
    """Translate both hands so the pair's 2D bounds are centred on ``centre`` (the item's pivot)."""
    prims = [p for i, hp in enumerate(hands) for p in glove_prims(hp, i)]
    lo, hi = prim_bounds(prims)
    c = (lo + hi) / 2
    d = np.array([centre[0] - c[0], centre[1] - c[1], 0.0])
    out = []
    for hp in hands:
        q = copy.deepcopy(hp)
        q.O = tuple(np.asarray(hp.O, float) + d)
        out.append(q)
    return out, (hi - lo)


def render_box(hands, Wbox, margin=0.35, icon=None, ss=3, front_index=1):
    """Render the hands for an item box Wbox px wide (world: the box is 1 wide, 0.86 tall, centred)."""
    Hbox = Wbox * 0.86
    Wpx = int(round(Wbox * (1 + 2 * margin)))
    Hpx = int(round(Hbox + 2 * margin * Wbox))
    x0 = -0.5 - margin
    y0 = 0.43 + margin
    prims = [glove_prims(hp, i, icon=(i == front_index and icon is not None)) for i, hp in enumerate(hands)]
    arr = render(prims, Wpx, Hpx, x0, y0, Wbox, ss=ss, size=hands[0].s, icon=icon)
    return arr, (margin * Wbox, margin * Wbox)


def fit_scale(builder, target=(0.86, 0.84), **kw):
    """Build a pose at size 0.55, measure it, and rebuild at the size that fills the item box."""
    hands = builder(s=0.55, **kw)
    _, size = place(list(hands))
    k = min(target[0] / size[0], target[1] / size[1])
    return builder(s=0.55 * k, **kw)


# ------------------------------------------------------------------ readability


def visibility(hands, W=140):
    """Visible fraction of each (glove, part) from the camera: rendered together vs alone."""
    from g3d import Raster, PALM, INDEX, MIDDLE, PINKY, THUMB
    prims = [glove_prims(hp, i) for i, hp in enumerate(hands)]
    lo, hi = prim_bounds([p for ps in prims for p in ps])
    pad = 0.05
    k = W / max(hi[0] - lo[0] + 2 * pad, hi[1] - lo[1] + 2 * pad)
    Wp = int((hi[0] - lo[0] + 2 * pad) * k) + 1
    Hp = int((hi[1] - lo[1] + 2 * pad) * k) + 1

    def counts(ps):
        R = Raster(Wp, Hp, lo[0] - pad, hi[1] + pad, k)
        R.draw(ps)
        v = np.isfinite(R.z)
        out = {}
        for g in (0, 1):
            for part, name in ((PALM, "palm"), (INDEX, "fingers"), (MIDDLE, "fingers"), (PINKY, "fingers"), (THUMB, "thumb")):
                out[(g, name)] = out.get((g, name), 0) + int(((R.g == g) & (R.p == part) & v).sum())
        return out
    both = counts([p for ps in prims for p in ps])
    alone = {}
    for g in (0, 1):
        c = counts(prims[g])
        for key, val in c.items():
            if key[0] == g:
                alone[key] = val
    return {key: (both[key] / alone[key] if alone[key] else 0.0) for key in alone}


def clearance_report(pose, name, other, start_seg=0):
    """Per segment of a chain: the nearest part of the other glove and the clearance (x size)."""
    pts, r = world_chains(pose)[name]
    rows = []
    for i in range(start_seg, len(pts) - 1):
        P = samples([pts[i], pts[i + 1]])
        best = ("palm", float(np.min(palm_dist(P, other) - r)))
        for oname, (opts, orad) in world_chains(other).items():
            for j, (a, b) in enumerate(zip(opts[:-1], opts[1:])):
                rr = THUMB_ROOT_R * other.s if (oname == "thumb" and j == 0) else orad
                c = float(np.min(seg_dist(P, a, b) - rr - r))
                if c < best[1]:
                    best = (f"{oname}[{j}]", c)
        rows.append((i, best[0], round(best[1] / pose.s, 3)))
    return rows
