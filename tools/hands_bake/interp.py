"""Pose interpolation for the animated previews."""
import math, copy
import numpy as np
from g3d import PALM_CY

# ------------------------------------------------------------------ rotations
def mat_to_quat(M):
    t = np.trace(M)
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        return np.array([0.25 * s, (M[2, 1] - M[1, 2]) / s, (M[0, 2] - M[2, 0]) / s, (M[1, 0] - M[0, 1]) / s])
    i = int(np.argmax(np.diag(M)))
    if i == 0:
        s = math.sqrt(1.0 + M[0, 0] - M[1, 1] - M[2, 2]) * 2
        return np.array([(M[2, 1] - M[1, 2]) / s, 0.25 * s, (M[0, 1] + M[1, 0]) / s, (M[0, 2] + M[2, 0]) / s])
    if i == 1:
        s = math.sqrt(1.0 + M[1, 1] - M[0, 0] - M[2, 2]) * 2
        return np.array([(M[0, 2] - M[2, 0]) / s, (M[0, 1] + M[1, 0]) / s, 0.25 * s, (M[1, 2] + M[2, 1]) / s])
    s = math.sqrt(1.0 + M[2, 2] - M[0, 0] - M[1, 1]) * 2
    return np.array([(M[1, 0] - M[0, 1]) / s, (M[0, 2] + M[2, 0]) / s, (M[1, 2] + M[2, 1]) / s, 0.25 * s])


def quat_to_mat(q):
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def slerp(q0, q1, t):
    d = float(q0 @ q1)
    if d < 0:
        q1, d = -q1, -d
    if d > 0.9995:
        q = q0 + (q1 - q0) * t
        return q / np.linalg.norm(q)
    th = math.acos(d)
    return (math.sin((1 - t) * th) * q0 + math.sin(t * th) * q1) / math.sin(th)


def palm_centre(hp):
    return np.asarray(hp.O, float) + hp.s * (hp.frame() @ np.array([0.0, PALM_CY, 0.0]))


def lerp_pose(a, b, t):
    """Interpolate two HandPoses: the palm centre moves straight, the hand turns by slerp, joints blend."""
    def proper(hp):
        # a right-handed basis from the pose's Y and Z (the left glove's own frame is mirrored, det -1)
        F = hp.frame()
        Y, Z = F[:, 1], F[:, 2]
        return np.column_stack([np.cross(Y, Z), Y, Z])
    Ma, Mb = proper(a), proper(b)
    M = quat_to_mat(slerp(mat_to_quat(Ma), mat_to_quat(Mb), t))
    s = a.s + (b.s - a.s) * t
    c = palm_centre(a) + (palm_centre(b) - palm_centre(a)) * t
    out = copy.deepcopy(a)
    out.Y = tuple(M[:, 1])
    out.Z = tuple(M[:, 2])
    out.s = s
    out.O = tuple(c - s * (M @ np.array([0.0, PALM_CY, 0.0])))
    out.fingers = {n: tuple(np.asarray(a.fingers[n], float) + (np.asarray(b.fingers[n], float) - np.asarray(a.fingers[n], float)) * t)
                   for n in a.fingers}
    out.thumb = tuple(np.asarray(a.thumb, float) + (np.asarray(b.thumb, float) - np.asarray(a.thumb, float)) * t)
    out.tb_dy = a.tb_dy + (b.tb_dy - a.tb_dy) * t
    out.tb_dz = a.tb_dz + (b.tb_dz - a.tb_dz) * t
    out._clasped = getattr(b, "_clasped", False) if t > 0.5 else getattr(a, "_clasped", False)
    return out


