"""
Bake-time only (not imported by the app): cartoon gloves modelled in 3D and rendered with a small numpy
z-buffer in the gloves' cartoon style -- per-pixel depth decides what is in front (so hands can
interlock without clipping), outlines are drawn where the depth jumps (so a thumb flows into its
hand, and anything crossing in front gets a crisp outline), a light-space depth map casts soft
shadows, and the picture is rendered at 3x and scaled down for smooth edges.

World space: x right, y up, z toward the viewer (orthographic camera looking down -z).
Glove local space (units of the glove's size): X = thumb side, Y = toward the fingers,
Z = palm normal (out of the palm); origin at the wrist.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

PALM, INDEX, MIDDLE, PINKY, THUMB, CUFF, RIM, CAP = range(8)
FINGER_PARTS = {"index": INDEX, "middle": MIDDLE, "pinky": PINKY}


# ------------------------------------------------------------------ math

def nrm(v):
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else v


def rot(axis, deg):
    k = nrm(axis)
    th = math.radians(deg)
    K = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + math.sin(th) * K + (1.0 - math.cos(th)) * (K @ K)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def sd_round_rect(qx, qy, hx, hy, rho):
    """Signed distance to a rounded rectangle (half extents hx, hy, corner radius rho), 2D."""
    dx = np.abs(qx) - (hx - rho)
    dy = np.abs(qy) - (hy - rho)
    out = np.sqrt(np.maximum(dx, 0) ** 2 + np.maximum(dy, 0) ** 2)
    return out + np.minimum(np.maximum(dx, dy), 0) - rho


# ------------------------------------------------------------------ the glove

PALM_CY = 0.36          # palm centre (local Y)
PALM_HX, PALM_HY, PALM_RHO = 0.185, 0.235, 0.10    # inner rounded rectangle
PALM_T = 0.115          # half thickness (= the rim's tube radius)
FINGERS = (             # name, knuckle (local X, Y), radius, segment lengths
    ("index", (0.150, 0.585), 0.128, (0.185, 0.145, 0.105)),
    ("middle", (0.000, 0.605), 0.133, (0.205, 0.158, 0.115)),
    ("pinky", (-0.150, 0.575), 0.119, (0.160, 0.125, 0.090)),
)
THUMB_BASE = (0.160, 0.3305, 0.02)
THUMB_ROOT_R = 0.125
THUMB_R = 0.138
THUMB_SEGS = (0.200, 0.180, 0.150)     # metacarpal (mostly inside the thenar), proximal, distal
THUMB_CREASE_TILT = 1.2
META_LIFT_MIN = -4.0     # the thumb's metacarpal never tilts further toward the back of the hand
THENAR = dict(c=(0.130, 0.205, 0.055), r=(0.115, 0.175, 0.085), turn=-38.0)
CUFF_TOP, CUFF_LEN = 0.075, 0.24
CUFF_RX, CUFF_RZ = 0.255, 0.160
RIM_R = 0.048


@dataclass
class HandPose:
    side: str                 # "right" | "left"
    O: tuple                  # the wrist (world)
    Y: tuple                  # finger direction (world)
    Z: tuple                  # palm normal, out of the palm (world)
    s: float = 0.55           # size (world units)
    fingers: dict = field(default_factory=dict)   # name -> (spread deg, flex1, flex2, flex3)
    thumb: tuple = (52.0, 30.0, 0.0, 10.0, 10.0)   # (spread from Y toward X, lift toward Z, flex cmc, mcp, ip)
    tb_dz: float = 0.0        # the thumb's base moved toward the palm side (opposition)
    tb_dy: float = 0.0        # ...and along the palm (toward the wrist when negative)

    def frame(self):
        Yw = nrm(self.Y)
        Zw = np.asarray(self.Z, float)
        Zw = nrm(Zw - Yw * float(Zw @ Yw))
        Xw = np.cross(Yw, Zw) if self.side == "right" else np.cross(Zw, Yw)
        return np.column_stack([Xw, Yw, Zw])


def _chain(p0, d, z, segs, flex):
    """Joint positions of a finger: start at p0 along d; each flex rotates d toward z (the palm side)."""
    pts, zs = [np.asarray(p0, float)], [np.asarray(z, float)]
    d = np.asarray(d, float)
    z = np.asarray(z, float)
    for i, L in enumerate(segs):
        th = math.radians(flex[i] if i < len(flex) else 0.0)
        d, z = math.cos(th) * d + math.sin(th) * z, -math.sin(th) * d + math.cos(th) * z
        zs.append(z)
        pts.append(pts[-1] + d * L)
    return pts, zs


def glove_local(pose: HandPose):
    """Joint chains (local units) for the fingers and the thumb."""
    out = {}
    X, Y, Z = np.eye(3)
    for name, (kx, ky), r, segs in FINGERS:
        f = pose.fingers.get(name, (0.0, 8.0, 10.0, 8.0))
        d = nrm(math.cos(math.radians(f[0])) * Y + math.sin(math.radians(f[0])) * X)
        pts, zs = _chain((kx, ky, 0.0), d, Z, segs, f[1:])
        out[name] = (pts, zs, r)
    beta, alpha = pose.thumb[0], pose.thumb[1]
    dp = math.cos(math.radians(beta)) * Y + math.sin(math.radians(beta)) * X
    d0 = nrm(math.cos(math.radians(alpha)) * dp + math.sin(math.radians(alpha)) * Z)
    # the thumb curls toward the middle of the palm's surface (over whatever lies on the palm)
    base = np.asarray(THUMB_BASE, float) + np.array([0.0, pose.tb_dy, pose.tb_dz])
    T = np.array([-0.05, 0.42, 0.13]) - base
    f0 = nrm(T - d0 * float(T @ d0))
    pts, zs = _chain(base, d0, f0, THUMB_SEGS, pose.thumb[2:])
    if alpha < META_LIFT_MIN:
        # the metacarpal stays inside the hand (no lump on the back); the backward tilt starts at the knuckle
        am = math.radians(META_LIFT_MIN)
        dm = nrm(math.cos(am) * dp + math.sin(am) * Z)
        fm = nrm(T - dm * float(T @ dm))
        pm, _ = _chain(base, dm, fm, THUMB_SEGS[:1], pose.thumb[2:3])
        delta = pm[1] - pts[1]
        pts = [pts[0], pm[1]] + [q + delta for q in pts[2:]]
    out["thumb"] = (pts, zs, THUMB_R)
    return out


def glove_prims(pose: HandPose, g: int, icon: bool = False):
    """World-space primitives of one glove (dicts; see Raster.draw)."""
    M = pose.frame()
    O = np.asarray(pose.O, float)
    s = float(pose.s)

    def W(p):
        return O + s * (M @ np.asarray(p, float))

    def V(v):
        return M @ np.asarray(v, float)

    prims = []
    # palm: a rounded slab = two faces + a rim of capsules around the inner rounded rectangle
    pc = W((0.0, PALM_CY, 0.0))
    for side in (1, -1):
        prims.append(dict(k="face", C=pc + V((0, 0, side * PALM_T)) * s, N=V((0, 0, side)), Cs=pc, M=M, s=s,
                          hx=PALM_HX, hy=PALM_HY, rho=PALM_RHO, side=side, g=g, p=PALM, icon=icon))
    loop = []
    hx, hy, rho = PALM_HX - PALM_RHO, PALM_HY - PALM_RHO, PALM_RHO
    for cx, cy, a0 in ((hx, hy, 0), (-hx, hy, 90), (-hx, -hy, 180), (hx, -hy, 270)):
        for i in range(5):
            a = math.radians(a0 + 90 * i / 4)
            loop.append((cx + rho * math.cos(a), PALM_CY + cy + rho * math.sin(a), 0.0))
    for i in range(len(loop)):
        A, B = W(loop[i]), W(loop[(i + 1) % len(loop)])
        if np.linalg.norm(B - A) > 1e-9:
            prims.append(dict(k="cyl", A=A, B=B, r=PALM_T * s, g=g, p=PALM))
        prims.append(dict(k="sph", C=A, r=PALM_T * s, g=g, p=PALM))
    # the thenar: the soft pad at the thumb's root, on the palm side
    Rth = M @ rot((0, 0, 1), THENAR["turn"])
    if THENAR.get("on", False):
        prims.append(dict(k="ell", C=W(THENAR["c"]), R=Rth, rad=np.asarray(THENAR["r"]) * s, g=g, p=PALM))
    # fingers and thumb: capsules with a sphere at every joint
    chains = glove_local(pose)
    for name, (pts, zs, r) in chains.items():
        part = THUMB if name == "thumb" else FINGER_PARTS[name]
        wp = [W(q) for q in pts]
        wz = [V(z) for z in zs]
        for i in range(len(wp) - 1):
            if name == "thumb" and i == 0:          # the thumb's root swells into the hand
                prims.append(dict(k="cone", A=wp[0], B=wp[1], rA=THUMB_ROOT_R * s, rB=r * s, g=g, p=part))
                continue
            zc = wz[i + 1]
            if name == "thumb":
                # the thumb flexes across the palm, so its creases face the palm side, not the flexion side
                zc = nrm(zc + V((0.0, 0.0, 1.0)) * THUMB_CREASE_TILT)
            prims.append(dict(k="cyl", A=wp[i], B=wp[i + 1], r=r * s, g=g, p=part,
                              crA=(i >= 1), crB=(i + 1 < len(wp) - 1), zA=zc, zB=zc))
        for i, q in enumerate(wp):
            rr = r * s * (1.04 if i == len(wp) - 1 else 1.0)
            if name == "thumb" and i == 0:
                rr = THUMB_ROOT_R * s
            prims.append(dict(k="sph", C=q, r=rr, g=g, p=part))
    # the cuff: an elliptical band around the wrist with a rolled rim
    T = W((0.0, CUFF_TOP, 0.0))
    a = V((0, -1, 0))
    ex, ez = V((1, 0, 0)), V((0, 0, 1))
    prims.append(dict(k="ecyl", T=T, a=a, L=CUFF_LEN * s, ex=ex, ez=ez, rx=CUFF_RX * s, rz=CUFF_RZ * s, g=g, p=CUFF))
    prims.append(dict(k="ecap", C=T + a * CUFF_LEN * s, N=a, ex=ex, ez=ez, rx=CUFF_RX * s, rz=CUFF_RZ * s, g=g, p=CAP))
    n = 18
    ring = [T + ex * (CUFF_RX + 0.018) * s * math.cos(2 * math.pi * i / n) + ez * (CUFF_RZ + 0.018) * s * math.sin(2 * math.pi * i / n)
            for i in range(n)]
    for i in range(n):
        prims.append(dict(k="cyl", A=ring[i], B=ring[(i + 1) % n], r=RIM_R * s, g=g, p=RIM))
        prims.append(dict(k="sph", C=ring[i], r=RIM_R * s, g=g, p=RIM))
    return prims


def transform_prims(prims, Q):
    """Rotate every primitive by Q (for the light's view)."""
    out = []
    for pr in prims:
        q = dict(pr)
        for key in ("C", "A", "B", "T", "Cs"):
            if key in q:
                q[key] = Q @ q[key]
        for key in ("N", "a", "ex", "ez", "zA", "zB"):
            if key in q:
                q[key] = Q @ q[key]
        for key in ("R", "M"):
            if key in q:
                q[key] = Q @ q[key]
        out.append(q)
    return out


# ------------------------------------------------------------------ rasterizer

class Raster:
    """A z-buffer over a world rectangle: pixel (i, j) centre = (x0 + (i + .5) / k, y0 - (j + .5) / k)."""

    def __init__(self, Wpx, Hpx, x0, y0, k, full=True):
        self.W, self.H, self.x0, self.y0, self.k = Wpx, Hpx, x0, y0, k
        self.z = np.full((Hpx, Wpx), -np.inf, np.float32)
        if full:
            self.n = np.zeros((Hpx, Wpx, 3), np.float32)
            self.g = np.full((Hpx, Wpx), -1, np.int8)
            self.p = np.full((Hpx, Wpx), -1, np.int8)
            self.face = np.zeros((Hpx, Wpx), np.int8)
            self.uv = np.zeros((Hpx, Wpx, 2), np.float32)
            self.crease = np.zeros((Hpx, Wpx), np.float32)
            self.icon = np.zeros((Hpx, Wpx), np.bool_)
        self.full = full

    def grid(self, xmin, xmax, ymin, ymax):
        i0 = max(0, int(math.floor((xmin - self.x0) * self.k)))
        i1 = min(self.W, int(math.ceil((xmax - self.x0) * self.k)) + 1)
        j0 = max(0, int(math.floor((self.y0 - ymax) * self.k)))
        j1 = min(self.H, int(math.ceil((self.y0 - ymin) * self.k)) + 1)
        if i1 <= i0 or j1 <= j0:
            return None
        xs = self.x0 + (np.arange(i0, i1) + 0.5) / self.k
        ys = self.y0 - (np.arange(j0, j1) + 0.5) / self.k
        X, Y = np.meshgrid(xs, ys)
        return (slice(j0, j1), slice(i0, i1)), X, Y

    def put(self, sl, m, z, n, pr, face=0, uv=None, crease=None):
        zb = self.z[sl]
        upd = m & (z > zb)
        if not upd.any():
            return
        zb[upd] = z[upd]
        if not self.full:
            return
        nb = self.n[sl]
        nb[upd] = n[upd]
        self.g[sl][upd] = pr["g"]
        self.p[sl][upd] = pr["p"]
        self.face[sl][upd] = face
        if uv is not None:
            self.uv[sl][upd] = uv[upd]
        cb = self.crease[sl]
        cb[upd] = crease[upd] if crease is not None else 0.0
        self.icon[sl][upd] = bool(pr.get("icon", False)) and face == 2

    # --- primitives

    def r_sph(self, pr):
        C, r = pr["C"], pr["r"]
        gr = self.grid(C[0] - r, C[0] + r, C[1] - r, C[1] + r)
        if gr is None:
            return
        sl, X, Y = gr
        dx, dy = X - C[0], Y - C[1]
        d2 = dx * dx + dy * dy
        m = d2 <= r * r
        dz = np.sqrt(np.maximum(r * r - d2, 0.0))
        z = C[2] + dz
        n = np.stack([dx, dy, dz], -1) / r
        self.put(sl, m, z, n, pr)

    def r_cyl(self, pr):
        A, B, r = pr["A"], pr["B"], pr["r"]
        D = B - A
        L = float(np.linalg.norm(D))
        if L < 1e-9:
            return
        u = D / L
        a = 1.0 - u[2] * u[2]
        if a < 1e-6:
            return
        lo, hi = np.minimum(A, B) - r, np.maximum(A, B) + r
        gr = self.grid(lo[0], hi[0], lo[1], hi[1])
        if gr is None:
            return
        sl, X, Y = gr
        dx, dy = X - A[0], Y - A[1]
        k = dx * u[0] + dy * u[1]
        b = -2.0 * k * u[2]
        c = dx * dx + dy * dy - k * k - r * r
        disc = b * b - 4.0 * a * c
        m = disc >= 0
        t = (-b + np.sqrt(np.maximum(disc, 0.0))) / (2.0 * a)
        sax = k + t * u[2]
        m &= (sax >= 0) & (sax <= L)
        z = A[2] + t
        w = np.stack([dx, dy, t], -1)
        n = (w - sax[..., None] * u) / r
        crease = None
        if self.full and (pr.get("crA") or pr.get("crB")):
            crease = np.zeros_like(z)
            wcr = 0.12 * r
            if pr.get("crA"):
                zA = pr["zA"]
                side = (n @ zA)
                crease = np.maximum(crease, (1.0 - smoothstep(0.0, wcr * 1.0, sax)) * smoothstep(0.15, 0.55, side))
            if pr.get("crB"):
                zB = pr["zB"]
                side = (n @ zB)
                crease = np.maximum(crease, (1.0 - smoothstep(0.0, wcr * 1.0, L - sax)) * smoothstep(0.15, 0.55, side))
        self.put(sl, m, z, n, pr, crease=crease)

    def r_cone(self, pr):
        """A frustum whose radius goes linearly from rA (at A) to rB (at B); spheres cap the ends."""
        A, B, rA, rB = pr["A"], pr["B"], pr["rA"], pr["rB"]
        D = B - A
        L = float(np.linalg.norm(D))
        if L < 1e-9:
            return
        u = D / L
        kr = (rB - rA) / L
        a = 1.0 - u[2] * u[2] - kr * kr * u[2] * u[2]
        if abs(a) < 1e-6:
            return
        rmax = max(rA, rB)
        lo, hi = np.minimum(A, B) - rmax, np.maximum(A, B) + rmax
        gr = self.grid(lo[0], hi[0], lo[1], hi[1])
        if gr is None:
            return
        sl, X, Y = gr
        dx, dy = X - A[0], Y - A[1]
        k = dx * u[0] + dy * u[1]
        r0 = rA + kr * k
        b = -2.0 * k * u[2] - 2.0 * r0 * kr * u[2]
        c = dx * dx + dy * dy - k * k - r0 * r0
        disc = b * b - 4.0 * a * c
        m = disc >= 0
        sq = np.sqrt(np.maximum(disc, 0.0))
        t1 = (-b + sq) / (2.0 * a)
        t2 = (-b - sq) / (2.0 * a)
        t = np.maximum(t1, t2)
        sax = k + t * u[2]
        m &= (sax >= 0) & (sax <= L) & ((rA + kr * sax) > 0)
        z = A[2] + t
        w = np.stack([dx, dy, t], -1)
        rr = (rA + kr * sax)[..., None]
        n = (w - sax[..., None] * u) - rr * kr * u
        n = n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)
        self.put(sl, m, z, n, pr)

    def r_face(self, pr):
        C, N = pr["C"], pr["N"]
        if abs(N[2]) < 1e-4:
            return
        M, s, Cs = pr["M"], pr["s"], pr["Cs"]
        ext = (PALM_HX + 0.0) * s, (PALM_HY + 0.0) * s
        corners = [Cs + M @ np.array([sx * ext[0], sy * ext[1], 0.0]) for sx in (-1, 1) for sy in (-1, 1)]
        pts = np.array(corners) + (C - Cs)
        lo, hi = pts.min(0), pts.max(0)
        gr = self.grid(lo[0], hi[0], lo[1], hi[1])
        if gr is None:
            return
        sl, X, Y = gr
        z = C[2] - (N[0] * (X - C[0]) + N[1] * (Y - C[1])) / N[2]
        P = np.stack([X - Cs[0], Y - Cs[1], z - Cs[2]], -1)
        q = (P @ M) / s                                    # local coordinates (M columns are the axes)
        m = sd_round_rect(q[..., 0], q[..., 1], pr["hx"], pr["hy"], pr["rho"]) <= 0
        # shade it as a gently domed surface: the normal leans out toward the rim
        bx = np.clip(q[..., 0] / (pr["hx"] + PALM_T), -1, 1) * 0.85
        by = np.clip(q[..., 1] / (pr["hy"] + PALM_T), -1, 1) * 0.65
        n = (N[None, None, :] + bx[..., None] * M[:, 0][None, None, :] * 1.0 + by[..., None] * M[:, 1][None, None, :])
        n = n / np.linalg.norm(n, axis=-1, keepdims=True)
        uv = np.stack([q[..., 0], q[..., 1] + PALM_CY], -1)
        self.put(sl, m, z, n, pr, face=1 if pr["side"] > 0 else 2, uv=uv)

    def r_ell(self, pr):
        C, R, rad = pr["C"], pr["R"], pr["rad"]
        ext = np.sqrt(((R * rad[None, :]) ** 2).sum(1))
        gr = self.grid(C[0] - ext[0], C[0] + ext[0], C[1] - ext[1], C[1] + ext[1])
        if gr is None:
            return
        sl, X, Y = gr
        P0 = np.stack([X - C[0], Y - C[1], np.full_like(X, -C[2])], -1)
        q0 = (P0 @ R) / rad
        qd = (R.T @ np.array([0.0, 0.0, 1.0])) / rad
        A = float(qd @ qd)
        Bq = q0 @ qd
        Cc = (q0 * q0).sum(-1) - 1.0
        disc = Bq * Bq - A * Cc
        m = disc >= 0
        zz = (-Bq + np.sqrt(np.maximum(disc, 0.0))) / A
        q = q0 + zz[..., None] * qd
        n = (q / rad) @ R.T
        n = n / np.linalg.norm(n, axis=-1, keepdims=True)
        self.put(sl, m, zz, n, pr)

    def r_ecyl(self, pr):
        T, a, L, ex, ez, rx, rz = pr["T"], pr["a"], pr["L"], pr["ex"], pr["ez"], pr["rx"], pr["rz"]
        pts = [T + a * t + ex * rx * cx + ez * rz * cz for t in (0, L) for cx in (-1, 1) for cz in (-1, 1)]
        pts = np.array(pts)
        lo, hi = pts.min(0), pts.max(0)
        gr = self.grid(lo[0], hi[0], lo[1], hi[1])
        if gr is None:
            return
        sl, X, Y = gr
        dx, dy = X - T[0], Y - T[1]
        qx0 = (dx * ex[0] + dy * ex[1] - T[2] * ex[2]) / rx
        qx1 = ex[2] / rx
        qz0 = (dx * ez[0] + dy * ez[1] - T[2] * ez[2]) / rz
        qz1 = ez[2] / rz
        A = qx1 * qx1 + qz1 * qz1
        if A < 1e-9:
            return
        Bq = qx0 * qx1 + qz0 * qz1
        Cc = qx0 * qx0 + qz0 * qz0 - 1.0
        disc = Bq * Bq - A * Cc
        m = disc >= 0
        zz = (-Bq + np.sqrt(np.maximum(disc, 0.0))) / A
        ax = dx * a[0] + dy * a[1] + (zz - T[2]) * a[2]
        m &= (ax >= 0) & (ax <= L)
        qx = qx0 + zz * qx1
        qz = qz0 + zz * qz1
        n = (qx / rx)[..., None] * ex + (qz / rz)[..., None] * ez
        n = n / np.linalg.norm(n, axis=-1, keepdims=True)
        self.put(sl, m, zz, n, pr)

    def r_ecap(self, pr):
        C, N, ex, ez, rx, rz = pr["C"], pr["N"], pr["ex"], pr["ez"], pr["rx"], pr["rz"]
        if N[2] < 0.03:          # facing away (the cuff's wall hides it) or edge-on: nothing to draw
            return
        pts = np.array([C + ex * rx * cx + ez * rz * cz for cx in (-1, 1) for cz in (-1, 1)])
        lo, hi = pts.min(0), pts.max(0)
        gr = self.grid(lo[0], hi[0], lo[1], hi[1])
        if gr is None:
            return
        sl, X, Y = gr
        z = C[2] - (N[0] * (X - C[0]) + N[1] * (Y - C[1])) / N[2]
        P = np.stack([X - C[0], Y - C[1], z - C[2]], -1)
        m = ((P @ ex) / rx) ** 2 + ((P @ ez) / rz) ** 2 <= 1.0
        n = np.broadcast_to(N, X.shape + (3,))
        self.put(sl, m, z, n, pr)

    def draw(self, prims):
        for pr in prims:
            getattr(self, "r_" + pr["k"])(pr)


# ------------------------------------------------------------------ shading

def _shift(a, dy, dx, fill):
    out = np.full_like(a, fill)
    H, W = a.shape[:2]
    ys = slice(max(0, dy), H + min(0, dy))
    yd = slice(max(0, -dy), H + min(0, -dy))
    xs = slice(max(0, dx), W + min(0, dx))
    xd = slice(max(0, -dx), W + min(0, -dx))
    out[ys, xs] = a[yd, xd]
    return out


def max_filter(a, radius, fill):
    """Octagonal max filter (alternating cross / box steps), radius in pixels."""
    out = a
    for i in range(int(round(radius))):
        if i % 2 == 0:
            nb = [(0, 1), (0, -1), (1, 0), (-1, 0)]
        else:
            nb = [(1, 1), (1, -1), (-1, 1), (-1, -1), (0, 1), (0, -1), (1, 0), (-1, 0)]
        acc = out
        for dy, dx in nb:
            acc = np.maximum(acc, _shift(out, dy, dx, fill))
        out = acc
    return out


def max_filter_steps(a, radii, fill):
    """The octagonal max filter at several (increasing) radii, sharing the work."""
    out, res, done = a, [], 0
    for r in radii:
        while done < int(round(r)):
            nb = [(0, 1), (0, -1), (1, 0), (-1, 0)] if done % 2 == 0 else \
                [(1, 1), (1, -1), (-1, 1), (-1, -1), (0, 1), (0, -1), (1, 0), (-1, 0)]
            acc = out
            for dy, dx in nb:
                acc = np.maximum(acc, _shift(out, dy, dx, fill))
            out = acc
            done += 1
        res.append(out)
    return res


def tapered_ink(zf, rad, tau, fill=-np.inf):
    """Outline pixels behind a nearer surface: thin where the depth jump is small, full width where it
    is large, so lines taper off instead of ending raggedly."""
    radii = [rad * 0.40, rad * 0.70, rad]
    taus = [tau, tau * 1.6, tau * 2.4]
    zs = max_filter_steps(zf, radii, fill)
    ink = np.zeros(zf.shape, bool)
    for zm, tt in zip(zs, taus):
        ink |= zm > zf + tt
    return ink


def mixc(a, b, t):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return a + (b - a) * t


def hexc(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


@dataclass
class Look:
    glove: str = "#ecf6f7"
    ink: str = "#0d1621"
    cuff: str = "#0c8ea0"
    stitches: str = "#0c8ea0"


CAPTURE = None      # set to {} to receive the raw buffers of the next render (for tracing)
DEBUG_TINT = None   # e.g. {0: (1.0, 0.85, 0.85)} to tell the gloves apart while posing


def render(prims_by_glove, Wpx, Hpx, x0, y0, k, ss=3, look: Look = Look(), size=0.55, icon=None, light=(-0.80, 0.45, 0.36), dim=None):
    """Render to an RGBA float array (H, W, 4), premultiplied, at Wpx x Hpx over the world rect."""
    prims = [p for ps in prims_by_glove for p in ps]
    R = Raster(Wpx * ss, Hpx * ss, x0, y0, k * ss)
    R.draw(prims)
    valid = np.isfinite(R.z)
    L = nrm(light)
    # ---- shadows: the scene seen from the light
    zl = nrm(L)
    xl = nrm(np.cross([0.0, 1.0, 0.0], zl))
    yl = np.cross(zl, xl)
    Q = np.stack([xl, yl, zl])
    lp = transform_prims(prims, Q)
    pts = []
    for pr in lp:
        for key in ("C", "A", "B", "T"):
            if key in pr:
                pts.append(pr[key])
    pts = np.array(pts)
    pad = size * 0.4
    lo, hi = pts.min(0) - pad, pts.max(0) + pad
    kl = k * ss * 0.5
    SW, SH = int((hi[0] - lo[0]) * kl) + 2, int((hi[1] - lo[1]) * kl) + 2
    S = Raster(SW, SH, lo[0], hi[1], kl, full=False)
    S.draw(lp)
    jj, ii = np.nonzero(valid)
    xw = R.x0 + (ii + 0.5) / R.k
    yw = R.y0 - (jj + 0.5) / R.k
    zw = R.z[jj, ii]
    nw = R.n[jj, ii]
    Pw = np.stack([xw, yw, zw], -1) + nw * (0.012 * size)          # normal offset against acne
    Pl = Pw @ Q.T
    li = ((Pl[:, 0] - S.x0) * S.k).astype(int)
    lj = ((S.y0 - Pl[:, 1]) * S.k).astype(int)
    lit = np.zeros(len(li), float)
    cnt = 0
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            a = np.clip(li + ox, 0, SW - 1)
            b = np.clip(lj + oy, 0, SH - 1)
            lit += (Pl[:, 2] >= S.z[b, a] - 0.02 * size).astype(float)
            cnt += 1
    lit /= cnt
    # ---- colours
    g, ink, cuff = hexc(look.glove), hexc(look.ink), hexc(look.cuff)
    white = np.ones(3)
    tones = {
        "glove": (mixc(g, ink, 0.32), g, mixc(g, white, 0.85)),
        "cuff": (mixc(cuff, ink, 0.38), cuff, mixc(cuff, white, 0.45)),
        "rim": (mixc(cuff, ink, 0.30), mixc(cuff, white, 0.15), mixc(cuff, white, 0.60)),
        "cap": (mixc(cuff, ink, 0.65), mixc(cuff, ink, 0.55), mixc(cuff, ink, 0.45)),
    }
    part = R.p[jj, ii]
    I = nw @ L
    # occlusion: darken what sits just behind a nearer surface (contact shading between overlapping parts)
    pz_full = np.where(valid, R.z, -np.inf).astype(np.float32)
    znear = max_filter(pz_full, 0.10 * size * R.k, -np.inf)[jj, ii]
    occl = smoothstep(0.03 * size, 0.20 * size, znear - zw)
    view = 0.72 + 0.28 * smoothstep(0.0, 0.75, nw[:, 2])
    t = smoothstep(-0.12, 0.88, I) * (0.38 + 0.62 * lit) * view * (1.0 - 0.35 * occl)
    pf = (R.face[jj, ii] > 0) & (R.p[jj, ii] == PALM)
    t[pf] *= 0.78 + 0.22 * smoothstep(0.02, 0.32, R.uv[jj, ii][pf, 1])      # the wrist end falls into shadow
    rgb = np.zeros((len(ii), 3))
    for name, sel in (("glove", part <= THUMB), ("cuff", part == CUFF), ("rim", part == RIM), ("cap", part == CAP)):
        if not sel.any():
            continue
        sh, base, hi_ = tones[name]
        tt = t[sel][:, None]
        c = mixc(sh, base, smoothstep(0.10, 0.48, tt))
        c = mixc(c, hi_, smoothstep(0.70, 0.98, tt) * 0.80)
        rgb[sel] = c
    if dim:
        gidv = R.g[jj, ii]
        for gl_, amt in dim.items():
            sel = (gidv == gl_) & (part <= THUMB)
            rgb[sel] = mixc(rgb[sel], tones["glove"][0], amt)
    if DEBUG_TINT:
        gidv = R.g[jj, ii]
        for gl_, tint in DEBUG_TINT.items():
            sel = (gidv == gl_) & (part <= THUMB)
            rgb[sel] = rgb[sel] * np.asarray(tint)
    # a soft specular glint
    H_ = nrm(L + np.array([0.0, 0.0, 1.0]))
    spec = smoothstep(0.955, 0.99, nw @ H_) * lit
    rgb = mixc(rgb, white, (spec * 0.55)[:, None])
    # ---- surface details
    face = R.face[jj, ii]
    uv = R.uv[jj, ii]
    gl_ = (part == PALM)
    # stitching on the back of the hand
    st_col = hexc(look.stitches)
    back = gl_ & (face == 2)
    if back.any():
        u, v = uv[back, 0], uv[back, 1]
        m = np.zeros(u.shape, bool)
        for sx in (0.10, -0.02, -0.14):
            cx = sx + 0.02 * np.sin((v - 0.46) / 0.10 * math.pi)
            m |= (np.abs(u - cx) < 0.011) & (v > 0.46) & (v < 0.56)
        sub = rgb[back]
        sub[m] = st_col
        rgb[back] = sub
    # soft palm lines on the palm side
    palm_side = gl_ & (face == 1)
    if palm_side.any():
        u, v = uv[palm_side, 0], uv[palm_side, 1]
        l1 = np.abs(v - (0.50 - 0.05 * (u / 0.2) ** 2)) < 0.008
        l2 = (np.abs(u - (0.06 - 0.25 * (v - 0.30))) < 0.008) & (v > 0.18) & (v < 0.44)
        sub = rgb[palm_side]
        soft = mixc(g, tones["glove"][0], 0.55)
        sub[l1 | l2] = mixc(sub[l1 | l2], soft, 0.8)
        rgb[palm_side] = sub
    # the icon on the back of the hand
    if icon is not None:
        ic = R.icon[jj, ii] & (part == PALM)
        if ic.any():
            u, v = uv[ic, 0], uv[ic, 1]
            side_ = 0.24
            iu = (0.0 + side_ / 2 - u) / side_         # thumb side (+X) reads as the picture's left from the back
            iv = (0.33 + side_ / 2 - v) / side_
            inside = (iu >= 0) & (iu < 1) & (iv >= 0) & (iv < 1)
            ih, iw = icon.shape[:2]
            px_ = np.clip((iu * iw).astype(int), 0, iw - 1)
            py_ = np.clip((iv * ih).astype(int), 0, ih - 1)
            col = icon[py_, px_]
            a = col[:, 3] * inside
            sub = rgb[ic]
            sub = mixc(sub, col[:, :3], a[:, None])
            rgb[ic] = sub
    # joint creases (palm side of the fingers / thumb)
    cr = R.crease[jj, ii]
    rgb = mixc(rgb, mixc(g, tones["glove"][0], 0.9), (cr * 0.75)[:, None])
    # ---- outlines
    tau = 0.07 * size
    tau_soft = 0.17 * size
    zf = np.where(valid, R.z, -np.inf).astype(np.float32)
    rad = 0.034 * size * R.k
    ink_depth = tapered_ink(zf, rad, tau)
    for gl in np.unique(R.g[valid]):
        own_thumb = valid & (R.g == gl) & (R.p == THUMB)
        if not own_thumb.any():
            continue
        palm_px = valid & (R.g == gl) & (R.p == PALM)
        z_wo = np.where(own_thumb, -np.inf, zf).astype(np.float32)
        z_th = np.where(own_thumb, zf, -np.inf).astype(np.float32)
        soft = tapered_ink(z_wo, rad, tau) & True
        soft = np.where(np.isfinite(zf), soft, False)
        soft_wo = np.zeros_like(soft)
        zs_wo = max_filter_steps(z_wo, [rad * 0.4, rad * 0.7, rad], -np.inf)
        zs_th = max_filter_steps(z_th, [rad * 0.4, rad * 0.7, rad], -np.inf)
        for zm, tt in zip(zs_wo, [tau, tau * 1.6, tau * 2.4]):
            soft_wo |= zm > zf + tt
        for zm, tt in zip(zs_th, [tau_soft, tau_soft * 1.4, tau_soft * 1.9]):
            soft_wo |= zm > zf + tt
        soft = soft_wo
        ink_depth = np.where(palm_px, soft, ink_depth)
    # id boundaries: different fingers / thumb of one glove, glove vs cuff, glove vs glove
    gid = R.g.astype(np.int16)
    pid = R.p.astype(np.int16)
    cls = np.where(pid <= PALM, gid * 10 + 0, gid * 10 + pid)          # palm is its own class
    cls = np.where(valid, cls, -1)
    edge = np.zeros_like(valid)
    for dy, dx in ((0, 1), (1, 0)):
        o = _shift(cls, dy, dx, -1)
        oz = _shift(np.where(valid, R.z, -np.inf), dy, dx, -np.inf)
        pz = np.where(valid, R.z, -np.inf)
        diff = (o != cls) & (o >= 0) & (cls >= 0)
        # palm vs its own finger / thumb: only where the depth jumps (they grow out of each other)
        same_glove = (o // 10) == (cls // 10)
        o_part = o % 10
        c_part = cls % 10
        palm_pair = same_glove & ((o_part == 0) | (c_part == 0)) & ~(((o_part >= 5) | (c_part >= 5)))
        diff &= ~palm_pair
        e = diff
        edge |= e | _shift(e, -dy, -dx, False)
    ink_id = max_filter(edge.astype(np.float32), 0.012 * size * R.k, 0.0) > 0.5
    inkm = ink_depth | ink_id
    if CAPTURE is not None:
        tone = np.full(valid.shape, np.nan, np.float32); tone[jj, ii] = t
        litm = np.zeros(valid.shape, np.float32); litm[jj, ii] = lit
        CAPTURE.update(valid=valid, z=R.z.copy(), g=R.g.copy(), p=R.p.copy(), ink=inkm.copy(), ink_id=ink_id.copy(),
                       ink_depth=ink_depth.copy(), tone=tone, lit=litm, face=R.face.copy(), uv=R.uv.copy(),
                       crease=R.crease.copy(), x0=R.x0, y0=R.y0, k=R.k, ss=ss, size=size)
    # ---- compose (super-sampled), then box-filter down
    out = np.zeros((R.H, R.W, 4), np.float32)
    col = np.zeros((R.H, R.W, 3), np.float32)
    col[jj, ii] = rgb
    alpha = valid.astype(np.float32)
    col[inkm] = ink
    alpha[inkm] = 1.0
    out[..., :3] = col * alpha[..., None]
    out[..., 3] = alpha
    out = out.reshape(Hpx, ss, Wpx, ss, 4).mean(axis=(1, 3))
    return out


def to_qimage(arr):
    from PySide6.QtGui import QImage
    a = np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)
    H, W = a.shape[:2]
    bgra = np.empty((H, W, 4), np.uint8)
    bgra[..., 0] = a[..., 2]
    bgra[..., 1] = a[..., 1]
    bgra[..., 2] = a[..., 0]
    bgra[..., 3] = a[..., 3]
    img = QImage(bgra.data, W, H, W * 4, QImage.Format_ARGB32_Premultiplied)
    return img.copy()
