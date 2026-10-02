"""
Background loading of timeline visuals: film-strip thumbnails and
waveform peaks. Both are produced on a worker thread (decoding on the UI
thread would stall scrolling and playback) and cached; the timeline asks
for what it needs while painting and repaints when `ready` fires.
"""
from __future__ import annotations

import queue
import threading
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from ...nle import media

THUMB_STEP = 0.05          # thumbnail times are quantized to this (seconds)
_MAX_THUMBS = 1500


class TimelineVisuals(QObject):
    ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thumbs: "OrderedDict[tuple, QImage]" = OrderedDict()
        self._peaks: dict[str, np.ndarray] = {}
        self._pending: set = set()
        self._q: "queue.Queue" = queue.Queue()
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="timeline-visuals")
        self._thread.start()

    # ---- requests (UI thread) --------------------------------------------
    def thumbnail(self, path: str, t: float, height: int) -> "QImage | None":
        key = (path, round(round(t / THUMB_STEP) * THUMB_STEP, 3), int(height))
        with self._lock:
            img = self._thumbs.get(key)
            if img is not None:
                self._thumbs.move_to_end(key)
                return img
            if key in self._pending:
                return None
            self._pending.add(key)
        self._q.put(("thumb", key))
        return None

    def nearest_thumbnail(self, path: str, t: float, height: int) -> "QImage | None":
        """Any cached thumbnail of this file at this height, closest in time
        (shown while the exact one is still loading)."""
        best, best_d = None, 1e9
        with self._lock:
            for (p, tt, h), img in self._thumbs.items():
                if p == path and h == height and abs(tt - t) < best_d:
                    best, best_d = img, abs(tt - t)
        return best

    def peaks(self, path: str) -> "np.ndarray | None":
        with self._lock:
            pk = self._peaks.get(path)
            if pk is not None:
                return pk
            key = ("peaks", path)
            if key in self._pending:
                return None
            self._pending.add(key)
        self._q.put(("peaks", path))
        return None

    def forget(self) -> None:
        """Drop queued (not yet started) work, e.g. after zooming a lot."""
        try:
            while True:
                kind, key = self._q.get_nowait()
                with self._lock:
                    self._pending.discard(key if kind == "thumb" else ("peaks", key))
        except queue.Empty:
            pass

    def shutdown(self) -> None:
        self._stop = True
        self.forget()
        self._q.put(None)
        # Let an in-flight decode finish before Qt tears objects down, so
        # the worker never signals a deleted object at exit.
        self._thread.join(timeout=2.0)

    # ---- worker ------------------------------------------------------------
    def _run(self) -> None:
        reader = media.ThumbnailReader()
        while not self._stop:
            item = self._q.get()
            if item is None:
                break
            kind, key = item
            try:
                if kind == "thumb":
                    path, t, h = key
                    img = reader.thumbnail(path, t, h)
                    with self._lock:
                        self._pending.discard(key)
                        if img is not None:
                            self._thumbs[key] = img
                            while len(self._thumbs) > _MAX_THUMBS:
                                self._thumbs.popitem(last=False)
                else:
                    pk = media.waveform_peaks(key)
                    with self._lock:
                        self._pending.discard(("peaks", key))
                        self._peaks[key] = pk
            except Exception:
                with self._lock:
                    self._pending.discard(key if kind == "thumb" else ("peaks", key))
                continue
            if self._q.empty() or kind == "peaks":
                self.ready.emit()
        reader.close()
