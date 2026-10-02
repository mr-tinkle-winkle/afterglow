"""
Smart cutting / smart rendering for H.264 video: reuse the encoded video
as-is wherever nothing about the picture changes, and encode ONLY the frames
that have to be new.

An H.264 stream can be cut without re-encoding only at an IDR keyframe
(every frame after it decodes from it alone). A frame-exact trim that starts
between keyframes therefore re-encodes just the frames from the cut point up
to the next keyframe ("head"), stream-copies every whole GOP after it, and
-- when the trim ends mid-GOP -- re-encodes the last partial GOP ("tail").
For a 30 s clip out of an OBS replay buffer with 2 s keyframes that is under
2 s of encoding instead of 30.

Mixing two encoders in one stream works because both sets of parameter sets
travel with it: the new pieces are encoded by x264 with its own SPS/PPS id
(`sps-id`, chosen not to clash with the source's), the MP4/MKV avcC holds
the source's SPS/PPS AND x264's (avcC allows several), and each piece also
carries its parameter sets in-band on its first frame. Every decoder that
supports parameter-set ids (all of them -- it's baseline H.264) switches
between them per slice.

Building blocks used by the clip-capture trim (smart_trim), the Library's
quick trim and the Editor's export (nle/render.py):

  video_info(path)        codec / size / fps / avcC / colour of the first video stream
  eligible(info)          why it can't be smart-cut (None = it can)
  scan_gops(path, t0, t1) keyframes (and whether each is a clean IDR cut point)
  encode_frames(...)      encode decoded frames into a chunk file (x264, our sps-id)
  mux_chunks(...)         stitch copy-chunks + encoded chunks + audio into one file

Anything that doesn't fit (HEVC/AV1, 10-bit, VFR, open GOPs, odd avcC...)
raises Unsupported and the caller falls back to a full re-encode, so the
result is never worse than before -- only faster.
"""
from __future__ import annotations

import heapq
import math
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

import av


class Unsupported(Exception):
    """This file / edit can't be smart-cut; use a full re-encode."""


# =========================================================================
# H.264 parameter sets / avcC
# =========================================================================
def _unescape(nal: bytes) -> bytes:
    return nal.replace(b"\x00\x00\x03", b"\x00\x00")


class _Bits:
    def __init__(self, data: bytes):
        self.d, self.i = data, 0

    def bit(self) -> int:
        b = (self.d[self.i >> 3] >> (7 - (self.i & 7))) & 1
        self.i += 1
        return b

    def ue(self) -> int:
        z = 0
        while self.bit() == 0:
            z += 1
            if z > 31:
                raise Unsupported("bad exp-golomb")
        v = 0
        for _ in range(z):
            v = (v << 1) | self.bit()
        return (1 << z) - 1 + v


def sps_id(nal: bytes) -> int:
    return _Bits(_unescape(nal)[4:]).ue()          # after nal header, profile, constraints, level


def pps_ids(nal: bytes) -> "tuple[int, int]":
    b = _Bits(_unescape(nal)[1:])
    return b.ue(), b.ue()                          # (pps id, sps id)


@dataclass
class AvcC:
    profile: int
    compat: int
    level: int
    nal_len: int
    sps: list
    pps: list
    trailing: bytes = b""

    @classmethod
    def parse(cls, data: bytes) -> "AvcC":
        data = bytes(data or b"")
        if len(data) < 7 or data[0] != 1:
            raise Unsupported("no avcC (Annex B extradata)")
        nal_len = (data[4] & 3) + 1
        i = 5
        n = data[i] & 0x1F
        i += 1
        sps = []
        for _ in range(n):
            ln = int.from_bytes(data[i:i + 2], "big")
            sps.append(data[i + 2:i + 2 + ln])
            i += 2 + ln
        n = data[i]
        i += 1
        pps = []
        for _ in range(n):
            ln = int.from_bytes(data[i:i + 2], "big")
            pps.append(data[i + 2:i + 2 + ln])
            i += 2 + ln
        if not sps or not pps:
            raise Unsupported("avcC without SPS/PPS")
        return cls(data[1], data[2], data[3], nal_len, sps, pps, data[i:])

    def build(self) -> bytes:
        out = bytearray([1, self.profile, self.compat, self.level, 0xFC | (self.nal_len - 1), 0xE0 | len(self.sps)])
        for s in self.sps:
            out += len(s).to_bytes(2, "big") + s
        out.append(len(self.pps))
        for p in self.pps:
            out += len(p).to_bytes(2, "big") + p
        out += self.trailing
        return bytes(out)

    def sps_ids(self) -> set:
        return {sps_id(s) for s in self.sps}

    def pps_ids(self) -> set:
        return {pps_ids(p)[0] for p in self.pps}


def nal_units(data: bytes, nal_len: int):
    i, n = 0, len(data)
    while i + nal_len <= n:
        ln = int.from_bytes(data[i:i + nal_len], "big")
        i += nal_len
        if ln <= 0 or i + ln > n:
            return
        yield data[i:i + ln]
        i += ln


def reframe(data: bytes, nal_from: int, nal_to: int) -> bytes:
    if nal_from == nal_to:
        return data
    return b"".join(len(u).to_bytes(nal_to, "big") + u for u in nal_units(data, nal_from))


def is_idr(data: bytes, nal_len: int) -> bool:
    return any((u[0] & 0x1F) == 5 for u in nal_units(data, nal_len) if u)


def merge_avcc(sets: "list[AvcC]") -> AvcC:
    """One avcC holding every parameter set (deduplicated; an id used twice
    with different contents can't be merged -> Unsupported)."""
    sps: dict = {}
    pps: dict = {}
    for s in sets:
        for x in s.sps:
            i = sps_id(x)
            if sps.setdefault(i, x) != x:
                raise Unsupported(f"two different SPS with id {i}")
        for x in s.pps:
            i = pps_ids(x)[0]
            if pps.setdefault(i, x) != x:
                raise Unsupported(f"two different PPS with id {i}")
    top = max(sets, key=lambda a: (a.profile, a.level))
    trailing = top.trailing if top.profile in (100, 110, 122, 144) else b""
    if top.profile in (100, 110, 122, 144) and not trailing:
        trailing = bytes([0xFD, 0xF8, 0xF8, 0x00])             # 4:2:0, 8-bit, no SPS-ext
    return AvcC(top.profile, min(a.compat for a in sets), max(a.level for a in sets), 4,
                list(sps.values()), list(pps.values()), trailing)


# =========================================================================
# probing
# =========================================================================
@dataclass
class VideoInfo:
    path: str
    codec: str
    width: int
    height: int
    pix_fmt: str
    rate: Fraction
    time_base: Fraction
    start: float
    duration: float
    avcc: "AvcC | None"
    color: dict
    has_audio: bool
    extradata: bytes = b""


_info_cache: dict = {}
_info_lock = threading.Lock()


def video_info(path) -> VideoInfo:
    path = str(path)
    try:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime_ns)
    except OSError as e:
        raise Unsupported(str(e)) from e
    with _info_lock:
        if key in _info_cache:
            return _info_cache[key]
    with av.open(path) as c:
        if not c.streams.video:
            raise Unsupported("no video stream")
        vs = c.streams.video[0]
        cc = vs.codec_context
        rate = vs.average_rate or vs.guessed_rate
        if not rate:
            raise Unsupported("unknown frame rate")
        avcc = None
        if cc.name == "h264":
            try:
                avcc = AvcC.parse(cc.extradata)
            except Unsupported:
                avcc = None
        dur = float(vs.duration * vs.time_base) if vs.duration else (c.duration or 0) / 1e6
        start = float(vs.start_time * vs.time_base) if vs.start_time is not None else 0.0
        info = VideoInfo(path, cc.name, cc.width, cc.height, cc.pix_fmt or "", Fraction(rate), Fraction(vs.time_base),
                         start, dur, avcc,
                         {"color_range": int(cc.color_range or 0), "colorspace": int(cc.colorspace or 2),
                          "color_primaries": int(cc.color_primaries or 2), "color_trc": int(cc.color_trc or 2)},
                         bool(c.streams.audio), bytes(cc.extradata or b""))
    with _info_lock:
        if len(_info_cache) > 64:
            _info_cache.clear()
        _info_cache[key] = info
    return info


def eligible(info: VideoInfo) -> "str | None":
    """None when the file's video can be smart-cut, else why not."""
    if info.codec != "h264":
        return f"codec {info.codec} (only H.264 is stream-copied)"
    if info.avcc is None:
        return "H.264 without an avcC header"
    if info.pix_fmt not in ("yuv420p", "yuvj420p"):
        return f"pixel format {info.pix_fmt}"
    if info.avcc.nal_len not in (1, 2, 4):
        return "unusual NAL length size"
    if info.width % 2 or info.height % 2:
        return "odd frame size"
    return None


@dataclass
class Gop:
    pts: float          # keyframe presentation time (s)
    clean: bool         # an IDR with no leading pictures: a valid cut-in point
    n_packets: int = 0
    max_pts: float = 0.0


def scan_gops(path, t0: float, t1: "float | None") -> "list[Gop]":
    """Keyframes from the one at-or-before t0 up to the first one after t1
    (None = to the end), reading packet headers only (no decoding). Uses the
    container index to start near t0, so a 20-minute replay buffer costs the
    same as a 20-second one."""
    info = video_info(path)
    out: list = []
    with av.open(str(path)) as c:
        vs = c.streams.video[0]
        tb = vs.time_base
        try:
            c.seek(max(0, int((t0 - 0.001) / tb)), stream=vs, backward=True, any_frame=False)
        except av.error.FFmpegError:
            c.seek(0, stream=vs)
        nal_len = info.avcc.nal_len if info.avcc else 4
        cur = None
        for pkt in c.demux(vs):
            if pkt.pts is None:
                if pkt.dts is None:
                    continue
                raise Unsupported("packet without timestamp")
            pts = float(pkt.pts * tb)
            if pkt.is_keyframe:
                if t1 is not None and pts > t1 + 1e-6 and cur is not None:
                    out.append(cur)
                    cur = Gop(pts, False)
                    break
                if cur is not None:
                    out.append(cur)
                cur = Gop(pts, is_idr(bytes(pkt), nal_len), 0, pts)
            elif cur is not None and pts < cur.pts - 1e-6:
                cur.clean = False                         # a leading picture: open GOP
            if cur is not None:
                cur.n_packets += 1
                cur.max_pts = max(cur.max_pts, pts)
        if cur is not None and (not out or out[-1] is not cur):
            out.append(cur)
    return out


def cut_points(path, t0: float, t1: "float | None", fd: float) -> "tuple[Gop | None, Gop | None]":
    """(first clean keyframe at/after t0, last clean keyframe after that one
    at/before t1) -- None where there is none (t1 None = to the end: no
    second one needed). Scans only short windows around the two ends, not
    everything in between (GOPs in the middle are copied whole, so they
    don't need to be clean cut points)."""
    end = t1 if t1 is not None else float("inf")
    first = None
    win = 6.0
    a = t0
    while first is None:
        b = min(end, a + win)
        gops = scan_gops(path, a, b)
        first = next((g for g in gops if g.clean and t0 - fd * 0.5 <= g.pts <= end - fd * 0.5 + 1e-9), None)
        if first is not None or not gops or b >= end or gops[-1].pts <= a + 1e-6 and len(gops) == 1:
            break
        nxt = max(g.pts for g in gops)
        if nxt <= a + 1e-6:
            break
        a, win = nxt, win * 3
    if first is None or t1 is None:
        return first, None
    last = None
    win = 6.0
    while last is None:
        a = max(first.pts + fd * 0.5, t1 - win)
        gops = scan_gops(path, a, t1)
        cands = [g for g in gops if g.clean and first.pts + fd * 0.5 < g.pts <= t1 - fd * 0.5 + 1e-9]
        if cands:
            last = cands[-1]
            break
        if a <= first.pts + fd * 0.5 + 1e-9:
            break
        win *= 3
    return first, last


# =========================================================================
# encoding chunks
# =========================================================================
ENCODER_VERSION = "x264-chunk-1"


def pick_sps_id(used: "set[int]") -> int:
    for i in (7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 31, 5, 3, 1):
        if i not in used:
            return i
    raise Unsupported("no free SPS/PPS id")


SWS_COLORSPACE = {1: "ITU709", 4: "FCC", 5: "ITU601", 6: "ITU601", 7: "SMPTE240M", 9: "BT2020", 10: "BT2020"}


def color_pix_fmt(color: "dict | None") -> str:
    """yuvj420p for full-range video (that's how libavcodec labels it), else yuv420p."""
    return "yuvj420p" if (color or {}).get("color_range") == 2 else "yuv420p"


def to_output_yuv(frame, color: "dict | None"):
    """An RGB(A) or YUV frame -> the output's YUV: the SAME matrix and range
    the source video uses (what decoding it to RGB used), so re-encoded
    pictures match copied/passed-through ones exactly in colour. (Plain
    reformat(format="yuv420p") converts RGB with BT.601 -- on BT.709 HD
    video that shifted every re-rendered frame visibly.)"""
    fmt = color_pix_fmt(color)
    if frame.format.name == fmt:
        return frame
    cs = SWS_COLORSPACE.get(int((color or {}).get("colorspace", 2) or 2))
    if frame.format.name in ("yuv420p", "yuvj420p"):
        return frame.reformat(format=fmt)
    return frame.reformat(format=fmt, dst_colorspace=cs) if cs else frame.reformat(format=fmt)


def new_encoder(out, rate: Fraction, width: int, height: int, crf: int, preset: str, sps: int,
                color: "dict | None" = None, threads: "int | None" = None):
    vs = out.add_stream("libx264", rate=rate)
    vs.width, vs.height = width, height
    vs.pix_fmt = color_pix_fmt(color)
    opts = {"crf": str(crf), "preset": preset, "x264-params": f"sps-id={sps}:keyint=infinite:scenecut=0"}
    if threads:
        opts["threads"] = str(threads)
    vs.options = opts
    vs.thread_type = "AUTO"
    cc = vs.codec_context
    for k, v in (color or {}).items():
        try:
            setattr(cc, k, v)
        except (AttributeError, ValueError, TypeError):
            pass
    return vs


def encode_frames(frames, path: str, rate: Fraction, width: int, height: int, crf: int, preset: str, sps: int,
                  color: "dict | None" = None, threads: "int | None" = None, cancel=None, on_frame=None) -> int:
    """Encode an iterable of av.VideoFrame (YUV or RGB, already the right size)
    into an MP4 chunk file, pts 0,1,2... in frames. Returns the frame count."""
    n = 0
    tb = Fraction(rate.denominator, rate.numerator)
    with av.open(path, "w", format="mp4") as out:
        vs = new_encoder(out, rate, width, height, crf, preset, sps, color, threads)
        for f in frames:
            if cancel is not None and cancel.is_set():
                raise _cancelled()
            f = to_output_yuv(f, color)
            f.pts = n
            f.time_base = tb
            for pkt in vs.encode(f):
                out.mux(pkt)
            n += 1
            if on_frame is not None:
                on_frame()
        for pkt in vs.encode(None):
            out.mux(pkt)
    return n


def _cancelled():
    try:
        from .nle.render import ExportCancelled
        return ExportCancelled()
    except Exception:  # noqa: BLE001
        return RuntimeError("cancelled")


def decoded_frames(path, t0: float, n: int, rate: Fraction):
    """n consecutive source frames starting at the frame shown at t0 (CFR)."""
    with av.open(str(path)) as c:
        vs = c.streams.video[0]
        vs.thread_type = "AUTO"
        tb = vs.time_base
        half = 0.5 / float(rate)
        c.seek(max(0, int((t0 - 0.001) / tb)), stream=vs, backward=True, any_frame=False)
        got = 0
        for f in c.decode(vs):
            if f.pts is None:
                continue
            if float(f.pts * tb) < t0 - half:
                continue
            yield f
            got += 1
            if got >= n:
                return


# =========================================================================
# muxing chunks
# =========================================================================
@dataclass
class CopyChunk:
    """Source packets of whole closed GOPs: those with start <= pts < end
    (end None = to the end of the file), shown from output frame `at`."""
    path: str
    start: float
    end: "float | None"
    at: int
    n: int


@dataclass
class FileChunk:
    """An encoded chunk file (pts in frames from 0), shown from output frame `at`.
    `offset` frames from the start of the file (a GOP inside a longer file)."""
    path: str
    at: int
    n: int
    offset: int = 0
    only_sps: "int | None" = None      # use only this SPS/PPS id's parameter sets (a chunk of a previous export)


@dataclass
class _Plan:
    avcc: AvcC = None
    sets: dict = field(default_factory=dict)


def _chunk_avcc(chunk) -> AvcC:
    with av.open(chunk.path) as c:
        a = AvcC.parse(c.streams.video[0].codec_context.extradata)
    if getattr(chunk, "only_sps", None) is not None:
        sps = [x for x in a.sps if sps_id(x) == chunk.only_sps]
        pps = [x for x in a.pps if pps_ids(x)[0] == chunk.only_sps]
        if not sps or not pps:
            raise Unsupported("previous export lacks its parameter sets")
        a = AvcC(a.profile, a.compat, a.level, a.nal_len, sps, pps, a.trailing)
    return a


def _chunk_packets(chunk, rate: Fraction):
    """(decode-order) packets of a chunk as (bytes, out_frame_pts, is_key)."""
    fd = 1.0 / float(rate)
    with av.open(chunk.path) as c:
        vs = c.streams.video[0]
        tb = vs.time_base
        if isinstance(chunk, CopyChunk):
            c.seek(max(0, int((chunk.start - 0.001) / tb)), stream=vs, backward=True, any_frame=False)
            started = False
            for pkt in c.demux(vs):
                if pkt.pts is None:
                    continue
                pts = float(pkt.pts * tb)
                if not started:
                    if not (pkt.is_keyframe and abs(pts - chunk.start) < fd * 0.5):
                        if pts > chunk.start + fd * 0.5 and pkt.is_keyframe:
                            raise Unsupported("cut keyframe not found")
                        continue
                    started = True
                if chunk.end is not None and pkt.is_keyframe and pts >= chunk.end - fd * 0.5:
                    return
                k = int(round((pts - chunk.start) * float(rate)))
                if k < 0 or k >= chunk.n:
                    raise Unsupported("copied packet outside its GOP")
                yield bytes(pkt), chunk.at + k, pkt.is_keyframe
        else:
            started = chunk.offset == 0
            if chunk.offset > 0:
                c.seek(max(0, int((chunk.offset / float(rate) - 0.001) / tb)), stream=vs, backward=True,
                       any_frame=False)
            for pkt in c.demux(vs):
                if pkt.pts is None:
                    continue
                k = int(round(float(pkt.pts * tb) * float(rate))) - chunk.offset
                if not started:
                    if k == 0 and pkt.is_keyframe:
                        started = True
                    else:
                        if k > 0 and pkt.is_keyframe:
                            raise Unsupported("chunk start not found in the previous export")
                        continue
                if k < 0 or k >= chunk.n:
                    if chunk.offset > 0 or k >= chunk.n:
                        return                     # the next chunk of that file begins
                    continue
                yield bytes(pkt), chunk.at + k, pkt.is_keyframe


@dataclass
class AudioCut:
    """Every audio stream of `path`, copied (not re-encoded) from `start` to
    `end` (None = to the end) source seconds. A packet or two before `start`
    is kept, timestamped before 0, so the decoder warms up and the edit list
    (MP4) / timestamps (MKV) line the first audible sample up exactly."""
    path: str
    start: float
    end: "float | None"
    first_only: bool = False         # only the first audio stream (the one the Editor mixes)


def mux_chunks(dst: str, chunks: list, rate: Fraction, template_info: VideoInfo, audio: list = (),
               fmt: "str | None" = None) -> None:
    """Write `chunks` (in output order, frames contiguous from 0) as one video
    stream + audio: each entry of `audio` is a file whose audio streams are
    copied whole, or an AudioCut."""
    sets, by_chunk = [], []
    for ch in chunks:
        a = template_info.avcc if isinstance(ch, CopyChunk) and ch.path == template_info.path else \
            (video_info(ch.path).avcc if isinstance(ch, CopyChunk) else _chunk_avcc(ch))
        if a is None:
            raise Unsupported("chunk without avcC")
        sets.append(a)
        by_chunk.append(a)
    merged = merge_avcc(sets)
    total = sum(ch.n for ch in chunks)
    tb = Fraction(rate.denominator, rate.numerator)

    # pass 1: decode-order -> display-order offset, for the DTS restamp
    # (dts = decode index - D, D = max reorder delay over all chunks)
    def all_video():
        i = 0
        for ch, a in zip(chunks, by_chunk):
            first = True
            for data, pts, key in _chunk_packets(ch, rate):
                data = reframe(data, a.nal_len, 4)
                if first:
                    sets = a.sps + a.pps
                    units = list(nal_units(data, 4))
                    while units and units[0] and (units[0][0] & 0x1F) in (7, 8) and units[0] in sets:
                        units.pop(0)                       # already carried (a previous export's chunk)
                    data = b"".join(len(u).to_bytes(4, "big") + u for u in sets + units)
                    first = False
                yield i, data, pts, key
                i += 1

    delay = 0
    count = 0
    seen = set()
    for i, _d, pts, _k in _meta(chunks, rate):
        delay = max(delay, i - pts)
        count += 1
        seen.add(pts)
    if count != total or len(seen) != total:
        raise Unsupported(f"frame count mismatch ({count} packets / {len(seen)} frames for {total})")

    with av.open(dst, "w", format=fmt) as out:
        with av.open(template_info.path) as tin:
            ovs = out.add_stream_from_template(tin.streams.video[0])
        ovs.codec_context.extradata = merged.build()
        ovs.time_base = tb
        ains, aouts = [], []
        for ap in audio:
            cut = ap if isinstance(ap, AudioCut) else None
            ain = av.open(cut.path if cut else ap)
            for ast in (ain.streams.audio[:1] if cut is not None and cut.first_only else ain.streams.audio):
                aouts.append((ain, ast, out.add_stream_from_template(ast), cut))
            ains.append(ain)

        def video_packets():
            for i, data, pts, key in all_video():
                pkt = av.Packet(data)
                pkt.pts = pts
                pkt.dts = i - delay
                pkt.time_base = tb
                pkt.is_keyframe = key
                pkt.stream = ovs
                yield float((i - delay) * tb), pkt

        def audio_packets(ain, ast, aout, cut):
            if cut is None:
                for pkt in ain.demux(ast):
                    if pkt.dts is None:
                        continue
                    pkt.stream = aout
                    yield float(pkt.dts * ast.time_base), pkt
                return
            with av.open(cut.path) as c2:                 # own demuxer per stream: seek independently
                st2 = next(x for x in c2.streams if x.index == ast.index)
                tb = st2.time_base
                c2.seek(max(0, int((cut.start - 1.0) / tb)), stream=st2, backward=True)
                off = int(round(cut.start / tb))
                pending: list = []
                for pkt in c2.demux(st2):
                    if pkt.pts is None:
                        continue
                    t = float(pkt.pts * tb)
                    dur = float((pkt.duration or 0) * tb)
                    if cut.end is not None and t >= cut.end - 1e-9:
                        break
                    if t + dur <= cut.start + 1e-9:
                        pending = (pending + [pkt])[-2:]          # warm-up packets before the cut
                        continue
                    for q in pending + [pkt]:
                        q.pts -= off
                        q.dts = q.pts if q.dts is None else q.dts - off
                        q.stream = aout
                        yield float(q.dts * tb), q
                    pending = []

        sources = [video_packets()] + [audio_packets(*x) for x in aouts]
        # (the video's own timeline starts at 0; copied audio may start a
        # little before 0 -- the warm-up packets the edit list skips)
        try:
            for _t, pkt in heapq.merge(*sources, key=lambda x: x[0]):
                out.mux(pkt)
        finally:
            for ain in ains:
                ain.close()


def _meta(chunks, rate):
    i = 0
    for ch in chunks:
        for _data, pts, key in _chunk_packets(ch, rate):
            yield i, None, pts, key
            i += 1


# =========================================================================
# trim
# =========================================================================
def _audio_trim(src: str, dst: str, start: float, dur: float) -> None:
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.6f}", "-i", src, "-t", f"{dur:.6f}",
                        "-map", "0:a", "-vn", "-c:a", "aac", "-b:a", "192k", dst], capture_output=True, text=True)
    if r.returncode != 0:
        raise Unsupported(f"audio trim failed: {r.stderr[-300:]}")


def smart_trim(src, dst, start: float, end: float, crf: int = 18, preset: str = "veryfast",
               progress=None) -> dict:
    """Frame-exact trim of src to [start, end) into dst, re-encoding only the
    partial GOPs at the two ends. Returns stats ({"encoded", "copied"}
    frames). Raises Unsupported when it can't (caller re-encodes)."""
    src, dst = str(src), str(dst)
    info = video_info(src)
    why = eligible(info)
    if why:
        raise Unsupported(why)
    rate = info.rate
    fd = 1.0 / float(rate)
    t0 = info.start + start
    t1 = info.start + end
    last_frame = info.start + info.duration - fd
    to_eof = t1 >= last_frame + fd * 0.5 - 1e-6

    # frame grid (CFR): output frame k shows the source frame at first + k*fd
    first = None
    with av.open(src) as c:                                # exact pts of the first frame >= t0
        vs = c.streams.video[0]
        c.seek(max(0, int((t0 - 0.001) / vs.time_base)), stream=vs, backward=True, any_frame=False)
        cand = []
        for pkt in c.demux(vs):
            if pkt.pts is None:
                continue
            p = float(pkt.pts * vs.time_base)
            cand.append(p)
            if pkt.is_keyframe and p > t0 + 10 and len(cand) > 1:
                break
            if len(cand) > 2000:
                break
        after = sorted(x for x in cand if x >= t0 - fd * 0.5)
        if not after:
            raise Unsupported("no frame at the start point")
        first = after[0]
    if to_eof:
        n_total = int(round((last_frame - first) * float(rate))) + 1
    else:
        n_total = int(math.ceil((t1 - first) * float(rate) - 1e-6))
    if n_total <= 0:
        raise Unsupported("empty range")
    end_t = first + n_total * fd

    cut_in, k2_found = cut_points(src, first, None if to_eof else end_t, fd)
    if cut_in is not None and cut_in.pts >= end_t - fd * 0.5:
        cut_in = None
    chunks: list = []
    tmpdir = tempfile.mkdtemp(prefix="afterglow_smartcut_", dir=str(Path(dst).parent))
    stats = {"encoded": 0, "copied": 0}
    used = (info.avcc.sps_ids() | info.avcc.pps_ids())
    sps = pick_sps_id(used)
    color = dict(info.color)
    w, h = info.width, info.height
    try:
        audio_path = os.path.join(tmpdir, "audio.m4a")

        def enc(t_from: float, n: int, name: str) -> FileChunk:
            path = os.path.join(tmpdir, name)
            got = encode_frames(decoded_frames(src, t_from, n, rate), path, rate, w, h, crf, preset, sps, color)
            if got != n:
                raise Unsupported(f"decoded {got} of {n} frames")
            return path

        if cut_in is None:
            p = enc(first, n_total, "all.mp4")
            chunks.append(FileChunk(p, 0, n_total))
            stats["encoded"] = n_total
        else:
            head_n = int(round((cut_in.pts - first) * float(rate)))
            if head_n > 0:
                chunks.append(FileChunk(enc(first, head_n, "head.mp4"), 0, head_n))
                stats["encoded"] += head_n
            # the copied run: whole clean GOPs from cut_in; ends at EOF or at the last
            # clean keyframe at or before the end, the rest re-encoded as the tail
            if to_eof:
                copy_end, tail_n = None, 0
                copy_n = n_total - head_n
            else:
                k2 = k2_found
                if k2 is None:
                    # one partial GOP only: copy nothing, encode the rest
                    copy_end = cut_in.pts
                else:
                    copy_end = k2.pts
                copy_n = int(round((copy_end - cut_in.pts) * float(rate)))
                tail_n = n_total - head_n - copy_n
            if copy_n > 0:
                chunks.append(CopyChunk(src, cut_in.pts, copy_end, head_n, copy_n))
                stats["copied"] = copy_n
            if tail_n > 0:
                chunks.append(FileChunk(enc(copy_end, tail_n, "tail.mp4"), head_n + copy_n, tail_n))
                stats["encoded"] += tail_n
        tmp_out = os.path.join(tmpdir, "out" + Path(dst).suffix)
        a_end = None if to_eof else first + n_total * fd
        try:
            # audio copied as-is (exactly aligned, no re-encode)
            mux_chunks(tmp_out, chunks, rate, info,
                       [AudioCut(src, first, a_end)] if info.has_audio else [], fmt=_fmt_for(dst))
        except Exception:  # noqa: BLE001 -- an audio codec/container combination that won't copy
            if not info.has_audio:
                raise
            _audio_trim(src, audio_path, start, n_total * fd)
            mux_chunks(tmp_out, chunks, rate, info, [audio_path], fmt=_fmt_for(dst))
        os.replace(tmp_out, dst)
        return stats
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _fmt_for(path: str) -> "str | None":
    ext = Path(path).suffix.lower()
    return {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".mkv": "matroska", ".webm": "webm"}.get(ext)
