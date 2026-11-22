"""Find every barcode / DataMatrix in a camera frame.

One pass over the raw frame misses codes that are slightly blurry or low
contrast, so each frame is decoded several times with different clean-ups
(threshold, rescale, sharpen...). Results are merged and counted: a code seen
by many variants (or many frames) is more trustworthy than one seen once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator

import cv2
import numpy as np
import zxingcpp

F = zxingcpp.BarcodeFormat
FORMATS = [F.DataMatrix, F.QRCode, F.Code128, F.Code39, F.Code93, F.EAN13, F.UPCA, F.PDF417]
TWO_D = {"DataMatrix", "QRCode", "PDF417"}
MIN_1D_LENGTH = 3        # shorter 1D reads are almost always fragments of a bigger code


@dataclass
class Code:
    format: str             # "DataMatrix", "Code128", ...
    raw: bytes              # exact decoded bytes (keeps the GS/RS separators intact)
    vertical: bool          # printed sideways on the label
    hits: int = 1           # how many variants/frames read it
    found_by: list[str] = field(default_factory=list)
    corners: list[tuple[int, int]] | None = None   # where it is in the image (4 points, original pixels)

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
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(g)


def _sharpen(g):
    return cv2.addWeighted(g, 1.8, cv2.GaussianBlur(g, (0, 0), 3), -0.8, 0)


# cheap and most useful first; the thorough list is for single still images
FAST_VARIANTS: list[tuple[str, Callable]] = [
    ("gray", lambda g: g),
    ("otsu", _otsu),
    ("scale0.75", _scale(0.75)),
    ("scale0.5", _scale(0.5)),
]
THOROUGH_VARIANTS: list[tuple[str, Callable]] = FAST_VARIANTS + [
    ("clahe", _clahe),
    ("adaptive", _adaptive),
    ("sharpen", _sharpen),
    ("scale1.5", _scale(1.5)),
    ("median3", lambda g: cv2.medianBlur(g, 3)),
]


def _read(img: np.ndarray, binarizer) -> Iterator[zxingcpp.Barcode]:
    yield from zxingcpp.read_barcodes(img, formats=FORMATS, try_rotate=True, try_downscale=True,
                                      binarizer=binarizer)


def _keep(r: zxingcpp.Barcode) -> bool:
    if not r.valid:
        return False
    return r.format.name in TWO_D or len(r.bytes) >= MIN_1D_LENGTH


def merge(codes: Iterable[Code]) -> list[Code]:
    """Combine reads of the same code (same format and bytes), adding up hits."""
    out: dict[tuple[str, bytes], Code] = {}
    for c in codes:
        key = (c.format, c.raw)
        if key in out:
            out[key].hits += c.hits
            out[key].found_by += c.found_by
        else:
            out[key] = Code(c.format, c.raw, c.vertical, c.hits, list(c.found_by), c.corners)
    return sorted(out.values(), key=lambda c: (not c.is_2d, -c.hits))


def decode_frame(img: np.ndarray, thorough: bool = False) -> list[Code]:
    """All codes in one image (BGR or grayscale)."""
    g = _gray(img)
    found = []
    for name, fn in (THOROUGH_VARIANTS if thorough else FAST_VARIANTS):
        v = fn(g)
        k = g.shape[1] / v.shape[1]          # scaled variants: map positions back to the original
        for binarizer in (zxingcpp.Binarizer.LocalAverage, zxingcpp.Binarizer.GlobalHistogram):
            for r in _read(v, binarizer):
                if _keep(r):
                    vertical = abs(r.orientation) % 180 == 90
                    pos = r.position
                    corners = [(int(pt.x * k), int(pt.y * k))
                               for pt in (pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left)]
                    found.append(Code(r.format.name, r.bytes, vertical, 1, [f"{name}/{binarizer.name}"], corners))
    return merge(found)


def decode_frames(frames: Iterable[np.ndarray], thorough: bool = False) -> list[Code]:
    """All codes across several frames of the same scene (e.g. a 2 second scan)."""
    return merge(c for f in frames for c in decode_frame(f, thorough))
