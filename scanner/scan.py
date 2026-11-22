"""One "Scan bag" press: watch the camera for a few seconds, decode every
frame, merge what was found, and keep the frame that showed the most codes.

1D barcodes only count once they're read in 2 different frames (see
reader.confirmed). Stops early once the label is complete twice in a row; if
it's still incomplete at the end, the best frame gets a slower, thorough decode.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from scanner.camera.manager import CameraManager
from scanner.decode.draw import draw_codes
from scanner.decode.parse import ScanResult, interpret
from scanner.decode.reader import Code, confirmed, decode_frame, merge

SCAN_SECONDS = 3.0
SNAPSHOT_WIDTH = 1280


@dataclass
class Scan:
    result: ScanResult
    snapshot: bytes | None      # JPEG of the best frame, for the left panel
    frames: int
    seconds: float


def _jpeg(frame: np.ndarray) -> bytes | None:
    h, w = frame.shape[:2]
    if w > SNAPSHOT_WIDTH:
        frame = cv2.resize(frame, (SNAPSHOT_WIDTH, int(h * SNAPSHOT_WIDTH / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else None


def scan(mgr: CameraManager, seconds: float = SCAN_SECONDS) -> Scan:
    with mgr.busy():
        return _scan(mgr, seconds)


def _scan(mgr: CameraManager, seconds: float) -> Scan:
    start = time.time()
    found: list[Code] = []
    best_frame, best_codes = None, []
    complete_streak, frames = 0, 0
    fid = mgr.frame_id
    while time.time() - start < seconds:
        fid, frame = mgr.wait_frame(fid)
        codes = decode_frame(frame, frame_id=fid)
        mgr.show_codes(codes)
        frames += 1
        found = merge(found + codes)
        if best_frame is None or len(codes) > len(best_codes):
            best_frame, best_codes = frame, codes
        complete = bool(codes) and not interpret(confirmed(found, frames)).missing
        complete_streak = complete_streak + 1 if complete else 0
        if complete_streak >= 2:
            break

    trusted = confirmed(found, frames)
    if best_frame is not None and interpret(trusted).missing:
        # incomplete at normal effort: try harder on the best frame before giving up.
        # A code that frame shows again (or only the thorough passes find) counts as
        # a second read; the overlap rule still drops misreads.
        rescue = decode_frame(best_frame, thorough=True)
        trusted = confirmed(merge(trusted + rescue), frames_seen=1)
        best_codes = merge(best_codes + rescue)
    keys = {(c.format, c.raw) for c in trusted}
    shown = [c for c in best_codes if (c.format, c.raw) in keys]
    snapshot = _jpeg(draw_codes(best_frame, shown)) if best_frame is not None else None
    return Scan(interpret(trusted), snapshot, frames, round(time.time() - start, 2))
