"""
Timeline data model for the Advanced Editor (a small track-based NLE).

Pure Python, no Qt: everything here can be built, edited, serialized
and unit-tested without a GUI. The renderer (render.py) and the
timeline UI read this model; edits go through ops.py so they're
undoable (history.py).

Vocabulary (matches the spec):
- Project: one edit. Has a canvas (width/height/fps) and tracks.
- Track: a horizontal lane. Tracks are listed TOP to BOTTOM. There is
  no video/audio track type -- anything can go on any track. When
  compositing video, higher tracks draw over lower ones.
- Segment: one block on a track (the thing the user moves, splits,
  locks, mutes...). A segment holds one or more Parts. Normally one;
  "combine" (C) merges touching segments into one segment with several
  parts ("inclusive merge"), and the part boundaries are drawn as
  clickable split markers.
- Part: a piece of a source file (or generated element) placed inside
  its segment at `offset`. Its own `src_in..src_out` range of the
  source plays at `speed`.

Time is float seconds everywhere. Comparisons use EPS so accumulated
float error from splits/moves never creates phantom 1-microsecond gaps
or overlaps.

Serialization: Project.to_dict()/from_dict() round-trip through JSON.
SCHEMA_VERSION is stored in every project; bump it and add a migration
in from_dict() whenever a field's meaning changes (new fields with
defaults need no migration -- from_dict() ignores unknown keys and
fills missing ones from the dataclass defaults).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, fields, asdict

SCHEMA_VERSION = 2
EPS = 1e-6

# Part kinds. "av" = a media file's video and/or audio (has_video /
# has_audio say which streams it actually has). The rest are
# generated/still elements added in the "additions" phase.
KIND_AV = "av"
KIND_IMAGE = "image"
KIND_GIF = "gif"
KIND_TEXT = "text"
PART_KINDS = (KIND_AV, KIND_IMAGE, KIND_GIF, KIND_TEXT)

MIN_VOLUME, MAX_VOLUME = 0.0, 2.0      # 0%..200%
MIN_SPEED, MAX_SPEED = 0.1, 16.0


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Transform:
    """Placement of a segment's picture on the canvas. Defaults = the
    source fitted (letterboxed) into the canvas, centered, unrotated,
    uncropped. x/y are the picture center's offset from the canvas
    center as a fraction of canvas width/height; scale multiplies the
    fitted size; crop_* trim that fraction off each edge of the source
    before fitting."""
    x: float = 0.0
    y: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0          # degrees, clockwise
    crop_left: float = 0.0
    crop_top: float = 0.0
    crop_right: float = 0.0
    crop_bottom: float = 0.0

    def is_identity(self) -> bool:
        return self == Transform()


@dataclass
class TextStyle:
    """For KIND_TEXT parts."""
    text: str = "Text"
    font_family: str = "Sans Serif"
    font_size: float = 0.08        # fraction of canvas height
    color: str = "#ffffff"
    outline_color: str = "#000000"
    outline_width: float = 0.0     # px at 1080p, scaled with canvas
    bold: bool = False
    italic: bool = False
    # Typewriter: characters appear over type_in seconds at the start and
    # are deleted over type_out seconds at the end (0 = off). type_cursor
    # adds a text cursor "|" after the text (blinking once fully typed).
    type_in: float = 0.0
    type_out: float = 0.0
    type_cursor: bool = False
    # Comic bubbles: "" (plain text) | "speech" | "thought". The tail's tip
    # (tail_x, tail_y) is an OFFSET from the bubble's own position (same
    # units as Transform.x/y: fractions of the canvas width/height), so a
    # bubble that is moved -- or keyframed to follow something -- takes its
    # tail along; the tip can still be dragged/keyframed on its own
    # ("tail_x"/"tail_y" keyframes are offsets too). Schema 1 stored it as
    # an absolute canvas position (converted in Project.from_dict).
    bubble: str = ""
    bubble_fill: str = "#ffffff"
    bubble_outline: str = "#000000"
    bubble_outline_width: float = 4.0     # px at 1080p
    tail_x: float = -0.1
    tail_y: float = 0.25
    bubble_fill_transparency: float = 0.0      # 0 = solid, 1 = invisible
    bubble_outline_transparency: float = 0.0
    # Bubble style (render.BUBBLE_VARIANTS): "" = neutral; speech: "spiky",
    # "whisper", "wiggly", "intercom"; thought: "wobbly", "dreamy", "jagged",
    # "electronic". bubble_animated keeps the style's effect moving (the
    # neutral thought cloud has a gentle wiggle too).
    bubble_variant: str = ""
    bubble_animated: bool = True
    bubble_anim_speed: float = 1.0       # multiplier for the animated effect
    # Name of the global text preset this element was made from / given
    # (nle/globals.py), "" = none. Updating that preset restyles it.
    global_preset: str = ""
    # A picture inside the text / bubble (a reaction image, an avatar...):
    # image_path "" = none; image_place = "above" | "below" | "left" |
    # "right" of the words; image_size = its height as a fraction of the
    # canvas height.
    image_path: str = ""
    image_place: str = "above"
    image_size: float = 0.18
    # "Grow" in/out (seconds, 0 = off): a bubble grows out of its tail tip
    # into place (a thought bubble sprouts its circles first, then puffs up
    # like a cloud), and shrinks back into the tip at the end.
    grow_in: float = 0.0
    grow_out: float = 0.0
    # "Delay" in/out (seconds, 0 = off): the text fades in word by word, and
    # within a word letter by letter left to right (out = the reverse).
    # When the bubble grows, the delay starts once it has grown.
    delay_in: float = 0.0
    delay_out: float = 0.0
    # Per-word Delay timing ("captions as they're said"): with delay_keyed on,
    # delay_word_times[i] is when word i starts to appear (seconds from the
    # element's start) or None; unkeyed words are spread evenly between the
    # nearest keyed ones (or the start/end of the text's time).
    delay_keyed: bool = False
    delay_word_times: list = field(default_factory=list)


@dataclass
class Part:
    kind: str = KIND_AV
    source: str = ""               # file path (empty for text)
    src_in: float = 0.0            # seconds into the source
    src_out: float = 0.0
    offset: float = 0.0            # seconds from the segment's start (timeline time)
    speed: float = 1.0
    has_video: bool = True
    has_audio: bool = True
    text: "TextStyle | None" = None
    # Full length of the source (seconds); how far an edge can be dragged
    # back out. 0 = unknown/unbounded (stills and text can be any length).
    source_duration: float = 0.0
    # Per-part gain and visibility. Combining segments with different
    # volume/mute/visibility bakes each one's state in here, so an
    # inclusive merge never changes how anything looks or sounds.
    gain: float = 1.0
    visible: bool = True

    @property
    def duration(self) -> float:
        """Length on the timeline (source length / speed)."""
        return max(0.0, (self.src_out - self.src_in) / self.speed)

    @property
    def end(self) -> float:
        return self.offset + self.duration

    def source_time(self, local_t: float) -> float:
        """Source timestamp shown at segment-local time local_t."""
        return self.src_in + (local_t - self.offset) * self.speed

    def active_at(self, local_t: float) -> bool:
        return self.offset - EPS <= local_t < self.end - EPS


@dataclass
class Keyframe:
    t: float                       # segment-local time
    value: float
    easing: str = "linear"         # "linear" | "ease" | "hold"


@dataclass
class Transition:
    """Applied at the START of a segment, blending from whatever
    segment ends where this one begins on the same track."""
    kind: str = "crossfade"        # crossfade | blur | slide | fade
    duration: float = 0.5
    target: str = "both"           # destination | original | both   (slide/fade)
    direction: str = "left"        # top | right | bottom | left      (slide/fade)


@dataclass
class Segment:
    id: str = field(default_factory=new_id)
    start: float = 0.0             # timeline seconds
    parts: list[Part] = field(default_factory=list)
    name: str = ""
    volume: float = 1.0            # 0.0..2.0
    muted: bool = False
    visible: bool = True
    locked: bool = False
    fade_in: float = 0.0
    fade_out: float = 0.0
    transform: Transform = field(default_factory=Transform)
    # Zoom filter: ramps scale up over zoom_in seconds from the start,
    # holds, then back down over zoom_out seconds before the end.
    zoom_amount: float = 1.0       # 1.0 = no zoom
    zoom_in: float = 0.0
    zoom_out: float = 0.0
    # Property name -> keyframes (e.g. "volume", "scale", "x", "y",
    # "rotation", "opacity"). Keyframes override the static value.
    keyframes: dict[str, list[Keyframe]] = field(default_factory=dict)
    transition_in: "Transition | None" = None
    # Drop shadow under whatever the segment draws (off by default).
    shadow: bool = False
    shadow_color: str = "#000000"
    shadow_opacity: float = 0.6
    shadow_distance: float = 0.012     # fraction of canvas height
    shadow_angle: float = 135.0        # degrees; 135 = down-right (0 = right, 90 = down)
    shadow_blur: float = 0.35          # 0 = hard edge .. 1 = very soft

    @property
    def duration(self) -> float:
        return max((p.end for p in self.parts), default=0.0)

    @property
    def end(self) -> float:
        return self.start + self.duration

    @property
    def has_video(self) -> bool:
        return any(p.has_video for p in self.parts)

    @property
    def has_audio(self) -> bool:
        return any(p.has_audio for p in self.parts)

    def covers(self, t: float) -> bool:
        return self.start - EPS <= t < self.end - EPS

    def split_markers(self) -> list[float]:
        """Timeline times of internal part boundaries (the visual splits
        an inclusive merge leaves; clicking one jumps the playhead there)."""
        edges = set()
        for p in self.parts:
            for e in (p.offset, p.end):
                if EPS < e < self.duration - EPS:
                    edges.add(round(self.start + e, 6))
        return sorted(edges)


@dataclass
class Track:
    id: str = field(default_factory=new_id)
    name: str = ""
    segments: list[Segment] = field(default_factory=list)
    collapsed: bool = False
    hidden: bool = False          # a hidden layer (H) contributes no picture and no sound

    def sorted_segments(self) -> list[Segment]:
        return sorted(self.segments, key=lambda s: s.start)

    def is_empty(self) -> bool:
        return not self.segments

    def free_between(self, start: float, end: float, ignore: "set[str] | None" = None) -> bool:
        """True if no segment (other than ignored ids) overlaps [start, end)."""
        ignore = ignore or set()
        for s in self.segments:
            if s.id in ignore:
                continue
            if s.start < end - EPS and s.end > start + EPS:
                return False
        return True


@dataclass
class Project:
    width: int = 1920
    height: int = 1080
    fps: float = 60.0
    tracks: list[Track] = field(default_factory=list)
    # Where saving writes to. For a library clip this is the clip's own
    # file (overwritten, backup kept); for an imported file it's the
    # "<name>-edited.<ext>" copy next to it.
    output_path: str = ""
    library_video_id: "int | None" = None
    # True while the project holds edits that haven't been rendered to the
    # output file yet (autosave keeps them; the editor shows a marker).
    unsaved_changes: bool = False
    # Edits (saved or not) that haven't been exported to the video yet.
    export_pending: bool = False
    schema_version: int = SCHEMA_VERSION

    # ---- queries ---------------------------------------------------------
    @property
    def duration(self) -> float:
        return max((s.end for t in self.tracks for s in t.segments), default=0.0)

    def all_segments(self):
        for t in self.tracks:
            yield from t.segments

    def find_segment(self, seg_id: str) -> "tuple[Track, Segment] | tuple[None, None]":
        for t in self.tracks:
            for s in t.segments:
                if s.id == seg_id:
                    return t, s
        return None, None

    def track_index(self, track_id: str) -> int:
        for i, t in enumerate(self.tracks):
            if t.id == track_id:
                return i
        raise KeyError(track_id)

    def edge_times(self, exclude: "set[str] | None" = None) -> list[float]:
        """Every segment start/end (for snapping)."""
        exclude = exclude or set()
        out = set()
        for s in self.all_segments():
            if s.id not in exclude:
                out.add(round(s.start, 6))
                out.add(round(s.end, 6))
        return sorted(out)

    # ---- serialization ---------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        version = d.get("schema_version", 1)
        if version > SCHEMA_VERSION:
            raise ValueError(f"Project was saved by a newer version (schema {version} > {SCHEMA_VERSION}).")
        # (migrations for older schema versions go here)
        if version < 2:
            d = _migrate_tail_to_offset(d)
        tracks = []
        for td in d.get("tracks", []):
            segs = []
            for sd in td.get("segments", []):
                parts = []
                for pd in sd.get("parts", []):
                    text = _build(TextStyle, pd.get("text")) if pd.get("text") else None
                    parts.append(_build(Part, {**pd, "text": text}))
                kfs = {k: [_build(Keyframe, kd) for kd in v] for k, v in (sd.get("keyframes") or {}).items()}
                trans = _build(Transition, sd["transition_in"]) if sd.get("transition_in") else None
                segs.append(_build(Segment, {
                    **sd, "parts": parts, "keyframes": kfs, "transition_in": trans,
                    "transform": _build(Transform, sd.get("transform") or {}),
                }))
            tracks.append(_build(Track, {**td, "segments": segs}))
        return _build(cls, {**d, "tracks": tracks, "schema_version": SCHEMA_VERSION})


def _lerp_keys(keys: list, t: float, default: float) -> float:
    ks = sorted(keys or [], key=lambda k: k["t"])
    if not ks:
        return default
    if t <= ks[0]["t"]:
        return ks[0]["value"]
    if t >= ks[-1]["t"]:
        return ks[-1]["value"]
    for a, b in zip(ks, ks[1:]):
        if a["t"] <= t <= b["t"]:
            u = (t - a["t"]) / max(b["t"] - a["t"], 1e-9)
            return a["value"] + (b["value"] - a["value"]) * u
    return default


def _migrate_tail_to_offset(d: dict) -> dict:
    """Schema 1 -> 2: bubble tail tips were absolute canvas positions; they're
    now offsets from the bubble's position (so the tail follows the bubble)."""
    import copy as _copy
    d = _copy.deepcopy(d)
    for td in d.get("tracks", []):
        for sd in td.get("segments", []):
            tr = sd.get("transform") or {}
            kfs = sd.get("keyframes") or {}
            x0, y0 = tr.get("x", 0.0), tr.get("y", 0.0)
            for pd in sd.get("parts", []):
                st = pd.get("text")
                if not st or not st.get("bubble"):
                    continue
                st["tail_x"] = st.get("tail_x", -0.1) - _lerp_keys(kfs.get("x"), 0.0, x0)
                st["tail_y"] = st.get("tail_y", 0.25) - _lerp_keys(kfs.get("y"), 0.0, y0)
            for axis, pos_key, base in (("tail_x", "x", x0), ("tail_y", "y", y0)):
                for k in kfs.get(axis, []) or []:
                    k["value"] = k["value"] - _lerp_keys(kfs.get(pos_key), k["t"], base)
    return d


def _build(cls, data: dict):
    """Construct a dataclass from a dict, ignoring unknown keys (forward
    compatibility) and using defaults for missing ones."""
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (data or {}).items() if k in names})


# ---- construction helpers -----------------------------------------------

def empty_track(name: str = "") -> Track:
    return Track(name=name)


def default_project(width: int = 1920, height: int = 1080, fps: float = 60.0) -> Project:
    """Four tracks, per the spec: the top and bottom are empty buffer
    tracks; 2nd-from-top is where video lands by default, 2nd-from-
    bottom where audio lands. (They're only conventions -- any segment
    can go on any track.)"""
    return Project(width=width, height=height, fps=fps,
                   tracks=[empty_track(), empty_track(), empty_track(), empty_track()])
