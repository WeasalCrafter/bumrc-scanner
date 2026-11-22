"""Find every barcode / DataMatrix in a camera frame.

One pass over the raw frame misses codes that are slightly blurry, tilted or
badly printed, so each frame is decoded several times with different clean-ups
(threshold, sharpen, "heal" blurs...). The passes run in parallel threads
(zxing-cpp releases the GIL, so this uses all cores). Results are merged:

- a small tilt (a few degrees) breaks long 1D codes, so the frame is
  straightened first and code positions are mapped back afterwards
- heavily shrunk copies only look for 2D codes: they help find big-module
  DataMatrix codes, but narrow 1D bars turn sub-pixel and misread
- each code remembers which frames it was seen in; see `confirmed()` for why
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable

import cv2
import numpy as np
import zxingcpp

F = zxingcpp.BarcodeFormat
FORMATS = [F.DataMatrix, F.QRCode, F.Code128, F.Code39, F.Code93, F.EAN13, F.UPCA, F.PDF417]
FORMATS_2D = [F.DataMatrix, F.QRCode, F.PDF417]
TWO_D = {"DataMatrix", "QRCode", "PDF417"}
MIN_1D_LENGTH = 2        # quantities ("47") and country codes ("JP") are only 2 characters;
                         # 1-character reads are almost always fragments of a bigger code
SKEW_MIN_DEGREES = 0.3   # below this, straightening isn't worth the time
UPSCALE_BELOW_WIDTH = 2500   # only small images (old crops, phone shots) get an upscaled pass
WORKERS = max(1, min(3, (os.cpu_count() or 2) - 1))   # Jetson Nano: 4 cores -> 3 workers


@dataclass
class Code:
    format: str             # "DataMatrix", "Code128", ...
    raw: bytes              # exact decoded bytes (keeps the GS/RS separators intact)
    vertical: bool          # printed sideways on the label
    hits: int = 1           # how many passes read it (several per frame)
    found_by: list[str] = field(default_factory=list)
    corners: list[tuple[int, int]] | None = None   # where it is in the image (4 points, original pixels)
    frames: set[int] = field(default_factory=set)  # which camera frames it was read in

    @property
    def text(self) -> str:
        return self.raw.decode("latin-1")

    @property
    def is_2d(self) -> bool:
        return self.format in TWO_D

    def to_dict(self) -> dict:
        return {"format": self.format, "text": self.text, "vertical": self.vertical, "hits": self.hits}


# ---------------------------------------------------------------- image clean-ups

def _gray(img: np.ndarray) -> np.ndarray:
    return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _scale(s: float) -> Callable[[np.ndarray], np.ndarray]:
    interp = cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC
    return lambda g: cv2.resize(g, None, fx=s, fy=s, interpolation=interp)


def _otsu(g):
    return cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]


def _adaptive(g):
    return cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 10)


def _clahe(g):
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)   # cheap to create; not safe to share across threads


def _sharpen(g):
    return cv2.addWeighted(g, 1.8, cv2.GaussianBlur(g, (0, 0), 3), -0.8, 0)


def _heal_vertical(g):
    """Blur along vertical bars: fills thermal-printhead streaks and broken bars."""
    return cv2.blur(g, (1, 9))


def _heal_horizontal(g):
    """Same for sideways barcodes (horizontal bars)."""
    return cv2.blur(g, (9, 1))


LA, GH = zxingcpp.Binarizer.LocalAverage, zxingcpp.Binarizer.GlobalHistogram


@dataclass(frozen=True)
class Pass:
    name: str
    fn: Callable[[np.ndarray], np.ndarray]
    binarizer: zxingcpp.Binarizer
    two_d_only: bool = False


# Full-size passes read everything. Shrunk passes only look for 2D codes.
FAST_PASSES = [
    Pass("gray", lambda g: g, LA),
    Pass("gray", lambda g: g, GH),
    Pass("otsu", _otsu, LA),
    Pass("scale0.75", _scale(0.75), GH),
    Pass("scale0.5", _scale(0.5), GH, two_d_only=True),
    Pass("scale0.3", _scale(0.3), GH, two_d_only=True),
]
THOROUGH_EXTRA = [
    Pass("otsu", _otsu, GH),
    Pass("scale0.75", _scale(0.75), LA),
    Pass("scale0.25", _scale(0.25), LA, two_d_only=True),
    Pass("clahe", _clahe, LA),
    Pass("adaptive", _adaptive, LA),
    Pass("sharpen", _sharpen, GH),
    Pass("healV", _heal_vertical, LA),
    Pass("healH", _heal_horizontal, LA),
    Pass("median3", lambda g: cv2.medianBlur(g, 3), GH),
]
UPSCALE_PASSES = [Pass("scale1.5", _scale(1.5), LA), Pass("scale1.5", _scale(1.5), GH)]


def passes_for(width: int, thorough: bool) -> list[Pass]:
    out = list(FAST_PASSES)
    if thorough:
        out += THOROUGH_EXTRA
        if width < UPSCALE_BELOW_WIDTH:      # at 8MP bars are already ~2.5 px wide; upscaling only costs time
            out += UPSCALE_PASSES
    return out


# ---------------------------------------------------------------- straightening

def estimate_skew(g: np.ndarray) -> float:
    """Tilt of the label in degrees (-45..45), from the direction of the strongest edges.
    Barcode bars give a sharp peak; vertical and horizontal bars agree once folded to 90°."""
    h, w = g.shape
    if w > 1000:
        g = cv2.resize(g, (1000, int(h * 1000 / w)), interpolation=cv2.INTER_AREA)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    strong = mag > np.percentile(mag, 95)
    if strong.sum() < 100:
        return 0.0
    ang = np.degrees(np.arctan2(gy[strong], gx[strong]))
    ang = (ang + 45) % 90 - 45                         # fold to [-45, 45)
    hist, edges = np.histogram(ang, bins=180, range=(-45, 45), weights=mag[strong])
    hist = np.convolve(hist, [1, 2, 3, 2, 1], mode="same")   # smooth, so the peak is stable
    i = int(np.argmax(hist))
    return float((edges[i] + edges[i + 1]) / 2)


def _rotation(g: np.ndarray, angle: float) -> tuple[np.ndarray, np.ndarray]:
    """(straightened image, matrix mapping its pixels back to the original).
    The canvas grows to fit, so codes near the frame corners aren't cut off."""
    h, w = g.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(w * cos + h * sin + 0.5), int(w * sin + h * cos + 0.5)
    m[0, 2] += (nw - w) / 2
    m[1, 2] += (nh - h) / 2
    rotated = cv2.warpAffine(g, m, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=255)
    return rotated, cv2.invertAffineTransform(m)


# ---------------------------------------------------------------- decoding

def _keep(r: zxingcpp.Barcode) -> bool:
    if not r.valid:
        return False
    if r.format.name in TWO_D:
        return True
    if len(r.bytes) < MIN_1D_LENGTH:
        return False
    # short reads of punctuation are noise; real 2-character codes are letters/digits
    return len(r.bytes) > 2 or r.bytes.isalnum()


def _run(p: Pass, g: np.ndarray, back: np.ndarray | None, tag: str) -> list[Code]:
    v = p.fn(g)
    k = g.shape[1] / v.shape[1]          # scaled passes: map positions back to the full image
    out = []
    for r in zxingcpp.read_barcodes(v, formats=FORMATS_2D if p.two_d_only else FORMATS, try_rotate=True,
                                    try_downscale=p.two_d_only, binarizer=p.binarizer):
        if not _keep(r):
            continue
        pos = r.position
        pts = np.array([(pt.x * k, pt.y * k) for pt in (pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left)],
                       dtype=np.float32)
        if back is not None:             # undo the straightening
            pts = pts @ back[:, :2].T + back[:, 2]
        vertical = abs(r.orientation) % 180 == 90
        out.append(Code(r.format.name, r.bytes, vertical, 1, [f"{tag}{p.name}/{p.binarizer.name[:5]}"],
                        [(int(x), int(y)) for x, y in pts]))
    return out


_pool: ThreadPoolExecutor | None = None


def _executor() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="decode")
    return _pool


def merge(codes: Iterable[Code]) -> list[Code]:
    """Combine reads of the same code (same format and bytes), adding up hits and frames."""
    out: dict[tuple[str, bytes], Code] = {}
    for c in codes:
        key = (c.format, c.raw)
        if key in out:
            out[key].hits += c.hits
            out[key].found_by += c.found_by
            out[key].frames |= c.frames
            out[key].corners = c.corners or out[key].corners    # newest position wins
        else:
            out[key] = Code(c.format, c.raw, c.vertical, c.hits, list(c.found_by), c.corners, set(c.frames))
    return sorted(out.values(), key=lambda c: (not c.is_2d, -c.hits))


def decode_frame(img: np.ndarray, thorough: bool = False, frame_id: int | None = None,
                 passes: list[Pass] | None = None) -> list[Code]:
    """All codes in one image (BGR or grayscale). `frame_id` tags each code with
    the camera frame it came from, for `confirmed()`."""
    g = _gray(img)
    todo = passes or passes_for(g.shape[1], thorough)
    jobs: list[tuple[Pass, np.ndarray, np.ndarray | None, str]] = []
    angle = estimate_skew(g)
    if abs(angle) >= SKEW_MIN_DEGREES:
        straight, back = _rotation(g, angle)
        jobs += [(p, straight, back, "deskew/") for p in todo]
        # a wrong skew estimate (labels with few bars) must not lose codes the raw image
        # has: always one pass on the raw frame, and all of them when thorough
        jobs += [(p, g, None, "") for p in (todo if thorough else todo[:1])]
    else:
        jobs += [(p, g, None, "") for p in todo]
    found = [c for codes in _executor().map(lambda j: _run(*j), jobs) for c in codes]
    if frame_id is not None:
        for c in found:
            c.frames = {frame_id}
    return merge(found)


def decode_frames(frames: Iterable[np.ndarray], thorough: bool = False) -> list[Code]:
    """All codes across several frames of the same scene (e.g. a 2 second scan)."""
    return merge(c for i, f in enumerate(frames) for c in decode_frame(f, thorough, frame_id=i))


# ---------------------------------------------------------------- which reads to trust

def _center(c: Code) -> tuple[float, float] | None:
    if not c.corners:
        return None
    return sum(x for x, _ in c.corners) / 4, sum(y for _, y in c.corners) / 4


def _near(c: Code, other: Code, margin: float = 10) -> bool:
    """c's centre is inside (or within `margin` px of) other's box."""
    p = _center(c)
    if p is None or not other.corners:
        return False
    x, y, w, h = cv2.boundingRect(np.array(other.corners, dtype=np.int32))
    pad = max(margin, 0.15 * max(w, h))      # 1D boxes are a thin scan line: give them some height
    return x - pad <= p[0] <= x + w + pad and y - pad <= p[1] <= y + h + pad


def confirmed(codes: list[Code], frames_seen: int) -> list[Code]:
    """The reads worth trusting from a multi-frame scan.

    - 2D codes: one read is enough (their error correction makes a wrong read
      practically impossible).
    - 1D codes: must be read in at least 2 different frames, since a single bad
      frame can misread (e.g. line item "006" as "0(6"). Skipped when only one
      frame was decoded (a still image).
    - A 1D read sitting where another 1D code with 3x the hits is, is a misread
      of that code: two barcodes can't occupy the same spot.
    """
    one_d = [c for c in codes if not c.is_2d]
    out = []
    for c in codes:
        if c.is_2d:
            out.append(c)
            continue
        if frames_seen >= 2 and len(c.frames) < 2:
            continue
        if any(o is not c and o.hits >= 3 * c.hits and _near(c, o) for o in one_d):
            continue
        out.append(c)
    return out
