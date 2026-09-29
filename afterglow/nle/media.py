"""
Media access for the Advanced Editor renderer: probing, frame-accurate
video decoding, and audio decoding. Uses PyAV (FFmpeg's libraries), so
anything ffmpeg can read works.

Source time convention: t = 0 is the start of the FILE (the container's
start_time), for both video and audio. Using one shared origin keeps
the file's own A/V sync intact, including files whose first video frame
or first audio packet doesn't sit exactly at 0.

VideoSource.frame_at(t) returns the frame that is ON SCREEN at source
time t, i.e. the last frame whose timestamp is <= t. Sequential access
(playback, export) decodes forward without seeking; a jump backward or
far ahead seeks to the previous keyframe and decodes forward from it.
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict
from fractions import Fraction

import av
import numpy as np

AUDIO_RATE = 48000
_FRAME_EPS = 1e-4
_FORWARD_DECODE_LIMIT = 2.0      # seconds: decode forward instead of seeking if within this
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
GIF_EXTS = {".gif"}
DEFAULT_STILL_DURATION = 5.0


def probe(path: str) -> dict:
    """{path, kind, duration, has_video, has_audio, width, height, fps}.
    Stills get DEFAULT_STILL_DURATION (they can be stretched to any length)."""
    ext = os.path.splitext(path)[1].lower()
    info = {"path": path, "kind": "av", "duration": 0.0, "has_video": False, "has_audio": False,
            "width": 0, "height": 0, "fps": 0.0}
    if ext in IMAGE_EXTS:
        from PySide6.QtGui import QImageReader
        r = QImageReader(path)
        size = r.size()
        if not size.isValid():
            raise ValueError(f"Can't read image {path}")
        info.update(kind="image", duration=DEFAULT_STILL_DURATION, has_video=True,
                    width=size.width(), height=size.height())
        return info
    with av.open(path) as c:
        v = c.streams.video[0] if c.streams.video else None
        a = c.streams.audio[0] if c.streams.audio else None
        dur = (c.duration / av.time_base) if c.duration else 0.0
        if not dur:
            for s in (v, a):
                if s is not None and s.duration and s.time_base:
                    dur = max(dur, float(s.duration * s.time_base))
        info["duration"] = float(dur)
        if v is not None:
            info["has_video"] = True
            info["width"], info["height"] = v.codec_context.width, v.codec_context.height
            rate = v.average_rate or v.base_rate or v.guessed_rate
            info["fps"] = float(rate) if rate else 30.0
        info["has_audio"] = a is not None
    if ext in GIF_EXTS:
        info["kind"] = "gif"
    return info


class VideoSource:
    """One open decoder for one file (not thread-safe: give each thread
    -- preview, export -- its own)."""

    def __init__(self, path: str):
        self.path = path
        self._c = av.open(path)
        self._s = self._c.streams.video[0]
        self._s.thread_type = "AUTO"
        self._tb = self._s.time_base
        self._origin = (self._c.start_time / av.time_base) if self._c.start_time is not None else 0.0
        self.width = self._s.codec_context.width
        self.height = self._s.codec_context.height
        self._decoder = None
        self._cur = None          # (t, frame)
        self._pending = None      # next decoded frame, (t, frame) -- not yet on screen
        self._eof = False

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:
            pass

    def _t(self, frame) -> float:
        pts = frame.pts if frame.pts is not None else frame.dts
        return float(pts * self._tb) - self._origin if pts is not None else 0.0

    def _seek(self, t: float) -> None:
        target = int((t + self._origin) / self._tb)
        self._c.seek(max(0, target), stream=self._s, backward=True, any_frame=False)
        self._decoder = self._c.decode(self._s)
        self._cur = None
        self._pending = None
        self._eof = False

    def _next(self):
        if self._decoder is None:
            self._decoder = self._c.decode(self._s)
        if self._pending is not None:
            item, self._pending = self._pending, None
            return item
        try:
            f = next(self._decoder)
        except (StopIteration, av.error.EOFError):
            self._eof = True
            return None
        return (self._t(f), f)

    def frame_at(self, t: float):
        """av.VideoFrame on screen at source time t (clamped to the
        first/last frame). None only if the file has no decodable frames."""
        t = max(0.0, t)
        cur = self._cur
        if cur is not None and cur[0] <= t + _FRAME_EPS:
            nxt = self._pending
            if nxt is not None and t + _FRAME_EPS < nxt[0]:
                return cur[1]
            if self._eof and nxt is None:
                return cur[1]          # holding the last frame past the end
            if t - cur[0] > _FORWARD_DECODE_LIMIT:
                self._seek(t)
        else:
            self._seek(t)
        while True:
            item = self._next()
            if item is None:
                return self._cur[1] if self._cur else None
            ft, f = item
            if ft <= t + _FRAME_EPS or self._cur is None:
                self._cur = item
                if ft > t + _FRAME_EPS:   # first frame after a seek is already past t (t before first frame)
                    return f
                continue
            self._pending = item
            return self._cur[1]


def frame_to_qimage(frame, width: int, height: int):
    """Scale (in C, via swscale) and convert an av.VideoFrame to a QImage."""
    from PySide6.QtGui import QImage
    width, height = max(2, int(width)), max(2, int(height))
    rgb = frame.reformat(width=width, height=height, format="bgra")
    arr = np.ascontiguousarray(rgb.to_ndarray())
    img = QImage(arr.data, width, height, width * 4, QImage.Format_ARGB32)
    return img.copy()   # own the pixels (arr is freed after return)


class FrameCache:
    """Small LRU of converted frames, for scrubbing back and forth."""

    def __init__(self, max_items: int = 96):
        self._d: OrderedDict = OrderedDict()
        self._max = max_items

    def get(self, key):
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v

    def put(self, key, value) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self._max:
            self._d.popitem(last=False)


# ---- audio ---------------------------------------------------------------

_audio_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
_audio_lock = threading.Lock()
_AUDIO_CACHE_BYTES = 768 * 1024 * 1024


def load_audio(path: str) -> np.ndarray:
    """Whole-file audio as float32 (N, 2) at AUDIO_RATE, positioned on the
    file's timeline (index 0 = source t 0). Cached by path+mtime; total
    cache capped by bytes. Clips are typically seconds to minutes long,
    so decoding once and slicing is far simpler and faster than
    streaming; very long sources (>~1h) would need a streaming path."""
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return np.zeros((0, 2), np.float32)
    with _audio_lock:
        hit = _audio_cache.get(key)
        if hit is not None:
            _audio_cache.move_to_end(key)
            return hit
    with av.open(path) as c:
        if not c.streams.audio:
            data = np.zeros((0, 2), np.float32)
        else:
            s = c.streams.audio[0]
            origin = (c.start_time / av.time_base) if c.start_time is not None else 0.0
            # Mono is decoded as mono and duplicated to both channels at FULL
            # level. Letting the resampler upmix mono -> stereo applies a
            # -3 dB "center" pan (x0.707 per channel), so every mono clip
            # (most mics, many voice recordings) came out noticeably quieter
            # than in any other editor. >2 channels are downmixed to stereo.
            mono = s.codec_context.channels == 1
            nch = 1 if mono else 2
            res = av.AudioResampler(format="flt", layout="mono" if mono else "stereo", rate=AUDIO_RATE)
            chunks, first_t = [], None
            for frame in c.decode(s):
                if first_t is None and frame.pts is not None:
                    first_t = float(frame.pts * frame.time_base) - origin
                for rf in res.resample(frame):
                    chunks.append(rf.to_ndarray().reshape(-1, nch))
            for rf in res.resample(None):
                chunks.append(rf.to_ndarray().reshape(-1, nch))
            data = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros((0, nch), np.float32)
            if mono:
                data = np.repeat(data, 2, axis=1)
            lead = int(round(max(0.0, first_t or 0.0) * AUDIO_RATE))
            if lead:
                data = np.concatenate([np.zeros((lead, 2), np.float32), data])
    with _audio_lock:
        _audio_cache[key] = data
        total = sum(v.nbytes for v in _audio_cache.values())
        while total > _AUDIO_CACHE_BYTES and len(_audio_cache) > 1:
            _k, old = _audio_cache.popitem(last=False)
            total -= old.nbytes
    return data


def fps_fraction(fps: float) -> Fraction:
    """Common rates as exact fractions (29.97 -> 30000/1001)."""
    for num, den in ((24000, 1001), (30000, 1001), (60000, 1001)):
        if abs(fps - num / den) < 0.01:
            return Fraction(num, den)
    return Fraction(fps).limit_denominator(1001)


# ---- timeline visuals ------------------------------------------------------

PEAK_RATE = 200                  # waveform bins per second of source
_peaks_cache: "OrderedDict[tuple, np.ndarray]" = OrderedDict()


def waveform_peaks(path: str) -> np.ndarray:
    """Max |sample| per 1/PEAK_RATE s of the source (float32, 0..1), for
    drawing a waveform. Index 0 = source t 0. Empty if no audio."""
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        return np.zeros(0, np.float32)
    with _audio_lock:
        hit = _peaks_cache.get(key)
        if hit is not None:
            return hit
    data = load_audio(path)
    if data.shape[0] == 0:
        peaks = np.zeros(0, np.float32)
    else:
        mono = np.abs(data).max(axis=1)
        step = AUDIO_RATE // PEAK_RATE
        n = int(np.ceil(mono.shape[0] / step))
        padded = np.zeros(n * step, np.float32)
        padded[:mono.shape[0]] = mono
        peaks = padded.reshape(n, step).max(axis=1)
    with _audio_lock:
        _peaks_cache[key] = peaks
        while len(_peaks_cache) > 64:
            _peaks_cache.popitem(last=False)
    return peaks


class ThumbnailReader:
    """Frames for the timeline film strip (one per thread). Keeps one
    decoder per file so neighboring tiles decode forward cheaply."""

    def __init__(self):
        self._sources: dict[str, VideoSource] = {}

    def thumbnail(self, path: str, t: float, height: int):
        from PySide6.QtGui import QImage
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_EXTS:
            img = QImage(path)
            return None if img.isNull() else img.scaledToHeight(max(2, height))
        src = self._sources.get(path)
        if src is None:
            try:
                src = VideoSource(path)
            except Exception:
                return None
            self._sources[path] = src
        frame = src.frame_at(t)
        if frame is None:
            return None
        w = max(2, int(round(src.width * height / max(src.height, 1))))
        return frame_to_qimage(frame, w, height)

    def close(self) -> None:
        for s in self._sources.values():
            s.close()
        self._sources.clear()
