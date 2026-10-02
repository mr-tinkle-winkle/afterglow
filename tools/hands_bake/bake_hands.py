"""Bake the Hands clip-indicator animation: the 3D gloves (posed and animated in 3D, so they interlock
without clipping) are rendered, traced into 2D vector shapes frame by frame, and written as one
compact JSON file the app draws at runtime in either look (retro / cel) with the user's colours.

usage: bake_hands.py out.json.gz [--quick]
Sequences: "ready" (2 s idle loop, 30 fps), "clap" (from the clap event to the settled clasp, 60 fps),
"rest" (2 s idle loop of the clasp, 30 fps).  All coordinates are in item-box units (fractions of the
item's width, origin at its top-left; the box is 1 x 0.86)."""
import os, sys, json, math, copy, gzip, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import cv2
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
BOX = int(os.environ.get("BOX", 300))
SS = int(os.environ.get("SS", 3))
MARGIN = 0.30
Q = 4000.0                       # quantisation: box units * Q -> int

# ------------------------------------------------------------------ poses (lazy: built in each worker)
_P = {}


def ease_io(t): return t * t * (3 - 2 * t)
def ease_in(t): return t * t
def ease_out(t): return 1 - (1 - t) * (1 - t)


def setup():
    if _P:
        return _P
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from sheetlib import build, row_scale, bounds
    from design import rx, ry
    spec = json.load(open(os.path.join(HERE, "hands_spec.json")))
    ready = build(spec["ready"]); wind = build(spec["wind"])
    closed = build(spec["impact"]); rest = build(dict(spec["impact"], relax_fingers=5, relax_thumbs=4))
    cam_ready, cam_imp = spec["cam_ready"], spec["cam_impact"]
    R25, R40 = ry(cam_ready), ry(cam_imp)

    def translate(hands, d):
        out = []
        for hp in hands:
            q = copy.deepcopy(hp); q.O = tuple(np.asarray(hp.O, float) + np.asarray(d, float))
            q._clasped = getattr(hp, "_clasped", False); out.append(q)
        return out

    def centre_shift(hands, Rv):
        lo, hi = bounds(hands, Rv); c2 = (lo + hi) / 2
        return Rv.T @ np.array([-c2[0], -c2[1], 0.0])
    d_ready = centre_shift(ready, R25)
    ready = translate(ready, d_ready); wind = translate(wind, d_ready)
    d_imp = centre_shift(closed, R40)
    closed = translate(closed, d_imp); rest = translate(rest, d_imp)
    k = min(row_scale([ready], R25, 0.94, 0.84), row_scale([closed], R40, 0.94, 0.84))

    def opened(hands, lt_open, rt_open):
        out = []
        for gi, hp in enumerate(hands):
            q = copy.deepcopy(hp)
            q.fingers = {n: (v[0], 0.0, 4.0, 4.0) for n, v in hp.fingers.items()}
            q.thumb = tuple(lt_open if gi == 0 else rt_open); q._clasped = True; out.append(q)
        return out
    contact = opened(closed, spec["lt_open"], spec["rt_open"])
    gap = spec["gap"]
    pre = translate(contact[:1], (0, 0, -gap * closed[0].s)) + translate(contact[1:], (0, 0, gap * closed[0].s))
    for q in pre:
        q._clasped = True
    _P.update(ready=ready, wind=wind, closed=closed, rest=rest, contact=contact, pre=pre, k=k,
              cam_ready=cam_ready, cam_imp=cam_imp, T=spec["timeline"], ry=ry, bounds=bounds)
    return _P


WOB = [(0.0, 1.0), (1.3, 0.5), (2.4, 1.5)]      # (phase, Hz): every rhythm repeats within 2 s, so the loops close


def wobble(hands, t, amp):
    out = []
    for gi, hp in enumerate(hands):
        q = copy.deepcopy(hp); q._clasped = getattr(hp, "_clasped", False)
        fl = {}
        for i, (n, v) in enumerate(hp.fingers.items()):
            ph, fr = WOB[i]
            w = amp * math.sin(2 * math.pi * fr * t + ph + gi * 0.7)
            fl[n] = (v[0], max(0.0, v[1] + 0.4 * w), max(0.0, v[2] + w), max(0.0, v[3] + 0.8 * w))
        q.fingers = fl
        th = list(hp.thumb); th[3] = max(0.0, th[3] + 0.5 * amp * math.sin(2 * math.pi * 0.5 * t + 0.4 + gi)); q.thumb = tuple(th)
        out.append(q)
    return out


def pose(seq, t):
    """(hands, camera degrees, extras) for a sequence at time t (s; for "clap": since the clap event)."""
    P = setup()
    from interp import lerp_pose
    W, S, C, CL, ST = P["T"]
    if seq == "ready":
        return wobble(P["ready"], t, 3.0), P["cam_ready"], {}
    if seq == "rest":
        return wobble(P["rest"], t, 2.0), P["cam_imp"], {}
    cam = P["cam_ready"] + (P["cam_imp"] - P["cam_ready"]) * ease_io(min(1.0, t / C))
    if t < W:
        u = ease_io(t / W)
        return [lerp_pose(a, b, u) for a, b in zip(wobble(P["ready"], t, 3.0 * (1 - u)), P["wind"])], cam, {}
    if t < S:
        u = ease_in((t - W) / (S - W))
        return [lerp_pose(a, b, u) for a, b in zip(P["wind"], P["pre"])], cam, {"streak": 1.0 - 0.3 * u}
    if t < C:
        u = (t - S) / (C - S)
        return [lerp_pose(a, b, u) for a, b in zip(P["pre"], P["contact"])], cam, {"streak": 0.7 * (1 - u)}
    if t < CL:
        u = ease_out((t - C) / (CL - C))
        return [lerp_pose(a, b, u) for a, b in zip(P["contact"], P["closed"])], cam, {"squash": 1 - u}
    u = ease_io(min(1.0, (t - CL) / (ST - CL)))
    hands = [lerp_pose(a, b, u) for a, b in zip(P["closed"], P["rest"])]
    return wobble(hands, t, 2.0 * u), cam, {}


def to_box(hands, k):
    out = []
    for hp in hands:
        q = copy.deepcopy(hp); q.O = tuple(np.asarray(hp.O, float) * k); q.s = hp.s * k
        q._clasped = getattr(hp, "_clasped", False); out.append(q)
    return out

# ------------------------------------------------------------------ one frame -> vector data


def qpts(c):
    return [int(round(v * Q)) for xy in c for v in xy]


def to_units(c):
    return np.asarray(c, float) / (SS * BOX) - MARGIN


def simplify(c, eps=0.7, closed=True):
    if len(c) < 4:
        return np.asarray(c, float)
    a = cv2.approxPolyDP(np.asarray(c, np.float32).reshape(-1, 1, 2), eps, closed)[:, 0, :]
    return a.astype(float)


def bake_frame(job):
    seq, i, t = job
    P = setup()
    import g3d
    from g3d import glove_prims, render, transform_prims, PALM, PALM_T, PALM_CY
    from trace2d import trace_frame, contours, blob
    from interp import palm_centre
    hands, cam, ex = pose(seq, t)
    hb = to_box(hands, P["k"])
    Rv = P["ry"](cam)
    prims = [transform_prims(glove_prims(hp, gi), Rv) for gi, hp in enumerate(hb)]
    Wpx = int(round(BOX * (1 + 2 * MARGIN))); Hpx = int(round(BOX * (0.86 + 2 * MARGIN)))
    g3d.CAPTURE = {}
    render(prims, Wpx, Hpx, -0.5 - MARGIN, 0.43 + MARGIN, BOX, ss=SS, size=hb[0].s, light=(-0.55, 0.55, 0.63))
    C = {f"f_{k_}": v for k_, v in g3d.CAPTURE.items()}
    g3d.CAPTURE = None
    tr = trace_frame(C, "f")
    out = {}
    out["sil"] = [qpts(to_units(simplify(c, 0.6))) for c in tr["silhouette"]]
    fills = {}
    for (gl, name), cs in tr["regions"].items():
        key = name
        lst = fills.setdefault(key, [])
        for c in cs:
            s_ = simplify(c, 0.7)
            if len(s_) >= 3:
                lst.append(qpts(to_units(s_)))
    # the stitching on the back of each glove (same pattern the 3D renderer paints)
    part, face, uv, valid = C["f_p"], C["f_face"], C["f_uv"], C["f_valid"]
    back = valid & (part == PALM) & (face == 2)
    u, v = uv[..., 0], uv[..., 1]
    m = np.zeros(back.shape, bool)
    for sx in (0.10, -0.02, -0.14):
        cx = sx + 0.02 * np.sin((v - 0.46) / 0.10 * math.pi)
        m |= (np.abs(u - cx) < 0.013) & (v > 0.46) & (v < 0.56)
    m &= back
    fills["stitch"] = [qpts(to_units(simplify(c, 0.5))) for c in contours(m, sigma=1.5, min_area=20, step=1)]
    out["fills"] = {k_: v_ for k_, v_ in fills.items() if v_}
    lines = []
    for xy, w in tr["lines"]:
        idx = list(range(0, len(xy), 2)) + ([len(xy) - 1] if (len(xy) - 1) % 2 else [])
        xy2 = to_units(xy[idx]); w2 = np.asarray(w)[idx] / (SS * BOX)
        lines.append({"p": qpts(xy2), "w": [int(round(x * Q)) for x in w2]})
    out["lines"] = lines
    out["ink"] = round(tr["ink_width"] / (SS * BOX), 5)
    # shines: the brightest lit spots (centre, radius)
    sh = []
    for c in tr["regions"].get((0, "glove_light"), []) + tr["regions"].get((1, "glove_light"), []):
        cu = to_units(c)
        ctr = cu.mean(0); r = float(np.sqrt(((cu - ctr) ** 2).sum(1)).mean())
        if r > 0.015:
            sh.append([round(float(ctr[0]), 4), round(float(ctr[1]), 4), round(r, 4)])
    out["shines"] = sh
    # the icon on the back of the right glove: a square on the back of the hand, and where it shows
    R = hb[1]; M = R.frame(); O = np.asarray(R.O, float)
    nrm_view = Rv @ (M @ np.array([0.0, 0.0, -1.0]))
    icon = None
    if nrm_view[2] > 0.12:
        half = 0.12
        def P2(lx, ly):
            w_ = Rv @ (O + R.s * (M @ np.array([lx, ly, -PALM_T])))
            return [w_[0] + 0.5, 0.43 - w_[1]]
        o = P2(half, 0.33 + half); ur = P2(-half, 0.33 + half); bl = P2(half, 0.33 - half)
        vis = valid & (C["f_g"] == 1) & (part == PALM) & (face == 2)
        clip = [qpts(to_units(simplify(c, 0.7))) for c in contours(vis, sigma=2.0, min_area=200, step=1)]
        if clip:
            icon = {"quad": [round(x, 4) for x in (o + ur + bl)], "clip": clip}
    out["icon"] = icon
    if "streak" in ex:
        pcs = []
        for hp in hb:
            c = Rv @ palm_centre(hp)
            pcs.append([round(float(c[0] + 0.5), 4), round(float(0.43 - c[1]), 4)])
        out["streak"] = {"k": round(ex["streak"], 3), "palms": pcs}
    if "squash" in ex:
        out["squash"] = round(ex["squash"], 3)
    # bounds (box units) for layout / tests
    allp = np.concatenate([to_units(c) for c in tr["silhouette"]])
    out["bounds"] = [round(float(x), 4) for x in (allp[:, 0].min(), allp[:, 1].min(), allp[:, 0].max(), allp[:, 1].max())]
    return seq, i, out


def worst_clearance(jobs):
    setup()
    from design import clearances
    worst = {}
    for seq, i, t in jobs:
        hands, _, _ = pose(seq, t)
        cl = clearances(*hands)
        w = min(v[0] for v in cl.values())
        if seq not in worst or w < worst[seq][0]:
            worst[seq] = (round(w, 3), round(t, 3), min(cl, key=lambda key: cl[key][0]))
    return worst


if __name__ == "__main__":
    quick = "--quick" in sys.argv
    spec = json.load(open(os.path.join(HERE, "hands_spec.json")))
    W, S, C, CL, ST = spec["timeline"]
    fps_loop, fps_clap, period = 30, 60, 2.0
    jobs = [("ready", i, i / fps_loop) for i in range(int(period * fps_loop))]
    n_clap = int(math.ceil(ST * fps_clap)) + 1
    jobs += [("clap", i, i / fps_clap) for i in range(n_clap)]
    jobs += [("rest", i, i / fps_loop) for i in range(int(period * fps_loop))]
    if quick:
        jobs = [j for j in jobs if j[1] % 15 == 0]
    print("clearance (worst per sequence, x glove size):", worst_clearance(jobs))
    t0 = time.time()
    with Pool(int(os.environ.get("PROCS", 2))) as pool:
        res = pool.map(bake_frame, jobs, chunksize=1)
    print("baked", len(res), "frames in", round(time.time() - t0), "s")
    data = {"version": 1, "q": Q, "aspect": 0.86,
            "timing": {"windup_ms": round(W * 1000), "contact_ms": round(C * 1000), "closed_ms": round(CL * 1000),
                       "settled_ms": round(ST * 1000)},
            "ready": {"fps": fps_loop, "frames": []}, "clap": {"fps": fps_clap, "frames": []},
            "rest": {"fps": fps_loop, "frames": []}}
    for seq, i, out in sorted(res, key=lambda r: (r[0], r[1])):
        data[seq]["frames"].append(out)
    # the shock ring sits above the settled clasp
    b = data["clap"]["frames"][-1]["bounds"]
    data["fx"] = {"ring": [round((b[0] + b[2]) / 2, 4), round(b[1] + 0.12, 4)]}
    raw = json.dumps(data, separators=(",", ":")).encode()
    with gzip.open(sys.argv[1], "wb", compresslevel=9) as f:
        f.write(raw)
    print("wrote", sys.argv[1], len(raw) // 1024, "KB raw", os.path.getsize(sys.argv[1]) // 1024, "KB gz")
