"""
The clasp, placed in the left glove's frame and then closed by contact:

  * the right glove lies across the left palm, turned theta from the left glove's fingers toward its
    thumb side, so its fingers run toward the left glove's thumb side and its pinky edge crosses the
    left fingers near their bases;
  * the right fingers lie on the left palm and curl round whatever edge they reach;
  * the left fingers run under the right glove's pinky edge and curl over it;
  * the left thumb comes down over the right fingers and curls round them;
  * the right thumb lies across the left heel and curls round it.
"""
from __future__ import annotations

import math

from design import build2
from pose3d import close_chain, close_coupled, settle_scan, wrap_finger, _flex_of, _set_flex


def clasp2(theta=75.0, cl=45.0, pc=(-0.2, 0.55), s=0.55,
           rf_spread=(0.0, 0.0, 0.0), lf_spread=(0.0, 0.0, 0.0),
           right_max=(70.0, 90.0, 80.0), left_max=(30.0, 75.0, 65.0),
           lt_beta=30.0, lt_from=85.0, lt_to=-5.0, lt_w=(0.2, 1.0, 0.9), lt_max=(25.0, 75.0, 70.0),
           rt_beta=95.0, rt_from=-85.0, rt_to=40.0, rt_w=(0.2, 1.0, 0.9), rt_max=(25.0, 75.0, 70.0),
           left_wrap=False, right_wrap=True, relax=0.0, order="right_first", lt_fixed=None, rt_fixed=None, lt_dz=0.0, lf_fixed=None, rf_fixed=None, lt_dy=0.0):
    lf = {n: (sp, 0.0, 4.0, 4.0) for n, sp in zip(("index", "middle", "pinky"), lf_spread)}
    rf = {n: (sp, 0.0, 4.0, 4.0) for n, sp in zip(("index", "middle", "pinky"), rf_spread)}
    left, right = build2(theta=theta, cl=cl, pc=pc, s=s, lf=lf, rf=rf,
                         lt=(lt_beta, lt_from, 0.0, 4.0, 4.0), rt=(rt_beta, rt_from, 0.0, 4.0, 4.0))

    left.tb_dz = lt_dz
    left.tb_dy = lt_dy

    def fingers(hand, other, wrap, mx):
        for name in ("index", "middle", "pinky"):
            if wrap:
                wrap_finger(hand, name, other, max_flex=mx)
            else:
                close_chain(hand, name, other, max_flex=mx)

    if rf_fixed is not None:
        right.fingers = {k: tuple(v) for k, v in rf_fixed.items()}
    if lf_fixed is not None:
        left.fingers = {k: tuple(v) for k, v in lf_fixed.items()}
    if order == "right_first":
        if rf_fixed is None:
            fingers(right, left, right_wrap, right_max)
        if lf_fixed is None:
            fingers(left, right, left_wrap, left_max)
    else:
        if lf_fixed is None:
            fingers(left, right, left_wrap, left_max)
        if rf_fixed is None:
            fingers(right, left, right_wrap, right_max)
    if lt_fixed is not None:
        left.thumb = tuple(lt_fixed)
    else:
        settle_scan(left, right, lt_from, lt_to, skip=("thumb",))
        close_coupled(left, "thumb", right, lt_w, lt_max, first=1, skip=("thumb",))
    if rt_fixed is not None:
        right.thumb = tuple(rt_fixed)
    else:
        settle_scan(right, left, rt_from, rt_to)
        close_coupled(right, "thumb", left, rt_w, rt_max, first=1)
    if relax:
        for hand in (left, right):
            for name in ("index", "middle", "pinky", "thumb"):
                _set_flex(hand, name, [max(0.0, f - relax) for f in _flex_of(hand, name)])
    return left, right
