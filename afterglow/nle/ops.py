"""
Editing operations on a Project. Every function here mutates the
project in place and is meant to be called through History.perform()
(or inside a begin()/end() gesture) so it's undoable.

Rules from the spec implemented here:
- Buffer tracks: the top and bottom tracks are kept empty. If anything
  lands on either, a new empty track is created past it (above the top,
  below the bottom). normalize_tracks() enforces this after every
  placement.
- Overlap: a segment placed where it would overlap another is "kicked"
  to the nearest track where its whole time range is free, on its
  respective side (prefer=-1 = upward, +1 = downward; ties go to the
  preferred side). If no track is free, a new track is created on that
  side.
- Locked segments can't be moved, trimmed, split, combined, deleted, or
  have properties changed. Operations silently skip them.
- Combine (C): segments that touch or overlap in time merge into one
  segment ("inclusive merge"). Each segment's volume/mute/visibility is
  baked into its parts first, so the merge never changes the output.
- Snapping: snap_move() / snap_time() -- segment edges snap to other
  segments' edges and the playhead; the playhead snaps weakly to edges.
"""
from __future__ import annotations

import copy
import os
from dataclasses import replace

from .model import (
    EPS, KIND_AV, MAX_SPEED, MAX_VOLUME, MIN_SPEED, MIN_VOLUME, Keyframe,
    Part, Project, Segment, Track, empty_track, new_id,
)

MIN_TRACKS = 4


class OpError(ValueError):
    """An edit that can't be done as asked (shown to the user)."""


# =========================================================================
# tracks
# =========================================================================

def normalize_tracks(project: Project) -> None:
    """Keep an empty buffer track at the top and bottom, and at least
    MIN_TRACKS tracks overall."""
    if not project.tracks:
        project.tracks = [empty_track() for _ in range(MIN_TRACKS)]
    if not project.tracks[0].is_empty():
        project.tracks.insert(0, empty_track())
    if not project.tracks[-1].is_empty():
        project.tracks.append(empty_track())
    while len(project.tracks) < MIN_TRACKS:
        project.tracks.insert(len(project.tracks) - 1, empty_track())


def add_track(project: Project, index: int) -> Track:
    t = empty_track()
    project.tracks.insert(max(0, min(index, len(project.tracks))), t)
    normalize_tracks(project)
    return t


def remove_track(project: Project, track_id: str) -> None:
    i = project.track_index(track_id)
    if any(s.locked for s in project.tracks[i].segments):
        raise OpError("That track has locked segments.")
    del project.tracks[i]
    normalize_tracks(project)


def move_track(project: Project, from_index: int, to_index: int) -> None:
    """Reorder via the 3-line handle."""
    n = len(project.tracks)
    if not (0 <= from_index < n):
        raise OpError("No such track.")
    to_index = max(0, min(to_index, n - 1))
    t = project.tracks.pop(from_index)
    project.tracks.insert(to_index, t)
    normalize_tracks(project)


def set_track_collapsed(project: Project, track_id: str, collapsed: bool) -> None:
    project.tracks[project.track_index(track_id)].collapsed = collapsed


# =========================================================================
# placement
# =========================================================================

def _track_free(track: Track, start: float, end: float, ignore: set[str]) -> bool:
    return track.free_between(start, end, ignore)


def place(project: Project, seg: Segment, track_index: int, start: float, prefer: int = -1) -> Track:
    """Put seg (not currently on any track, or already removed from its
    track) at `start` on `track_index`, kicking it to the nearest fully
    free track if it would overlap. Returns the track it landed on."""
    seg.start = max(0.0, start)
    n = len(project.tracks)
    track_index = max(0, min(track_index, n - 1))
    end = seg.start + seg.duration
    order = [track_index]
    for d in range(1, n):
        first, second = track_index + d * prefer, track_index - d * prefer
        order += [i for i in (first, second) if 0 <= i < n]
    for i in order:
        if _track_free(project.tracks[i], seg.start, end, {seg.id}):
            project.tracks[i].segments.append(seg)
            normalize_tracks(project)
            return _track_of(project, seg.id)
    # Nothing free anywhere: new track on the preferred side.
    new = empty_track()
    if prefer < 0:
        project.tracks.insert(0, new)
    else:
        project.tracks.append(new)
    new.segments.append(seg)
    normalize_tracks(project)
    return _track_of(project, seg.id)


def _track_of(project: Project, seg_id: str) -> Track:
    t, _ = project.find_segment(seg_id)
    return t


def _detach(project: Project, seg_id: str) -> "tuple[int, Segment]":
    for i, t in enumerate(project.tracks):
        for s in t.segments:
            if s.id == seg_id:
                t.segments.remove(s)
                return i, s
    raise OpError("No such segment.")


def add_media(project: Project, info: dict, start: "float | None" = None,
              track_index: "int | None" = None) -> Segment:
    """Add a media file as a new segment. `info` comes from
    media.probe(): {path, duration, has_video, has_audio, width, height, fps}.
    Default track: 2nd from top if it has video, 2nd from bottom if
    audio-only (the spec's default video/audio lanes)."""
    if info.get("duration", 0) <= 0:
        raise OpError(f"Can't read a duration from {info.get('path')}.")
    part = Part(kind=info.get("kind", KIND_AV), source=info["path"], src_in=0.0,
                src_out=info["duration"], source_duration=info["duration"],
                has_video=info.get("has_video", True), has_audio=info.get("has_audio", True))
    seg = Segment(start=0.0, parts=[part], name=os.path.basename(info["path"]))
    if track_index is None:
        track_index = 1 if part.has_video else max(1, len(project.tracks) - 2)
    if start is None:
        start = max((s.end for s in project.tracks[track_index].segments), default=0.0)
    place(project, seg, track_index, start, prefer=-1 if part.has_video else 1)
    return seg


def move_segments(project: Project, ids: list[str], dt: float, dtrack: int = 0, prefer: int = -1) -> None:
    """Move segments by dt seconds and dtrack tracks, keeping their
    relative arrangement. Locked segments stay put. The group is never
    moved before t=0 (the whole group stops at 0, not each segment)."""
    movable = []
    for sid in ids:
        _, s = project.find_segment(sid)
        if s is not None and not s.locked:
            movable.append(s)
    if not movable:
        return
    earliest = min(s.start for s in movable)
    dt = max(dt, -earliest)
    # Detach all first so group members don't collide with each other.
    detached = []
    for s in sorted(movable, key=lambda s: s.start):
        i, seg = _detach(project, s.id)
        detached.append((i, seg))
    for i, seg in detached:
        place(project, seg, i + dtrack, seg.start + dt, prefer=prefer)


# =========================================================================
# split / combine
# =========================================================================

def _split_part(p: Part, local_t: float) -> "tuple[Part | None, Part | None]":
    """Split a part at segment-local time local_t -> (left, right) where
    right's offset is still in the ORIGINAL segment's local time."""
    if local_t <= p.offset + EPS:
        return None, p
    if local_t >= p.end - EPS:
        return p, None
    cut_src = p.source_time(local_t)
    left = replace(p, src_out=cut_src)
    right = replace(p, src_in=cut_src, offset=local_t)
    return left, right


def _split_keyframes(kfs: dict, local_t: float) -> "tuple[dict, dict]":
    left, right = {}, {}
    for name, lst in kfs.items():
        l = [copy.copy(k) for k in lst if k.t <= local_t + EPS]
        r = [Keyframe(k.t - local_t, k.value, k.easing) for k in lst if k.t >= local_t - EPS]
        if l:
            left[name] = l
        if r:
            right[name] = r
    return left, right


def split_segment(project: Project, seg_id: str, t: float) -> "tuple[Segment, Segment] | None":
    """Split one segment at timeline time t. Returns (left, right), or
    None if t isn't strictly inside it or it's locked."""
    track, seg = project.find_segment(seg_id)
    if seg is None or seg.locked:
        return None
    if not (seg.start + EPS < t < seg.end - EPS):
        return None
    local = t - seg.start
    left_parts, right_parts = [], []
    for p in seg.parts:
        l, r = _split_part(p, local)
        if l is not None:
            left_parts.append(l)
        if r is not None:
            right_parts.append(replace(r, offset=r.offset - local))
    kl, kr = _split_keyframes(seg.keyframes, local)
    right = replace(seg, id=new_id(), start=t, parts=right_parts, fade_in=0.0,
                    keyframes=kr, transition_in=None, transform=copy.deepcopy(seg.transform))
    seg.parts = left_parts
    seg.fade_out = 0.0
    seg.keyframes = kl
    seg.fade_in = min(seg.fade_in, seg.duration)
    right.fade_out = min(right.fade_out, right.duration)
    track.segments.append(right)
    return seg, right


def split_at(project: Project, t: float, ids: "list[str] | None" = None) -> list[str]:
    """S: split the given segments (or, if none given, every segment
    under t on every track) at t. Returns ids of the new right halves."""
    targets = ids if ids else [s.id for s in project.all_segments() if s.covers(t)]
    out = []
    for sid in list(targets):
        r = split_segment(project, sid, t)
        if r is not None:
            out.append(r[1].id)
    return out


def _bake(seg: Segment) -> list[Part]:
    """Parts of seg with the segment's volume/mute/visibility folded in."""
    gain = 0.0 if seg.muted else seg.volume
    return [replace(p, gain=p.gain * gain, visible=p.visible and seg.visible) for p in seg.parts]


def combine(project: Project, ids: list[str]) -> Segment:
    """C: merge touching/overlapping segments into one (inclusive
    merge). The result lives on the highest (top-most) of their tracks,
    spans the union of their ranges, and keeps every part at its
    original place in time -- e.g. long audio + short video = video then
    black with the audio continuing."""
    segs = []
    for sid in ids:
        _, s = project.find_segment(sid)
        if s is None:
            continue
        if s.locked:
            raise OpError("Can't combine a locked segment.")
        segs.append(s)
    if len(segs) < 2:
        raise OpError("Select at least two segments to combine.")
    segs.sort(key=lambda s: s.start)
    reach = segs[0].end
    for s in segs[1:]:
        if s.start > reach + EPS:
            raise OpError("Segments must be touching to combine.")
        reach = max(reach, s.end)
    top_index = min(project.track_index(_track_of(project, s.id).id) for s in segs)
    new_start = segs[0].start
    parts = []
    for s in segs:
        for p in _bake(s):
            parts.append(replace(p, offset=p.offset + (s.start - new_start)))
    first = segs[0]
    merged = Segment(start=new_start, parts=parts, name=first.name,
                     fade_in=first.fade_in, fade_out=max(segs, key=lambda s: s.end).fade_out,
                     transform=copy.deepcopy(first.transform), transition_in=first.transition_in)
    for s in segs:
        _detach(project, s.id)
    place(project, merged, top_index, new_start)
    return merged


# =========================================================================
# delete / gaps
# =========================================================================

def delete(project: Project, ids: list[str]) -> int:
    n = 0
    for sid in ids:
        _, s = project.find_segment(sid)
        if s is not None and not s.locked:
            _detach(project, sid)
            n += 1
    normalize_tracks(project)
    return n


def gap_at(project: Project, track_id: str, t: float) -> "tuple[float, float] | None":
    """The empty span on a track containing time t, bounded by segments
    on both sides (a leading gap before the first segment counts too).
    None if t is inside a segment or after the last one."""
    track = project.tracks[project.track_index(track_id)]
    segs = track.sorted_segments()
    prev_end = 0.0
    for s in segs:
        if t < s.start - EPS:
            return (prev_end, s.start) if s.start - prev_end > EPS and t >= prev_end - EPS else None
        if s.covers(t):
            return None
        prev_end = max(prev_end, s.end)
    return None


def close_gap(project: Project, track_id: str, t: float) -> float:
    """Ripple-delete the gap at t on a track (the hover trash can):
    shift everything after the gap on that track left to close it.
    Returns the amount closed (0 if nothing to close)."""
    gap = gap_at(project, track_id, t)
    if gap is None:
        return 0.0
    g0, g1 = gap
    track = project.tracks[project.track_index(track_id)]
    after = [s for s in track.segments if s.start >= g1 - EPS]
    if any(s.locked for s in after):
        raise OpError("A locked segment after the gap prevents closing it.")
    shift = g1 - g0
    for s in after:
        s.start -= shift
    return shift


def ripple_delete(project: Project, ids: list[str]) -> None:
    """Shift+Delete: delete segments and close the space they leave on
    their own tracks."""
    for sid in ids:
        track, s = project.find_segment(sid)
        if s is None or s.locked:
            continue
        start, dur = s.start, s.duration
        track.segments.remove(s)
        later = [o for o in track.segments if o.start >= start + dur - EPS]
        if any(o.locked for o in later):
            continue  # leave the gap rather than move a locked segment
        # Only close as much as is actually empty now (other segments may
        # already sit inside the removed range).
        earlier_end = max((o.end for o in track.segments if o.start < start + dur - EPS), default=0.0)
        shift = (start + dur) - max(start, earlier_end)
        for o in later:
            o.start -= shift
    normalize_tracks(project)


# =========================================================================
# edge trimming
# =========================================================================

def trim_start(project: Project, seg_id: str, new_start: float) -> None:
    """Drag a segment's LEFT edge. Moving right removes material from the
    start; moving left reveals more source (up to the first part's
    src_in reaching 0, and never overlapping the previous segment)."""
    track, seg = project.find_segment(seg_id)
    if seg is None or seg.locked:
        return
    new_start = min(new_start, seg.end - 1.0 / max(project.fps, 1))
    # Can't move left past the previous segment on this track.
    prev_end = max((o.end for o in track.segments if o.id != seg.id and o.end <= seg.start + EPS), default=0.0)
    new_start = max(new_start, prev_end, 0.0)
    delta = new_start - seg.start          # >0 cuts, <0 extends
    if abs(delta) < EPS:
        return
    if delta > 0:
        local = delta
        parts = []
        for p in seg.parts:
            _l, r = _split_part(p, local)
            if r is not None:
                parts.append(replace(r, offset=r.offset - local))
        seg.parts = parts
        seg.keyframes = _split_keyframes(seg.keyframes, local)[1]
    else:
        ext = -delta
        first = min(seg.parts, key=lambda p: p.offset)
        if first.offset > EPS:
            ext = 0.0                      # leading gap inside segment: nothing to reveal
        elif first.kind == KIND_AV and first.source_duration > 0:
            ext = min(ext, first.src_in / first.speed)
        if ext <= EPS:
            return
        for p in seg.parts:
            if p is first:
                p.src_in -= ext * p.speed
            else:
                p.offset += ext
        for lst in seg.keyframes.values():
            for k in lst:
                k.t += ext
        delta = -ext
    seg.start += delta
    seg.fade_in = min(seg.fade_in, seg.duration)
    seg.fade_out = min(seg.fade_out, seg.duration)


def trim_end(project: Project, seg_id: str, new_end: float) -> None:
    """Drag a segment's RIGHT edge (cut or reveal, bounded by the next
    segment on the track and the source's length)."""
    track, seg = project.find_segment(seg_id)
    if seg is None or seg.locked:
        return
    new_end = max(new_end, seg.start + 1.0 / max(project.fps, 1))
    next_start = min((o.start for o in track.segments if o.id != seg.id and o.start >= seg.end - EPS),
                     default=float("inf"))
    new_end = min(new_end, next_start)
    local_end = new_end - seg.start
    if abs(local_end - seg.duration) < EPS:
        return
    if local_end < seg.duration:
        parts = []
        for p in seg.parts:
            l, _r = _split_part(p, local_end)
            if l is not None:
                parts.append(l)
        seg.parts = parts
        seg.keyframes = _split_keyframes(seg.keyframes, local_end)[0]
    else:
        last = max(seg.parts, key=lambda p: p.end)
        want = local_end - last.end
        if last.kind == KIND_AV and last.source_duration > 0:
            want = min(want, (last.source_duration - last.src_out) / last.speed)
        if want > EPS:
            last.src_out += want * last.speed
    seg.fade_in = min(seg.fade_in, seg.duration)
    seg.fade_out = min(seg.fade_out, seg.duration)


# =========================================================================
# properties
# =========================================================================

def _editable(project: Project, ids: list[str]):
    for sid in ids:
        _, s = project.find_segment(sid)
        if s is not None and not s.locked:
            yield s


def set_volume(project: Project, ids: list[str], volume: float) -> None:
    for s in _editable(project, ids):
        s.volume = max(MIN_VOLUME, min(MAX_VOLUME, volume))


def toggle(project: Project, ids: list[str], attr: str) -> None:
    """M (muted), V (visible), L (locked). Toggles as a group: if any
    selected segment is off, all turn on; otherwise all turn off --
    so a mixed selection always ends up consistent. Lock can be toggled
    on locked segments (that's how you unlock); the others can't."""
    if attr not in ("muted", "visible", "locked"):
        raise OpError(attr)
    segs = [project.find_segment(i)[1] for i in ids]
    segs = [s for s in segs if s is not None and (attr == "locked" or not s.locked)]
    if not segs:
        return
    new_value = not all(getattr(s, attr) for s in segs)
    for s in segs:
        setattr(s, attr, new_value)


def set_fades(project: Project, ids: list[str], fade_in: "float | None" = None,
              fade_out: "float | None" = None) -> None:
    for s in _editable(project, ids):
        if fade_in is not None:
            s.fade_in = max(0.0, min(fade_in, s.duration))
        if fade_out is not None:
            s.fade_out = max(0.0, min(fade_out, s.duration))
        # Fades can't overlap past each other.
        total = s.fade_in + s.fade_out
        if total > s.duration and total > 0:
            k = s.duration / total
            s.fade_in *= k
            s.fade_out *= k


def set_speed(project: Project, seg_id: str, speed: float) -> None:
    """Per-segment speed. The segment keeps its start; its length
    changes. If it would grow into the next segment on its track, the
    speed is limited so it just fits."""
    track, seg = project.find_segment(seg_id)
    if seg is None or seg.locked:
        return
    speed = max(MIN_SPEED, min(MAX_SPEED, speed))
    next_start = min((o.start for o in track.segments if o.id != seg.id and o.start >= seg.end - EPS),
                     default=float("inf"))
    room = next_start - seg.start
    # Current length at speed 1 for this segment's parts (relative scale).
    old = seg.parts[0].speed if seg.parts else 1.0
    factor = old / speed               # new_duration = old_duration * factor
    if seg.duration * factor > room + EPS:
        factor = room / seg.duration
        speed = old / factor
    for p in seg.parts:
        p.speed = p.speed * (speed / old)
        p.offset = p.offset * factor
    for lst in seg.keyframes.values():
        for k in lst:
            k.t *= factor
    seg.fade_in = min(seg.fade_in * factor, seg.duration)
    seg.fade_out = min(seg.fade_out * factor, seg.duration)


def set_transform(project: Project, ids: list[str], **values) -> None:
    for s in _editable(project, ids):
        for k, v in values.items():
            if not hasattr(s.transform, k):
                raise OpError(k)
            setattr(s.transform, k, float(v))


def set_zoom(project: Project, ids: list[str], amount: float, zoom_in: float, zoom_out: float) -> None:
    for s in _editable(project, ids):
        s.zoom_amount = max(1.0, amount)
        s.zoom_in = max(0.0, min(zoom_in, s.duration))
        s.zoom_out = max(0.0, min(zoom_out, s.duration - s.zoom_in))


def set_keyframe(project: Project, seg_id: str, prop: str, t: float, value: float, easing: str = "linear") -> None:
    _, s = project.find_segment(seg_id)
    if s is None or s.locked:
        return
    lst = s.keyframes.setdefault(prop, [])
    for k in lst:
        if abs(k.t - t) < 1e-3:
            k.value, k.easing = value, easing
            break
    else:
        lst.append(Keyframe(t, value, easing))
        lst.sort(key=lambda k: k.t)


def remove_keyframe(project: Project, seg_id: str, prop: str, t: float) -> None:
    _, s = project.find_segment(seg_id)
    if s is None or s.locked or prop not in s.keyframes:
        return
    s.keyframes[prop] = [k for k in s.keyframes[prop] if abs(k.t - t) >= 1e-3]
    if not s.keyframes[prop]:
        del s.keyframes[prop]


# =========================================================================
# clipboard / duplicate / detach
# =========================================================================

def copy_segments(project: Project, ids: list[str]) -> dict:
    """Ctrl+C. Returns a clipboard payload (plain data), remembering each
    segment's track index and time relative to the earliest one."""
    items = []
    for sid in ids:
        track, s = project.find_segment(sid)
        if s is not None:
            items.append((project.track_index(track.id), s))
    if not items:
        return {}
    t0 = min(s.start for _, s in items)
    return {"items": [{"track": ti, "dt": s.start - t0, "segment": copy.deepcopy(s)} for ti, s in items]}


def paste(project: Project, clipboard: dict, at: float, track_index: "int | None" = None) -> list[str]:
    """Ctrl+V at the playhead. Pasted copies are unlocked. Returns new ids."""
    items = (clipboard or {}).get("items", [])
    if not items:
        return []
    base_track = min(i["track"] for i in items)
    out = []
    for it in items:
        seg = copy.deepcopy(it["segment"])
        seg.id = new_id()
        seg.locked = False
        ti = it["track"] if track_index is None else track_index + (it["track"] - base_track)
        place(project, seg, ti, at + it["dt"], prefer=1)
        out.append(seg.id)
    return out


def duplicate(project: Project, ids: list[str]) -> list[str]:
    """Ctrl+D: copies placed right after the selection's end."""
    clip = copy_segments(project, ids)
    if not clip:
        return []
    end = max(project.find_segment(i)[1].end for i in ids if project.find_segment(i)[1] is not None)
    return paste(project, clip, end)


def detach_audio(project: Project, seg_id: str) -> "Segment | None":
    """Split a video's audio off into its own segment on the nearest
    free track below (same timing), leaving the original video-only."""
    track, seg = project.find_segment(seg_id)
    if seg is None or seg.locked or not any(p.has_video and p.has_audio for p in seg.parts):
        return None
    audio_parts = [replace(p, has_video=False) for p in seg.parts if p.has_audio]
    for p in seg.parts:
        if p.has_video:
            p.has_audio = False
    seg.parts = [p for p in seg.parts if p.has_video or p.has_audio]
    audio = Segment(start=seg.start, parts=audio_parts, name=(seg.name + " (audio)").strip(),
                    volume=seg.volume, muted=seg.muted, fade_in=seg.fade_in, fade_out=seg.fade_out)
    place(project, audio, project.track_index(track.id) + 1, seg.start, prefer=1)
    return audio


# =========================================================================
# snapping
# =========================================================================

def snap_time(t: float, targets: list[float], threshold: float) -> "tuple[float, float | None]":
    """Snap t to the nearest target within threshold. Returns
    (snapped_t, target or None)."""
    best, best_d = None, threshold
    for x in targets:
        d = abs(x - t)
        if d <= best_d:
            best, best_d = x, d
    return (best, best) if best is not None else (t, None)


def snap_move(project: Project, ids: list[str], dt: float, playhead: float, threshold: float) -> "tuple[float, float | None]":
    """Adjust a proposed move delta so the moving group's start or end
    snaps to another segment's edge or the playhead. Returns
    (adjusted_dt, snapped_to_time or None)."""
    moving = [project.find_segment(i)[1] for i in ids]
    moving = [s for s in moving if s is not None]
    if not moving:
        return dt, None
    ids_set = {s.id for s in moving}
    targets = project.edge_times(exclude=ids_set) + [playhead]
    best_dt, best_t, best_d = dt, None, threshold
    for s in moving:
        for edge in (s.start, s.end):
            proposed = edge + dt
            for x in targets:
                d = abs(x - proposed)
                if d <= best_d:
                    best_dt, best_t, best_d = dt + (x - proposed), x, d
    return best_dt, best_t


PLAYHEAD_SNAP_FRACTION = 0.4   # playhead snaps with less strength than segments do


def snap_playhead(project: Project, t: float, threshold: float) -> float:
    """The playhead snaps to segment edges, weakly."""
    return snap_time(t, project.edge_times(), threshold * PLAYHEAD_SNAP_FRACTION)[0]
