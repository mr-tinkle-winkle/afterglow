"""Trace captured 3D buffers into 2D vector geometry: fill regions, cel-shadow shapes, highlight
spots, a silhouette and interior ink centerlines (with the renderer's line weights as pressure)."""
import numpy as np, cv2, pickle, sys
from scipy import ndimage as ndi
from skimage.morphology import skeletonize
from g3d import PALM, THUMB, CUFF, RIM, CAP

def smooth_closed(c, sigma):
    if len(c) < 8: return c
    return np.stack([ndi.gaussian_filter1d(c[:, i].astype(float), sigma, mode="wrap") for i in (0, 1)], 1)

def smooth_open(c, sigma):
    if len(c) < 5: return c.astype(float)
    out = np.stack([ndi.gaussian_filter1d(c[:, i].astype(float), sigma, mode="nearest") for i in (0, 1)], 1)
    out[0], out[-1] = c[0], c[-1]
    return out

def contours(mask, sigma=4.0, min_area=40, step=3):
    m = mask.astype(np.uint8)
    cs, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    out = []
    for c in cs:
        if abs(cv2.contourArea(c)) < min_area: continue
        p = c[:, 0, :].astype(float)
        p = smooth_closed(p, sigma)[::step]
        out.append(p)
    return out

def blob(mask, blur, thr=0.5):
    f = ndi.gaussian_filter(mask.astype(np.float32), blur)
    return f > thr

NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

def trace_skeleton(sk):
    """Polylines (row, col) along a 1-px skeleton."""
    H, W = sk.shape
    sk = sk.copy()
    cnt = ndi.convolve(sk.astype(np.uint8), np.ones((3, 3), np.uint8), mode="constant") - 1
    cnt[~sk] = 0
    node = sk & (cnt != 2)
    visited_edge = set()
    paths = []
    pix = set(zip(*np.nonzero(sk)))
    def nbrs(p):
        r, c = p
        return [(r + dr, c + dc) for dr, dc in NB if (r + dr, c + dc) in pix]
    used = set()
    for start in zip(*np.nonzero(node)):
        for n in nbrs(start):
            e = (start, n) if start < n else (n, start)
            if e in visited_edge: continue
            path = [start, n]; visited_edge.add(e); used.add(start); used.add(n)
            prev, cur = start, n
            while not node[cur]:
                nx = [q for q in nbrs(cur) if q != prev and ((cur, q) if cur < q else (q, cur)) not in visited_edge]
                if not nx: break
                # prefer straight continuation
                d0 = (cur[0] - prev[0], cur[1] - prev[1])
                nx.sort(key=lambda q: -((q[0] - cur[0]) * d0[0] + (q[1] - cur[1]) * d0[1]))
                q = nx[0]
                visited_edge.add((cur, q) if cur < q else (q, cur))
                path.append(q); used.add(q); prev, cur = cur, q
            paths.append(np.array(path))
    # loops with no nodes
    rest = pix - used
    while rest:
        start = rest.pop()
        path = [start]; prev = None; cur = start
        while True:
            nx = [q for q in nbrs(cur) if q != prev and q in rest]
            if not nx: break
            q = nx[0]; rest.discard(q); path.append(q); prev, cur = cur, q
        if len(path) > 2: path.append(path[0])
        paths.append(np.array(path))
    return paths

def join_paths(paths, tol=3.0):
    """Greedy join of polylines whose ends meet with a smooth continuation (so lines stay long)."""
    paths = [p.astype(float) for p in paths]
    changed = True
    while changed:
        changed = False
        for i in range(len(paths)):
            if paths[i] is None: continue
            for j in range(len(paths)):
                if i == j or paths[j] is None: continue
                a, b = paths[i], paths[j]
                for ra in (False, True):
                    for rb in (False, True):
                        A = a[::-1] if ra else a
                        B = b[::-1] if rb else b
                        if np.linalg.norm(A[-1] - B[0]) <= tol and len(A) > 3 and len(B) > 3:
                            da = A[-1] - A[-4]; db = B[3] - B[0]
                            if np.dot(da, db) / (np.linalg.norm(da) * np.linalg.norm(db) + 1e-9) > 0.7:
                                paths[i] = np.vstack([A, B[1:]]); paths[j] = None; changed = True
                                break
                    if changed: break
                if changed: break
            if changed: break
    return [p for p in paths if p is not None]

def trace_frame(C, n):
    g = lambda k: C[f"{n}_{k}"]
    valid, part, gid, ink, tone, z = g("valid"), g("p"), g("g"), g("ink"), g("tone"), g("z")
    ss = int(g("ss"))
    res = {}
    res["shape"] = valid.shape
    res["silhouette"] = contours(valid, sigma=3.0 * ss / 2, step=2)
    gloves = [int(x) for x in np.unique(gid[valid])]
    res["regions"] = {}
    for gl in gloves:
        own = valid & (gid == gl)
        res["regions"][(gl, "glove")] = contours(own & (part <= THUMB), sigma=2.0 * ss / 2, step=2)
        res["regions"][(gl, "cuff")] = contours(own & (part == CUFF), sigma=2.0 * ss / 2, step=2)
        res["regions"][(gl, "rim")] = contours(own & (part == RIM), sigma=2.0 * ss / 2, step=2)
        res["regions"][(gl, "cap")] = contours(own & (part == CAP), sigma=2.0 * ss / 2, step=2)
        t = np.nan_to_num(tone, nan=1.0)
        for name, sel in (("glove", part <= THUMB), ("cuff", (part == CUFF) | (part == CAP)), ("rim", part == RIM)):
            m = own & sel
            for lvl, thr in (("shadow", 0.34), ("deep", 0.20), ("light", 0.88)):
                mm = (m & (t < thr)) if lvl != "light" else (m & (t > thr))
                mm = blob(mm, (4.0 if lvl == 'light' else 3.0) * ss, 0.5) & m
                res["regions"][(gl, f"{name}_{lvl}")] = contours(mm, sigma=3.0 * ss / 2, min_area=60 * ss, step=2)
    # interior lines straight from the buffers: part boundaries and depth jumps (as the renderer decides them)
    size = float(g("size"))
    tau, tau_soft = 0.07 * size, 0.17 * size
    zf = np.where(valid, z, -np.inf)
    cls = np.where(part <= PALM, gid * 10, gid * 10 + part).astype(np.int32)
    cls = np.where(valid, cls, -1)
    edge = np.zeros(valid.shape, bool); wgt = np.zeros(valid.shape, np.float32)
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        o = np.roll(np.roll(cls, -dy, 0), -dx, 1); oz = np.roll(np.roll(zf, -dy, 0), -dx, 1)
        op = np.roll(np.roll(part, -dy, 0), -dx, 1); og = np.roll(np.roll(gid, -dy, 0), -dx, 1)
        both = valid & (o >= 0)
        dz = np.abs(zf - oz); dz[~both] = 0
        same_glove = (o // 10) == (cls // 10)
        oc, cc = o % 10, cls % 10
        palm_pair = same_glove & ((oc == 0) | (cc == 0)) & ~((oc >= 5) | (cc >= 5))
        idd = both & (o != cls) & ~palm_pair
        thumb_palm = same_glove & (((part == THUMB) & (op <= PALM) & (op != THUMB)) | ((op == THUMB) & (part <= PALM) & (part != THUMB)))
        dep = both & (dz > np.where(thumb_palm, tau_soft, tau))
        e = idd | dep
        # mark the nearer pixel of the pair
        near_here = zf >= oz
        m1 = e & near_here
        m2 = np.roll(np.roll(e & ~near_here, dy, 0), dx, 1)
        w = np.where(dep, np.clip(dz / size, 0.05, 0.6), 0.12)
        edge |= m1 | m2
        wgt = np.maximum(wgt, np.where(m1, w, 0)); wgt = np.maximum(wgt, np.roll(np.roll(np.where(e & ~near_here, w, 0), dy, 0), dx, 1))
    # drop the stitch / palm-line details: only keep edges between surfaces, never inside one
    edge &= ndi.binary_erosion(valid, iterations=max(2, ss))
    edge = ndi.binary_closing(edge, iterations=1)
    sk = skeletonize(edge)
    wgt = ndi.grey_dilation(wgt, size=(5, 5))
    paths = join_paths(trace_skeleton(sk), tol=3.0)
    lines = []
    for p in paths:
        if len(p) < 10 * ss / 2: continue
        w = wgt[p[:, 0].astype(int), p[:, 1].astype(int)]
        xy = smooth_open(p[:, ::-1], 3.0 * ss / 2)
        w = ndi.gaussian_filter1d(w.astype(float), 4.0)
        lines.append((xy[::2], w[::2]))
    dist = ndi.distance_transform_edt(ink)
    skk = skeletonize(ink)
    iw = float(np.median(dist[skk])) if skk.any() else 2.0
    res["ink_width"] = iw
    res["lines"] = [(xy, iw * (0.55 + 0.9 * np.clip(w / 0.25, 0, 1))) for xy, w in lines]
    return res
    res["lines"] = lines
    res["ink_width"] = float(np.median(dist[sk])) if sk.any() else 2.0
    return res

if __name__ == "__main__":
    C = dict(np.load(sys.argv[1]))
    out = {n: trace_frame(C, n) for n in ("ready", "impact")}
    for n in out:
        print(n, "silhouette", len(out[n]["silhouette"]), "lines", len(out[n]["lines"]), "ink w", out[n]["ink_width"])
    pickle.dump(out, open(sys.argv[2], "wb"))
