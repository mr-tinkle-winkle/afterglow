"""
Undo/redo for the Advanced Editor.

Snapshot-based: before each edit, the whole project is serialized (a
plain dict). Undo restores the previous snapshot; redo the next one.
Projects are small (tens of segments), so a snapshot is cheap (~0.1ms
for a typical edit), and snapshots can never drift out of sync with the
model the way hand-written inverse operations can.

Continuous gestures (dragging a segment, dragging the volume line,
dragging a trim edge) must produce ONE undo step, not one per mouse
move. Wrap them in begin()/end(): the snapshot is taken at begin(),
nothing is recorded during the drag, and end() records a single step
(or nothing, if the project didn't actually change).

    history.perform("Split", lambda p: ops.split(p, seg_id, t))

    history.begin("Move segment")
    ... many ops.move_segment(...) calls while dragging ...
    history.end()
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable

from .model import Project

MAX_STEPS = 200


@dataclass
class _Step:
    label: str
    before: dict
    after: dict


class History:
    def __init__(self, project: Project, on_change: "Callable[[], None] | None" = None):
        self.project = project
        self._undo: list[_Step] = []
        self._redo: list[_Step] = []
        self._open: "tuple[str, dict] | None" = None
        self._on_change = on_change
        # Count of steps since the last save; lets the UI show "unsaved".
        self._saved_marker: "int | None" = 0
        self._position = 0

    # ---- recording -------------------------------------------------------
    def perform(self, label: str, fn: Callable[[Project], object]):
        """Run fn(project) as one undoable step. Returns fn's result.
        If fn raises, the project is restored and nothing is recorded."""
        if self._open is not None:
            return fn(self.project)  # already inside a gesture: just apply
        before = self.project.to_dict()
        try:
            result = fn(self.project)
        except Exception:
            self._restore(before)
            raise
        self._record(label, before)
        return result

    def begin(self, label: str) -> None:
        if self._open is None:
            self._open = (label, self.project.to_dict())

    def end(self) -> None:
        if self._open is None:
            return
        label, before = self._open
        self._open = None
        self._record(label, before)

    def cancel(self) -> None:
        """Abort an open gesture and put the project back as it was."""
        if self._open is None:
            return
        _label, before = self._open
        self._open = None
        self._restore(before)
        self._changed()

    @property
    def in_gesture(self) -> bool:
        return self._open is not None

    def _record(self, label: str, before: dict) -> None:
        after = self.project.to_dict()
        if after == before:
            return  # no-op edit: don't add an empty undo step
        self._undo.append(_Step(label, before, after))
        if len(self._undo) > MAX_STEPS:
            self._undo.pop(0)  # positions are absolute, so the saved marker stays valid
        self._redo.clear()
        self._position += 1
        self._changed()

    # ---- undo / redo -----------------------------------------------------
    def can_undo(self) -> bool:
        return bool(self._undo) and self._open is None

    def can_redo(self) -> bool:
        return bool(self._redo) and self._open is None

    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def undo(self) -> bool:
        if not self.can_undo():
            return False
        step = self._undo.pop()
        self._restore(step.before)
        self._redo.append(step)
        self._position -= 1
        self._changed()
        return True

    def redo(self) -> bool:
        if not self.can_redo():
            return False
        step = self._redo.pop()
        self._restore(step.after)
        self._undo.append(step)
        self._position += 1
        self._changed()
        return True

    # ---- saved state -----------------------------------------------------
    def mark_saved(self) -> None:
        self._saved_marker = self._position

    @property
    def dirty(self) -> bool:
        return self._saved_marker != self._position

    # ---- internals -------------------------------------------------------
    def _restore(self, snapshot: dict) -> None:
        restored = Project.from_dict(copy.deepcopy(snapshot))
        # Mutate in place so every holder of self.project sees the change.
        self.project.__dict__.update(restored.__dict__)

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()
