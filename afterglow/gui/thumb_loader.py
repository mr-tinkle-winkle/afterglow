"""
Card thumbnails, loaded off the GUI thread.

A VideoCard used to make (ffmpeg) and decode its thumbnail inside its own
constructor: ~60-160 ms per clip when the thumbnail wasn't cached yet and
~7-28 ms when it was (decode + smooth scale of a full-size frame) -- with
93 clips that was most of the time between "window appears" and "Library
is filled", all of it on the GUI thread.

Now a card asks `request()` for its picture and shows a blank placeholder
until it arrives. A few worker threads do everything that is slow (run
ffmpeg for a missing thumbnail, decode, scale, crop); the finished QImage
comes back to the GUI thread through a queued signal, where it becomes a
QPixmap (QPixmap must only be touched on the GUI thread; QImage is safe in
workers).

Requests for the same picture are merged, a card that is deleted before
its picture arrives is skipped. Requests run in the order they were made,
which is the grid's own order: the top rows get their pictures first.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QImage, QImageReader

from .. import thumbnails

_MAX_WORKERS = max(2, min(4, (os.cpu_count() or 2)))


def read_scaled_cover(path: "Path | str", target: QSize) -> "QImage | None":
    """`path` decoded, scaled to cover `target` and centre-cropped to exactly
    that size. JPEGs are decoded at reduced size by the decoder itself
    (QImageReader.setScaledSize), which is where most of the time goes for a
    large source. Safe off the GUI thread."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and size.width() > 0 and size.height() > 0:
        factor = max(target.width() / size.width(), target.height() / size.height())
        # decode at up to 2x the target (cheap), then one smooth scale to the exact size
        pre = min(1.0, factor * 2)
        if pre < 1.0:
            reader.setScaledSize(QSize(max(1, round(size.width() * pre)), max(1, round(size.height() * pre))))
    image = reader.read()
    if image.isNull():
        return None
    return _cover(image, target)


def _cover(image: QImage, target: QSize) -> QImage:
    # Crop to exactly the target AFTER scaling to cover it: a source that isn't
    # 16:9 (YouTube's 4:3 hqdefault.jpg, with its black bars) scales to e.g.
    # 400x300; the centre crop drops the letterbox bars and keeps the rounded
    # corners (applied later) inside what the card shows.
    if image.size() != target:
        image = image.scaled(target, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (image.width() - target.width()) // 2)
    y = max(0, (image.height() - target.height()) // 2)
    return image.copy(x, y, min(image.width(), target.width()), min(image.height(), target.height()))


def _load(video_id, video_path, explicit_path, target: QSize) -> "QImage | None":
    """Worker-thread body: the card picture for one video (None = no picture)."""
    if explicit_path is not None:
        return read_scaled_cover(explicit_path, target)
    video_path = Path(video_path)
    card = thumbnails.card_thumbnail_path(video_id, video_path)
    if card is None:
        return None
    if card.exists():
        image = read_scaled_cover(card, target)
        if image is not None:
            return image
    # A full-size thumbnail from before card thumbnails existed (or made for the
    # upload dialog): scale the small one from it instead of running ffmpeg.
    full = thumbnails.full_thumbnail_path_if_cached(video_id, video_path)
    if full is not None:
        small = read_scaled_cover(full, QSize(thumbnails.CARD_THUMB_WIDTH,
                                              round(thumbnails.CARD_THUMB_WIDTH * 9 / 16)))
        if small is not None:
            tmp = card.with_name(f".{card.stem}.{os.getpid()}.{threading.get_ident()}.tmp.jpg")
            try:
                if small.save(str(tmp), "JPG", 88):
                    os.replace(tmp, card)
            except OSError:
                pass
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
            return _cover(small, target)
    made = thumbnails.get_card_thumbnail(video_id, video_path)
    if made is None:
        return None
    return read_scaled_cover(made, target)


class ThumbLoader(QObject):
    _done = Signal(object, object)   # key, QImage | None  (worker -> GUI thread)
    idle = Signal()                  # nothing queued or running any more

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stack: deque = deque()      # pending jobs, oldest first
        self._waiting: dict = {}          # key -> [callback, ...]   (GUI thread only)
        self._active = 0
        self._threads: list[threading.Thread] = []
        self._stopping = False
        self._done.connect(self._deliver, Qt.QueuedConnection)

    # ------------------------------------------------------------ GUI thread

    def request(self, key, video_id, video_path, explicit_path, target: QSize, callback) -> None:
        """Load the picture for `key`; `callback(key, QImage | None)` runs on
        the GUI thread when it is ready."""
        waiting = self._waiting.get(key)
        if waiting is not None:
            waiting.append(callback)
            return
        self._waiting[key] = [callback]
        with self._cond:
            if self._stopping:
                # shutdown() was called (a window closed) and pictures are wanted
                # again (another window in the same process): start over
                self._stopping = False
            self._threads = [t for t in self._threads if t.is_alive()]
            self._stack.append((key, video_id, video_path, explicit_path, QSize(target)))
            if len(self._threads) < _MAX_WORKERS and len(self._stack) + self._active > len(self._threads):
                t = threading.Thread(target=self._run, name="afterglow-thumbs", daemon=True)
                self._threads.append(t)
                t.start()
            self._cond.notify()

    def busy(self) -> bool:
        return bool(self._waiting)

    def _deliver(self, key, image) -> None:
        callbacks = self._waiting.pop(key, [])
        for cb in callbacks:
            try:
                cb(key, image)
            except RuntimeError:
                pass   # the card was deleted while its picture loaded
            except Exception:
                import traceback
                traceback.print_exc()
        if not self._waiting:
            self.idle.emit()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Process events until every requested picture has been delivered
        (tests, and anything that needs the real thumbnails on screen)."""
        import time
        from PySide6.QtCore import QCoreApplication
        end = time.monotonic() + timeout
        while self._waiting and time.monotonic() < end:
            QCoreApplication.processEvents()
            time.sleep(0.002)
        return not self._waiting

    def shutdown(self) -> None:
        """Stop the workers and drop what is queued (the callbacks waiting for
        those pictures are dropped with it). Usable again afterwards."""
        with self._cond:
            self._stopping = True
            self._stack.clear()
            self._cond.notify_all()
        self._waiting.clear()

    def is_waiting(self, key) -> bool:
        return key in self._waiting

    # ------------------------------------------------------------ workers

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._stack and not self._stopping:
                    self._cond.wait()
                if self._stopping:
                    return
                key, video_id, video_path, explicit_path, target = self._stack.popleft()
                self._active += 1
            image = None
            try:
                image = _load(video_id, video_path, explicit_path, target)
            except Exception:
                import traceback
                traceback.print_exc()
            finally:
                with self._cond:
                    self._active -= 1
            try:
                self._done.emit(key, image)
            except RuntimeError:
                return   # the application is gone


_loader: "ThumbLoader | None" = None


def loader() -> ThumbLoader:
    global _loader
    if _loader is None:
        _loader = ThumbLoader()
    return _loader
