"""
Explicit clasp design: every joint angle is set by hand (no solver), then checked for clearance.

World: x right, y up, z toward the viewer.  The palms meet in the plane z = 0 (frontal, phi = 90):
the left palm's front face (local +Z) faces the viewer, the right palm's front face faces away, so
the viewer sees the back of the right glove and, behind it, the left palm.
"""
from __future__ import annotations

import copy
import math

import numpy as np

import g3d
from g3d import PALM_CY, PALM_T, HandPose, glove_prims, render, transform_prims
from pose3d import (chain_clearances, place, prim_bounds, world_chains, palm_dist, seg_dist, samples,
                    THUMB_ROOT_R)


def build(cl=50.0, cr=28.0, d=(0.10, 0.12), s=0.55, tilt=0.0,
          lf=None, rf=None, lt=(55.0, 25.0, 0.0, 30.0, 30.0), rt=(60.0, -8.0, 0.0, 20.0, 20.0)):
    """cl: the left glove turned clockwise from upright; cr: the right glove counter-clockwise.
    d: the right palm centre relative to the left palm centre (world x, y; units of s)."""
    lf = lf or dict(index=(-4.0, 12.0, 30.0, 24.0), middle=(0.0, 12.0, 30.0, 24.0), pinky=(6.0, 12.0, 30.0, 24.0))
    rf = rf or dict(index=(0.0, 10.0, 20.0, 15.0), middle=(0.0, 10.0, 20.0, 15.0), pinky=(0.0, 10.0, 20.0, 15.0))
    a, b = math.radians(cl), math.radians(cr)
    YL = np.array([math.sin(a), math.cos(a), 0.0])
    YR = np.array([-math.sin(b), math.cos(b), 0.0])
    n = np.array([0.0, 0.0, 1.0])
    left = HandPose("left", O=(0, 0, 0), Y=YL, Z=n, s=s, fingers=dict(lf), thumb=tuple(lt))
    right = HandPose("right", O=(0, 0, 0), Y=YR, Z=-n, s=s, fingers=dict(rf), thumb=tuple(rt))
    ML, MR = left.frame(), right.frame()
    cL = np.array([0.0, 0.0, -PALM_T * s])
    cR = np.array([d[0] * s, d[1] * s, PALM_T * s])
    left.O = tuple(cL - s * (ML @ np.array([0.0, PALM_CY, 0.0])))
    right.O = tuple(cR - s * (MR @ np.array([0.0, PALM_CY, 0.0])))
    if tilt:
        Rt = g3d.rot((0, 1, 0), tilt)
        for hp in (left, right):
            hp.O = tuple(Rt @ np.asarray(hp.O))
            hp.Y = tuple(Rt @ np.asarray(hp.Y))
            hp.Z = tuple(Rt @ np.asarray(hp.Z))
    return left, right


def build2(theta=60.0, cl=40.0, pc=(-0.05, 0.40), **kw):
    """Design in the LEFT glove's frame: the right glove turned theta from the left glove's finger
    direction toward its thumb side, its palm centre at pc (left-local, from the left wrist, units of
    the glove size); cl turns the whole clasp (the left glove clockwise from upright)."""
    a = math.radians(cl)
    YL = np.array([math.sin(a), math.cos(a)])
    XL = np.array([-math.cos(a), math.sin(a)])
    dxy = pc[0] * XL + (pc[1] - PALM_CY) * YL
    return build(cl=cl, cr=theta - cl, d=(float(dxy[0]), float(dxy[1])), **kw)


def clearances(left, right):
    """name -> worst clearance (x size) of every chain against the other glove (and thumb vs own palm)."""
    out = {}
    for tag, hand, other in (("L", left, right), ("R", right, left)):
        for name in ("index", "middle", "pinky", "thumb"):
            c = chain_clearances(hand, name, other, 1 if name == "thumb" else 0)
            worst_key = min(c, key=c.get)
            out[f"{tag}.{name}"] = (round(c[worst_key] / hand.s, 3), worst_key)
    # the palms / cuffs against the other glove's chains are covered by the chain checks; palm vs palm:
    return out


def local(pose, P):
    M = pose.frame()
    return ((np.asarray(P, float) - np.asarray(pose.O, float)) @ M) / pose.s


def rx(deg):
    t = math.radians(deg); c, s_ = math.cos(t), math.sin(t)
    return np.array([[1, 0, 0], [0, c, -s_], [0, s_, c]])


def ry(deg):
    t = math.radians(deg); c, s_ = math.cos(t), math.sin(t)
    return np.array([[c, 0, s_], [0, 1, 0], [-s_, 0, c]])


VIEWS = [("camera", np.eye(3)), ("from above", rx(80)), ("from the left", ry(80)), ("from behind", ry(180))]
VIEWS2 = [("camera", np.eye(3)), ("cam right 25", ry(-25)), ("right 25 + above 15", rx(15) @ ry(-25)), ("from above", rx(80))]
