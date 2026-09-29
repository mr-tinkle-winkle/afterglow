"""
Tests for the Advanced Editor's model, editing operations and undo.
No media or GUI needed.  Run:  python3 tests/test_nle_ops.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from afterglow.nle import ops
from afterglow.nle.history import History
from afterglow.nle.model import Project, default_project

FAILS = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def vid(path="a.mp4", dur=10.0):
    return {"path": path, "duration": dur, "has_video": True, "has_audio": True}


def aud(path="music.mp3", dur=10.0):
    return {"path": path, "duration": dur, "has_video": False, "has_audio": True}


def where(p, seg):
    return p.track_index(p.find_segment(seg.id)[0].id)


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---- default layout + buffer tracks --------------------------------------
p = default_project()
check(len(p.tracks) == 4 and all(t.is_empty() for t in p.tracks), "default project: 4 empty tracks")
v = ops.add_media(p, vid())
a = ops.add_media(p, aud())
check(where(p, v) == 1, "video lands on 2nd track from top")
check(where(p, a) == len(p.tracks) - 2, "audio-only lands on 2nd track from bottom")
check(p.tracks[0].is_empty() and p.tracks[-1].is_empty(), "top and bottom buffer tracks stay empty")

# ---- overlap kick + buffer growth ----------------------------------------
p = default_project()
v1 = ops.add_media(p, vid("a.mp4"), start=0, track_index=1)
v2 = ops.add_media(p, vid("b.mp4"), start=5, track_index=1)   # overlaps v1 (0-10)
check(p.find_segment(v2.id)[0] is not p.find_segment(v1.id)[0], "overlapping placement is kicked to another track")
check(approx(v2.start, 5), "kicked segment keeps its time position")
check(where(p, v2) < where(p, v1), "kicked to the preferred side (up) first")
check(p.tracks[0].is_empty() and p.tracks[-1].is_empty() and len(p.tracks) == 5,
      "landing on the top buffer creates a new empty track above it")

# ---- move ----------------------------------------------------------------
p = default_project()
s1 = ops.add_media(p, vid(dur=4), start=2, track_index=1)
s2 = ops.add_media(p, vid(dur=4), start=7, track_index=1)
ops.move_segments(p, [s1.id, s2.id], -5)
check(approx(s1.start, 0) and approx(s2.start, 5), "group move stops at t=0 as a group (keeps spacing)")
s1.locked = True
ops.move_segments(p, [s1.id], 3)
check(approx(s1.start, 0), "locked segment doesn't move")

# ---- split ---------------------------------------------------------------
p = default_project()
s = ops.add_media(p, vid(dur=10), start=2, track_index=1)
s.fade_in, s.fade_out = 1.0, 1.5
ops.set_keyframe(p, s.id, "volume", 1.0, 0.5)
ops.set_keyframe(p, s.id, "volume", 6.0, 1.5)
left, right = ops.split_segment(p, s.id, 5.0)
check(approx(left.duration, 3) and approx(right.start, 5) and approx(right.duration, 7), "split lengths")
check(approx(left.parts[0].src_out, 3) and approx(right.parts[0].src_in, 3), "split source ranges line up")
check(left.fade_in == 1.0 and left.fade_out == 0 and right.fade_in == 0 and right.fade_out == 1.5,
      "split keeps fade-in on the left half and fade-out on the right")
check([k.t for k in left.keyframes["volume"]] == [1.0] and [k.t for k in right.keyframes["volume"]] == [3.0],
      "keyframes are split and rebased")
check(ops.split_segment(p, left.id, 2.0) is None, "splitting exactly at an edge does nothing")

p = default_project()
x = ops.add_media(p, vid(dur=6), start=0, track_index=1)
y = ops.add_media(p, aud(dur=6), start=0)
new = ops.split_at(p, 3.0)
check(len(new) == 2, "S with nothing selected splits every segment under the playhead")

# ---- combine (inclusive merge) --------------------------------------------
p = default_project()
music = ops.add_media(p, aud(dur=10), start=0)
music.volume = 1.5
clip = ops.add_media(p, vid(dur=4), start=0, track_index=1)
merged = ops.combine(p, [music.id, clip.id])
check(approx(merged.duration, 10) and len(merged.parts) == 2, "long audio + short video -> one 10s segment, 2 parts")
check(where(p, merged) == 1, "merged segment lands on the upper track")
check(merged.split_markers() == [4.0], "visual split marker where the video ends (4.0s)")
audio_part = next(pt for pt in merged.parts if not pt.has_video)
check(approx(audio_part.gain, 1.5), "combine bakes each segment's volume into its part")
check(sum(1 for _ in p.all_segments()) == 1, "originals are removed")

p = default_project()
a1 = ops.add_media(p, vid(dur=2), start=0, track_index=1)
a2 = ops.add_media(p, vid(dur=2), start=5, track_index=1)
try:
    ops.combine(p, [a1.id, a2.id]); ok = False
except ops.OpError:
    ok = True
check(ok, "combine refuses segments that aren't touching")
a3 = ops.add_media(p, vid(dur=3), start=2, track_index=1)   # touches a1's end, a2's start
m = ops.combine(p, [a1.id, a2.id, a3.id])
check(approx(m.start, 0) and approx(m.duration, 7) and m.split_markers() == [2.0, 5.0],
      "same-track adjacent segments combine, split markers at the joins")

# ---- gaps / ripple ---------------------------------------------------------
p = default_project()
g1 = ops.add_media(p, vid(dur=2), start=0, track_index=1)
g2 = ops.add_media(p, vid(dur=2), start=5, track_index=1)
tid = p.find_segment(g1.id)[0].id
check(ops.gap_at(p, tid, 3.0) == (2.0, 5.0), "gap detected between two segments")
check(ops.gap_at(p, tid, 1.0) is None and ops.gap_at(p, tid, 9.0) is None, "no gap inside a segment or after the last")
closed = ops.close_gap(p, tid, 3.0)
check(approx(closed, 3) and approx(g2.start, 2), "trash-can gap delete closes the gap")

p = default_project()
r1 = ops.add_media(p, vid(dur=2), start=0, track_index=1)
r2 = ops.add_media(p, vid(dur=3), start=2, track_index=1)
r3 = ops.add_media(p, vid(dur=2), start=5, track_index=1)
ops.ripple_delete(p, [r2.id])
check(approx(r3.start, 2) and p.find_segment(r2.id)[1] is None, "ripple delete removes and closes the space")

# ---- edge trims --------------------------------------------------------------
p = default_project()
t1 = ops.add_media(p, vid(dur=10), start=0, track_index=1)
t2 = ops.add_media(p, vid(dur=10), start=12, track_index=1)
ops.trim_start(p, t2.id, 14)
check(approx(t2.start, 14) and approx(t2.parts[0].src_in, 2) and approx(t2.end, 22), "left-edge trim cuts the start")
ops.trim_start(p, t2.id, 5)
check(approx(t2.start, 12) and approx(t2.parts[0].src_in, 0), "left-edge reveal stops at the source start")
ops.trim_end(p, t1.id, 20)
check(approx(t1.end, 10), "right-edge reveal stops at the source's end")
ops.trim_end(p, t1.id, 6)
check(approx(t1.end, 6) and approx(t1.parts[0].src_out, 6), "right-edge trim cuts the end")
ops.trim_end(p, t1.id, 30)
check(approx(t1.end, 10), "right-edge reveal again, capped by source length (and the next segment)")

# ---- toggles / properties ----------------------------------------------------
p = default_project()
q1 = ops.add_media(p, vid(dur=2), start=0, track_index=1)
q2 = ops.add_media(p, vid(dur=2), start=3, track_index=1)
q1.muted = True
ops.toggle(p, [q1.id, q2.id], "muted")
check(q1.muted and q2.muted, "mixed mute selection -> all muted (consistent)")
ops.toggle(p, [q1.id, q2.id], "muted")
check(not q1.muted and not q2.muted, "toggle again -> all unmuted")
ops.toggle(p, [q1.id], "locked")
ops.set_volume(p, [q1.id, q2.id], 5.0)
check(q1.volume == 1.0 and q2.volume == 2.0, "locked segment ignores volume change; volume clamps to 200%")
ops.toggle(p, [q1.id], "locked")
check(not q1.locked, "L unlocks a locked segment")
ops.set_fades(p, [q2.id], fade_in=1.5, fade_out=1.5)
check(approx(q2.fade_in + q2.fade_out, q2.duration), "fade in + out can't exceed the segment")

# ---- speed -------------------------------------------------------------------
p = default_project()
sp = ops.add_media(p, vid(dur=10), start=0, track_index=1)
ops.set_speed(p, sp.id, 2.0)
check(approx(sp.duration, 5) and approx(sp.parts[0].src_out, 10), "2x speed halves the length, same source range")
blocker = ops.add_media(p, vid(dur=2), start=8, track_index=1)
ops.set_speed(p, sp.id, 0.5)
check(approx(sp.end, 8) and sp.end <= blocker.start + 1e-9, "slowing down is limited so it doesn't overlap the next segment")

# ---- clipboard / duplicate / detach ------------------------------------------
p = default_project()
c1 = ops.add_media(p, vid(dur=2), start=1, track_index=1)
c1.locked = True
clip = ops.copy_segments(p, [c1.id])
ids = ops.paste(p, clip, at=10)
pasted = p.find_segment(ids[0])[1]
check(approx(pasted.start, 10) and not pasted.locked and pasted.id != c1.id, "paste at playhead, unlocked, new id")
dup = ops.duplicate(p, [pasted.id])
check(approx(p.find_segment(dup[0])[1].start, 12), "duplicate lands right after the original")
p = default_project()
d = ops.add_media(p, vid(dur=4), start=0, track_index=1)
au = ops.detach_audio(p, d.id)
check(au is not None and not d.has_audio and d.has_video and au.has_audio and not au.has_video
      and where(p, au) > where(p, d), "detach audio -> video-only + audio-only segment below")

# ---- snapping ---------------------------------------------------------------
p = default_project()
n1 = ops.add_media(p, vid(dur=4), start=0, track_index=1)
n2 = ops.add_media(p, vid(dur=2), start=10, track_index=1)
dt, target = ops.snap_move(p, [n2.id], -5.9, playhead=20, threshold=0.2)
check(approx(10 + dt, 4) and target == 4, "moving segment's start snaps to another segment's end")
dt, target = ops.snap_move(p, [n2.id], 7.95, playhead=20, threshold=0.2)
check(approx(12 + dt, 20) and target == 20, "moving segment's end snaps to the playhead")
check(ops.snap_playhead(p, 4.05, 0.2) == 4.0 and ops.snap_playhead(p, 4.15, 0.2) == 4.15,
      "playhead snaps to edges, but more weakly than segments")

# ---- tracks ----------------------------------------------------------------
p = default_project()
m1 = ops.add_media(p, vid(dur=2), start=0, track_index=1)
ops.move_track(p, 1, 2)
check(where(p, m1) == 2, "track reorder moves its segments with it")

# ---- history ---------------------------------------------------------------
p = default_project()
h = History(p)
h.perform("Add", lambda pr: ops.add_media(pr, vid(dur=10), start=0, track_index=1))
seg_id = next(p.all_segments()).id
h.perform("Split", lambda pr: ops.split_at(pr, 4.0))
check(sum(1 for _ in p.all_segments()) == 2, "split recorded")
h.undo()
check(sum(1 for _ in p.all_segments()) == 1, "undo split")
h.redo()
check(sum(1 for _ in p.all_segments()) == 2, "redo split")
h.begin("Move")
for i in range(20):
    ops.move_segments(p, [seg_id], 0.1)
h.end()
check(h.undo_label() == "Move", "a 20-step drag is one undo step")
h.undo()
check(approx(p.find_segment(seg_id)[1].start, 0), "undoing the drag restores the start")
before = len(h._undo)
h.perform("Nothing", lambda pr: None)
check(len(h._undo) == before, "no-op edits don't create undo steps")
count_before = sum(1 for _ in p.all_segments())
snapshot_before = p.to_dict()
try:
    h.perform("Bad", lambda pr: (ops.split_at(pr, 1.0), ops.combine(pr, [seg_id])))
except ops.OpError:
    pass
check(p.to_dict() == snapshot_before, f"a failing edit is rolled back entirely (still {count_before} segments)")
h.mark_saved()
check(not h.dirty, "clean after save")
h.perform("Mute", lambda pr: ops.toggle(pr, [seg_id], "muted"))
check(h.dirty, "dirty after an edit")
h.undo()
check(not h.dirty, "undo back to the saved state -> clean again")

# ---- serialization ---------------------------------------------------------
p = default_project()
s = ops.add_media(p, vid(dur=8), start=1, track_index=1)
ops.add_media(p, aud(dur=8), start=0)
ops.set_keyframe(p, s.id, "scale", 1.0, 1.2)
ops.set_transform(p, [s.id], x=0.1, rotation=15)
s.transition_in = None
blob = json.dumps(p.to_dict())
p2 = Project.from_dict(json.loads(blob))
check(p2.to_dict() == p.to_dict(), "JSON round-trip is exact")
d = json.loads(blob); d["tracks"][1]["segments"][0]["future_field"] = 123
check(Project.from_dict(d).to_dict() == p.to_dict(), "unknown (newer) fields are ignored")

print("\nALL PASS" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
