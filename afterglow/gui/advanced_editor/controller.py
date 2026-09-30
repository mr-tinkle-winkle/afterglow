"""
EditorController: the one place the Advanced Editor's UI edits the
project. Owns the Project, its History (undo/redo), the selection, the
playhead, the clipboard and the snapping switch. Every panel reads from
here and every edit goes through perform()/begin()/end(), so everything
is undoable and every panel refreshes from the same `changed` signal.

Autosave: edits are written to the clip's project file a few seconds
after they happen (never rendered -- that's what Save does), with
Project.unsaved_changes set so reopening the clip shows the marker.
"""
from __future__ import annotations

import copy
import os

from PySide6.QtCore import QObject, QTimer, Signal

from ...nle import media, ops, store
from ...nle.history import History
from ...nle.model import (
    EPS, KIND_AV, KIND_GIF, KIND_IMAGE, KIND_TEXT, Part, Project, Segment, TextStyle, Transition,
)

AUTOSAVE_DELAY_MS = 3000
def comic_font() -> str:
    """Default font for speech/thought bubbles: the bundled Permanent Marker."""
    from ..fonts import load_bundled_fonts
    load_bundled_fonts()
    return "Permanent Marker"


COMIC_FONT = "Comic Sans MS"     # replaced with comic_font() when a bubble is created
TEXT_PRESETS = {
    "Title": TextStyle(text="Title", font_family="Montserrat", font_size=0.12, bold=True, outline_width=3.0),
    "Subtitle": TextStyle(text="Subtitle", font_family="Roboto", font_size=0.06, outline_width=2.0),
    "Caption": TextStyle(text="Caption", font_family="Roboto", font_size=0.045, outline_width=2.0),
    "Plain text": TextStyle(text="Text", font_family="Roboto", font_size=0.08),
    "Speech bubble": TextStyle(text="Speech!", font_family=COMIC_FONT, font_size=0.055,
                               color="#111111", bubble="speech", tail_x=-0.16, tail_y=0.25,
                               grow_in=0.35, grow_out=0.3, delay_in=0.6),
    "Thought bubble": TextStyle(text="Hmm...", font_family=COMIC_FONT, font_size=0.055,
                                color="#111111", bubble="thought", tail_x=-0.16, tail_y=0.25,
                                grow_in=0.6, grow_out=0.45, delay_in=0.6),
}
# Where each preset sits (Transform.x, Transform.y as fractions from the center;
# 0.5 would be the edge). Subtitles sit just above the bottom edge.
TEXT_PRESET_POS = {"Title": (0.0, 0.0), "Subtitle": (0.0, 0.36), "Caption": (0.0, 0.42),
                   "Plain text": (0.0, 0.0), "Speech bubble": (0.18, -0.2), "Thought bubble": (0.18, -0.2)}

_probe_cache: dict = {}


def probe_cached(path: str) -> dict:
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return {}
    info = _probe_cache.get(key)
    if info is None:
        try:
            info = media.probe(path)
        except Exception:
            info = {}
        _probe_cache[key] = info
    return info


class EditorController(QObject):
    changed = Signal()             # project content changed
    selection_changed = Signal()
    playhead_changed = Signal(float)
    state_changed = Signal()       # undo/redo availability, dirty marker, project loaded
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: "Project | None" = None
        self.history: "History | None" = None
        self.selection: list[str] = []
        self.selected_tracks: list[str] = []    # layers picked by clicking their header
        self.anchor: "str | None" = None
        self.playhead = 0.0
        self.clipboard: dict = {}
        self.snapping = True
        # "library" (library_video_id set) | "import" (import_source set)
        self.mode: "str | None" = None
        self.import_source: "str | None" = None
        self._loaded_unsaved = False
        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(AUTOSAVE_DELAY_MS)
        self._autosave.timeout.connect(self.autosave_now)
        # Called before any other edit starts, so a panel's grouped edit
        # (e.g. a burst of spin box changes) is closed off as its own undo
        # step first instead of swallowing the next action.
        self.flush_hooks: list = []

    # ------------------------------------------------------------ loading
    def set_project(self, project: Project, mode: str, import_source: "str | None" = None) -> None:
        self.autosave_now()
        self.project = project
        self.mode = mode
        self.import_source = import_source
        self.history = History(project, on_change=self._on_history_change)
        self._loaded_unsaved = bool(project.unsaved_changes)
        self.selection = []
        self.selected_tracks = []
        self.anchor = None
        self.playhead = 0.0
        self.changed.emit()
        self.selection_changed.emit()
        self.playhead_changed.emit(0.0)
        self.state_changed.emit()

    def clear(self) -> None:
        self.autosave_now()
        self.project = None
        self.history = None
        self.mode = None
        self.selection = []
        self.changed.emit()
        self.selection_changed.emit()
        self.state_changed.emit()

    @property
    def has_project(self) -> bool:
        return self.project is not None

    @property
    def unsaved(self) -> bool:
        return self.history is not None and (self.history.dirty or self._loaded_unsaved)

    def project_file(self):
        if self.project is None:
            return None
        if self.mode == "library" and self.project.library_video_id is not None:
            return store.project_path_for_video(self.project.library_video_id)
        if self.mode == "import" and self.import_source:
            return store.project_path_for_import(self.import_source)
        return None

    def autosave_now(self) -> None:
        self._autosave.stop()
        path = self.project_file()
        if path is None or self.history is None or self.history.in_gesture:
            return
        if not self.unsaved:
            return
        self.project.unsaved_changes = True
        try:
            store.save_project(self.project, path)
        except OSError:
            pass

    def mark_rendered(self) -> None:
        """After a successful Save (render)."""
        self._loaded_unsaved = False
        self.project.unsaved_changes = False
        self.history.mark_saved()
        path = self.project_file()
        if path is not None:
            store.save_project(self.project, path)
        self.state_changed.emit()

    def remap_sources(self, mapping: dict) -> None:
        """Re-point every part reading from a key path at its value, in the
        live project AND every undo/redo snapshot (so undoing past a save or
        a rename never points back at a file that no longer holds the
        original footage)."""
        if not mapping or self.project is None:
            return

        def walk(d):
            for track in d.get("tracks", []):
                for seg in track.get("segments", []):
                    for part in seg.get("parts", []):
                        src = part.get("source")
                        if src in mapping:
                            part["source"] = mapping[src]
            if d.get("output_path") in mapping:
                d["output_path"] = mapping[d["output_path"]]
        for seg in self.project.all_segments():
            for part in seg.parts:
                if part.source in mapping:
                    part.source = mapping[part.source]
        if self.project.output_path in mapping:
            self.project.output_path = mapping[self.project.output_path]
        h = self.history
        if h is not None:
            for step in list(h._undo) + list(h._redo):
                walk(step.before)
                walk(step.after)
            if h._open is not None:
                walk(h._open[1])
        self.changed.emit()

    def _on_history_change(self) -> None:
        # Selection may point at segments an undo removed.
        before = list(self.selection)
        self.selection = [i for i in self.selection if self.project.find_segment(i)[1] is not None]
        valid = {t.id for t in self.project.tracks}
        self.selected_tracks = [t for t in self.selected_tracks if t in valid]
        self.changed.emit()
        if self.selection != before:
            self.selection_changed.emit()
        self.state_changed.emit()
        self._autosave.start()

    # ------------------------------------------------------------ editing
    def _flush(self) -> None:
        for hook in list(self.flush_hooks):
            hook()

    def flush_edits(self) -> None:
        """Close any panel's grouped edit (e.g. a burst of spin box changes)
        so it's recorded -- before leaving the page or quitting."""
        self._flush()

    def perform(self, label: str, fn):
        if self.history is None:
            return None
        self._flush()
        try:
            result = self.history.perform(label, fn)
        except ops.OpError as e:
            self.error.emit(str(e))
            return None
        if self.history.in_gesture:
            self.changed.emit()
        return result

    def begin(self, label: str) -> None:
        if self.history is not None:
            self._flush()
            self.history.begin(label)

    def end(self) -> None:
        if self.history is not None:
            was_open = self.history.in_gesture
            self.history.end()
            if was_open:
                self.changed.emit()   # a no-op gesture records nothing but views may need a refresh
                self.state_changed.emit()

    def cancel(self) -> None:
        if self.history is not None:
            self.history.cancel()

    def live(self, fn) -> None:
        """Apply fn inside the open gesture (dragging). Errors are shown."""
        if self.project is None:
            return
        try:
            fn(self.project)
        except ops.OpError as e:
            self.error.emit(str(e))
        self.changed.emit()

    def undo(self) -> None:
        self._flush()
        if self.history is not None and self.history.undo():
            self.state_changed.emit()

    def redo(self) -> None:
        self._flush()
        if self.history is not None and self.history.redo():
            self.state_changed.emit()

    # ------------------------------------------------------------ playhead / selection
    def set_playhead(self, t: float, snap: bool = False, threshold: float = 0.0) -> None:
        if self.project is None:
            return
        t = max(0.0, t)
        if snap and self.snapping and threshold > 0:
            t = ops.snap_playhead(self.project, t, threshold)
        if abs(t - self.playhead) > 1e-9:
            self.playhead = t
            self.playhead_changed.emit(t)

    def frame_step(self) -> float:
        return 1.0 / max(self.project.fps if self.project else 30.0, 1.0)

    def step_playhead(self, frames: int = 0, seconds: float = 0.0) -> None:
        self.set_playhead(self.playhead + frames * self.frame_step() + seconds)

    def set_selection(self, ids: list[str], anchor: "str | None" = None) -> None:
        ids = [i for i in dict.fromkeys(ids) if self.project and self.project.find_segment(i)[1] is not None]
        if anchor is not None:
            self.anchor = anchor
        if ids != self.selection:
            self.selection = ids
            self.selection_changed.emit()

    def set_track_selection(self, track_ids: list) -> None:
        if self.project is None:
            return
        valid = {t.id for t in self.project.tracks}
        ids = [t for t in dict.fromkeys(track_ids) if t in valid]
        if ids != self.selected_tracks:
            self.selected_tracks = ids
            self.selection_changed.emit()

    def toggle_hidden_layers(self) -> None:
        """H: hide/show the selected layers (track headers), or else the
        layers holding the selected segments."""
        if self.project is None:
            return
        ids = list(self.selected_tracks)
        if not ids:
            for sid in self.selection:
                t, _s = self.project.find_segment(sid)
                if t is not None and t.id not in ids:
                    ids.append(t.id)
        if ids:
            self.perform("Hide layer", lambda p: ops.set_tracks_hidden(p, ids))

    def click_select(self, seg_id: str, ctrl: bool, shift: bool) -> None:
        """Click/Ctrl/Shift selection, per the spec: Ctrl toggles; Shift
        selects everything between the anchor and this segment -- on the
        same track, or (different tracks) every segment in that time span
        on every track between them, inclusive."""
        if ctrl:
            sel = list(self.selection)
            if seg_id in sel:
                sel.remove(seg_id)
            else:
                sel.append(seg_id)
            self.set_selection(sel, anchor=seg_id)
            return
        if shift and self.anchor and self.project.find_segment(self.anchor)[1] is not None:
            self.set_selection(self.range_between(self.anchor, seg_id))
            return
        self.set_selection([seg_id], anchor=seg_id)

    def range_between(self, a_id: str, b_id: str) -> list[str]:
        p = self.project
        ta, a = p.find_segment(a_id)
        tb, b = p.find_segment(b_id)
        ia, ib = p.track_index(ta.id), p.track_index(tb.id)
        t0 = min(a.start, b.start)
        t1 = max(a.end, b.end)
        out = []
        for i in range(min(ia, ib), max(ia, ib) + 1):
            for s in p.tracks[i].sorted_segments():
                if s.start < t1 - EPS and s.end > t0 + EPS:
                    out.append(s.id)
        return out

    def selected_segments(self) -> list[Segment]:
        if self.project is None:
            return []
        out = []
        for i in self.selection:
            _, s = self.project.find_segment(i)
            if s is not None:
                out.append(s)
        return out

    # ------------------------------------------------------------ actions (keyboard / menus)
    def split(self) -> None:
        t = self.playhead
        ids = [i for i in self.selection if (s := self.project.find_segment(i)[1]) is not None and s.covers(t)]
        new = self.perform("Split", lambda p: ops.split_at(p, t, ids or None))
        if new:
            self.set_selection(list(self.selection) + list(new))

    def combine(self) -> None:
        merged = self.perform("Combine", lambda p: ops.combine(p, list(self.selection)))
        if merged is not None:
            self.set_selection([merged.id], anchor=merged.id)

    def toggle(self, attr: str) -> None:
        if not self.selection:
            return
        label = {"locked": "Lock", "muted": "Mute", "visible": "Visibility"}[attr]
        self.perform(label, lambda p: ops.toggle(p, list(self.selection), attr))

    def copy(self) -> None:
        if self.selection:
            self.clipboard = ops.copy_segments(self.project, list(self.selection))

    def cut(self) -> None:
        self.copy()
        self.delete()

    def paste(self) -> None:
        if not self.clipboard or self.project is None:
            return
        t = self.playhead
        clip = copy.deepcopy(self.clipboard)
        new = self.perform("Paste", lambda p: ops.paste(p, clip, t))
        if new:
            self.set_selection(new, anchor=new[0])

    def duplicate(self) -> None:
        if not self.selection:
            return
        new = self.perform("Duplicate", lambda p: ops.duplicate(p, list(self.selection)))
        if new:
            self.set_selection(new, anchor=new[0])

    def delete(self) -> None:
        if self.selection:
            self.perform("Delete", lambda p: ops.delete(p, list(self.selection)))

    def ripple_delete(self) -> None:
        if self.selection:
            self.perform("Ripple delete", lambda p: ops.ripple_delete(p, list(self.selection)))

    def detach_audio(self) -> None:
        segs = list(self.selection)
        if segs:
            self.perform("Detach audio", lambda p: [ops.detach_audio(p, i) for i in segs])

    def close_gap(self, track_id: str, t: float) -> None:
        self.perform("Close gap", lambda p: ops.close_gap(p, track_id, t))

    def select_all(self) -> None:
        if self.project is not None:
            self.set_selection([s.id for s in self.project.all_segments()])

    # ------------------------------------------------------------ adding things
    def add_file(self, path: str, t: "float | None" = None, track_index: "int | None" = None) -> "Segment | None":
        info = probe_cached(path)
        if not info or info.get("duration", 0) <= 0:
            self.error.emit(f"Can't read {os.path.basename(path)}.")
            return None
        at = self.playhead if t is None else t
        seg = self.perform("Add media", lambda p: ops.add_media(p, info, start=at, track_index=track_index))
        if seg is not None:
            self.set_selection([seg.id], anchor=seg.id)
        return seg

    def add_text(self, preset: str = "Plain text", t: "float | None" = None,
                 track_index: "int | None" = None) -> "Segment | None":
        style = copy.deepcopy(TEXT_PRESETS.get(preset, TEXT_PRESETS["Plain text"]))
        if style.font_family == COMIC_FONT:
            style.font_family = comic_font()
        part = Part(kind=KIND_TEXT, source="", src_in=0.0, src_out=5.0, has_video=True, has_audio=False,
                    text=style)
        seg = Segment(parts=[part], name=style.text)
        seg.transform.x, seg.transform.y = TEXT_PRESET_POS.get(preset, (0.0, 0.0))
        at = self.playhead if t is None else t
        ti = 1 if track_index is None else track_index
        placed = self.perform("Add text", lambda p: ops.place(p, seg, ti, at, prefer=-1))
        if placed is not None:
            self.set_selection([seg.id], anchor=seg.id)
            return seg
        return None

    def apply_transition(self, seg_ids: list[str], kind: "str | None", duration: float = 0.5,
                         target: str = "both", direction: str = "left") -> None:
        def fn(p):
            for sid in seg_ids:
                _, s = p.find_segment(sid)
                if s is None or s.locked:
                    continue
                if kind is None:
                    s.transition_in = None
                else:
                    s.transition_in = Transition(kind=kind, duration=max(0.05, min(duration, s.duration)),
                                                 target=target, direction=direction)
        self.perform("Transition" if kind else "Remove transition", fn)

    # ------------------------------------------------------------ properties (keyframe-aware)
    def local_time(self, seg: Segment) -> float:
        return max(0.0, min(self.playhead - seg.start, seg.duration))

    def animated_fn(self, seg_ids: list[str], prop: str, value: float) -> None:
        """Set a transform/volume/opacity value. If that property has
        keyframes on a segment, set a keyframe at the playhead instead of
        the static value (standard NLE behavior)."""
        def fn(p):
            for sid in seg_ids:
                _, s = p.find_segment(sid)
                if s is None or s.locked:
                    continue
                if prop in s.keyframes:
                    ops.set_keyframe(p, s.id, prop, self.local_time(s), value)
                elif prop in ("tail_x", "tail_y"):
                    for part in s.parts:
                        if part.text is not None:
                            setattr(part.text, prop, value)
                elif prop == "volume":
                    ops.set_volume(p, [s.id], value)
                elif prop == "opacity":
                    ops.set_keyframe(p, s.id, prop, self.local_time(s), value)
                else:
                    ops.set_transform(p, [s.id], **{prop: value})
        return fn


def is_audio_only(seg: Segment) -> bool:
    return seg.has_audio and not seg.has_video


def seg_kind(seg: Segment) -> str:
    kinds = {p.kind for p in seg.parts}
    if KIND_TEXT in kinds:
        return KIND_TEXT
    if kinds & {KIND_IMAGE, KIND_GIF} and KIND_AV not in kinds:
        return KIND_IMAGE
    if is_audio_only(seg):
        return "audio"
    return KIND_AV
