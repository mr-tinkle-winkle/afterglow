"""The 'button pressed' pose: the gloves apart, palms turned toward each other, relaxed."""
import math
import numpy as np
from g3d import HandPose, rot, nrm


def ready3d(s=0.55, apart=0.50, turn=30.0, lean=8.0, drop=0.0, lthumb=(70.0, 8.0, 0.0, 18.0, 16.0), rthumb=(70.0, 8.0, 0.0, 18.0, 16.0),
            lfingers=None, rfingers=None, rturn=None, rlean=None, rdy=0.0, ldy=0.0):
    """turn: each palm turned this far toward the other glove (0 = the left palm / right back face the viewer);
    lean: the gloves' tops lean outward by this much (degrees); apart: the wrists' distance from the centre."""
    fb = (6.0, 14.0, 10.0)
    lf = lfingers or {"index": (4.0,) + fb, "middle": (0.0,) + fb, "pinky": (-5.0,) + fb}
    rf = rfingers or {"index": (4.0,) + fb, "middle": (0.0,) + fb, "pinky": (-5.0,) + fb}
    rturn = turn if rturn is None else rturn
    rlean = lean if rlean is None else rlean
    a, b = math.radians(turn), math.radians(rturn)
    YL = rot((0, 0, 1), lean) @ np.array([0.0, 1.0, 0.0])           # leans left (outward)
    YR = rot((0, 0, 1), -rlean) @ np.array([0.0, 1.0, 0.0])
    ZL = np.array([math.sin(a), 0.0, math.cos(a)])
    ZR = np.array([-math.sin(b), 0.0, -math.cos(b)])
    left = HandPose("left", O=(-apart * s, -0.33 * s + ldy * s, 0.0), Y=tuple(YL), Z=tuple(ZL), s=s, fingers=lf, thumb=tuple(lthumb))
    right = HandPose("right", O=(apart * s, -0.33 * s + rdy * s, 0.0), Y=tuple(YR), Z=tuple(ZR), s=s, fingers=rf, thumb=tuple(rthumb))
    return left, right
