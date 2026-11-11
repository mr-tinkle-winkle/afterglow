"""
Blend a transparent RGB overlay straight into a decoded YUV 4:2:0 frame.

The export's usual "something drawn over the clip" frame -- a caption, a
bubble, a sticker over an otherwise untouched full-frame video -- used to go
decoded YUV -> RGB (swscale) -> QImage -> paint -> RGB -> YUV (swscale) at
the full frame size, although only the few pixels under the overlay change.
At 2560x1440 the two conversions and the full-frame copies around them were
about two thirds of the time per rendered frame.

Here the video frame stays in YUV: only the overlay's bounding box is
converted (numpy) and alpha-blended into the Y, U and V planes. Everything
outside the box is the source frame's own pixels, bit for bit -- so this is
both faster and lossless where nothing is drawn.

Overlay pixels are premultiplied BGRA (QImage.Format_ARGB32_Premultiplied on
a little-endian machine): out = base * (1 - a) + overlay.
"""
from __future__ import annotations

import numpy as np

# (Kr, Kb) per libswscale colourspace name (smartcut.SWS_COLORSPACE values)
_KR_KB = {
    "ITU709": (0.2126, 0.0722),
    "ITU601": (0.299, 0.114),
    "FCC": (0.30, 0.11),
    "SMPTE240M": (0.212, 0.087),
    "BT2020": (0.2627, 0.0593),
}


def bounding_box(overlay: np.ndarray) -> "tuple[int, int, int, int] | None":
    """(x0, y0, x1, y1) of the non-transparent pixels of a premultiplied BGRA
    array (h, w, 4), grown to even coordinates; None when it is empty. A fully
    transparent premultiplied pixel is four zero bytes, so the test runs on
    whole machine words (two pixels per compare when the width is even)."""
    h, w = overlay.shape[:2]
    if w % 2 == 0:
        px, per = overlay.reshape(h, w * 4).view(np.uint64), 2
    else:
        px, per = overlay.reshape(h, w * 4).view(np.uint32), 1
    rows = np.flatnonzero(px.any(axis=1))
    if rows.size == 0:
        return None
    y0, y1 = int(rows[0]), int(rows[-1]) + 1
    cols = np.flatnonzero(px[y0:y1].any(axis=0))
    x0, x1 = int(cols[0]) * per, (int(cols[-1]) + 1) * per
    x0 -= x0 % 2
    y0 -= y0 % 2
    x1 += x1 % 2
    y1 += y1 % 2
    return x0, y0, min(x1, w), min(y1, h)


def blend_into_planes(y: np.ndarray, u: np.ndarray, v: np.ndarray, overlay: np.ndarray,
                      box: "tuple[int, int, int, int]", matrix: str = "ITU601", full_range: bool = False) -> None:
    """Blend `overlay`'s `box` into the three planes of a 4:2:0 picture (`y`
    full size, `u` / `v` half size; uint8, writable). In place."""
    x0, y0, x1, y1 = box
    kr, kb = _KR_KB.get(matrix, _KR_KB["ITU601"])
    kg = 1.0 - kr - kb
    reg = overlay[y0:y1, x0:x1].astype(np.float32)
    b, g, r = reg[..., 0], reg[..., 1], reg[..., 2]
    a = reg[..., 3] * np.float32(1.0 / 255.0)
    luma = kr * r + kg * g + kb * b                      # premultiplied: 0 .. 255 * a
    y_scale, c_scale, y_off = (1.0, 1.0, 0.0) if full_range else (219.0 / 255.0, 224.0 / 255.0, 16.0)

    yp = y[y0:y1, x0:x1]
    out = yp.astype(np.float32) * (1.0 - a) + (y_off * a + y_scale * luma)
    np.clip(out + 0.5, 0, 255, out=out)
    yp[...] = out.astype(np.uint8)

    # chroma: 2x2 box average of the (premultiplied) overlay and its alpha
    ch, cw = (y1 - y0) // 2, (x1 - x0) // 2
    if ch <= 0 or cw <= 0:
        return

    def half(p):
        p = p[:ch * 2, :cw * 2]
        return (p[0::2, 0::2] + p[0::2, 1::2] + p[1::2, 0::2] + p[1::2, 1::2]) * np.float32(0.25)

    a2, l2 = half(a), half(luma)
    cb = (half(b) - l2) / (2.0 * (1.0 - kb))
    cr = (half(r) - l2) / (2.0 * (1.0 - kr))
    cy0, cx0 = y0 // 2, x0 // 2
    for plane, c in ((u, cb), (v, cr)):
        pp = plane[cy0:cy0 + ch, cx0:cx0 + cw]
        o = pp.astype(np.float32) * (1.0 - a2) + (128.0 * a2 + c_scale * c)
        np.clip(o + 0.5, 0, 255, out=o)
        pp[...] = o.astype(np.uint8)


def _plane_view(plane, rows: int, cols: int) -> np.ndarray:
    return np.frombuffer(plane, np.uint8).reshape(-1, plane.line_size)[:rows, :cols]


def blend_onto_frame(frame, overlay: np.ndarray, matrix: str = "ITU601", box=None):
    """A new av.VideoFrame: `frame` (yuv420p / yuvj420p, never modified -- the
    decoder may hand the same frame out again) with `overlay` blended in.
    Returns `frame` itself when the overlay is empty. Raises ValueError for
    anything it doesn't handle (the caller renders the frame the long way)."""
    import av
    name = frame.format.name
    if name not in ("yuv420p", "yuvj420p"):
        raise ValueError(f"unsupported pixel format {name}")
    w, h = frame.width, frame.height
    if w % 2 or h % 2 or overlay.shape[0] != h or overlay.shape[1] != w:
        raise ValueError("size mismatch")
    if box is None:
        box = bounding_box(overlay)
    if box is None:
        return frame
    out = av.VideoFrame(w, h, name)
    dims = ((h, w), (h // 2, w // 2), (h // 2, w // 2))
    try:
        planes = []
        for i, (rows, cols) in enumerate(dims):
            dst = _plane_view(out.planes[i], rows, cols)
            dst[...] = _plane_view(frame.planes[i], rows, cols)      # one copy of the picture
            planes.append(dst)
    except (ValueError, TypeError) as e:      # a plane layout numpy can't view / write
        raise ValueError(str(e)) from None
    blend_into_planes(planes[0], planes[1], planes[2], overlay, box, matrix, full_range=(name == "yuvj420p"))
    for attr in ("colorspace", "color_range", "pts", "time_base"):
        try:
            setattr(out, attr, getattr(frame, attr))
        except Exception:  # noqa: BLE001 -- older PyAV: not settable; the stream is tagged anyway
            pass
    return out
