"""One "Scan bag" press: watch the camera for a few seconds, decode every
frame, merge what was found, and keep the frame that showed the most codes.
Stops early once a label is fully decoded twice in a row."""
from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from scanner.camera.manager import CameraManager
from scanner.decode.draw import draw_codes
from scanner.decode.parse import ScanResult, interpret
from scanner.decode.reader import Code, decode_frame, merge

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
    best_frame, best_codes, best_count = None, [], -1
    complete_streak, frames = 0, 0
    fid = mgr.frame_id
    while time.time() - start < seconds:
        fid, frame = mgr.wait_frame(fid)
        codes = decode_frame(frame)
        mgr.show_codes(codes)
        frames += 1
        found = merge(found + codes)
        if len(codes) > best_count:
            best_frame, best_codes, best_count = frame, codes, len(codes)
        complete_streak = complete_streak + 1 if codes and not interpret(found).missing else 0
        if complete_streak >= 2:
            break
    if best_count <= 0 and best_frame is not None:
        # nothing at normal effort: try harder on the last frame before giving up
        best_codes = decode_frame(best_frame, thorough=True)
        found = merge(found + best_codes)
    snapshot = _jpeg(draw_codes(best_frame, best_codes)) if best_frame is not None else None
    return Scan(interpret(found), snapshot, frames, round(time.time() - start, 2))
