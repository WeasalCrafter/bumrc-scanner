"""Draw boxes around decoded codes, so it's obvious which codes on a label
were read and which were missed (no box = not decoded)."""
from __future__ import annotations

import cv2
import numpy as np

from scanner.decode.reader import Code

GREEN = (60, 160, 40)     # BGR
BLUE = (200, 110, 30)


def _box(c: Code, scale: float) -> np.ndarray:
    pts = np.array([(x * scale, y * scale) for x, y in c.corners], dtype=np.float32)
    if c.is_2d:
        return pts.astype(np.int32)
    # 1D codes are reported as the scan line through them; widen it into a box
    (cx, cy), (w, h), angle = cv2.minAreaRect(pts)
    long_side = max(w, h)
    if min(w, h) < 0.25 * long_side:
        w, h = (w, 0.25 * long_side) if w >= h else (0.25 * long_side, h)
    return cv2.boxPoints(((cx, cy), (w, h), angle)).astype(np.int32)


def draw_codes(img: np.ndarray, codes: list[Code], scale: float = 1.0) -> np.ndarray:
    """Copy of img with a box and short tag on each code. `scale` maps the
    codes' full-resolution corners onto a resized img (e.g. 0.3 for a preview)."""
    out = img.copy()
    thick = max(2, int(round(min(out.shape[:2]) / 300)))
    font_scale = max(0.5, min(out.shape[:2]) / 900)
    for c in codes:
        if not c.corners:
            continue
        pts = _box(c, scale)
        color = GREEN if c.is_2d else BLUE
        cv2.polylines(out, [pts], isClosed=True, color=color, thickness=thick)
        tag = ("2D " if c.is_2d else "") + (c.text if len(c.text) <= 18 else c.text[:16] + "..")
        tag = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in tag)
        x, y = int(pts[:, 0].min()), int(pts[:, 1].min())
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
        y = max(y, th + 6)
        cv2.rectangle(out, (x, y - th - 6), (x + tw + 6, y), color, -1)
        cv2.putText(out, tag, (x + 3, y - 4), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 1, cv2.LINE_AA)
    return out
