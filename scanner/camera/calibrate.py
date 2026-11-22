"""Find the focus (and sharpness) setting that decodes the most codes.

The Arducam's in-focus band is only about 30 focus units wide, so the sweep
has to be fine enough not to step over it:

1. park the lens at the near end, then sweep the whole range in steps of 20,
   scoring each position by image sharpness only (cheap: a half-size grayscale
   decode of the centre of the frame, so the bag edges and background don't count)
2. sweep +-20 around the sharpest position in steps of 2, again by sharpness
3. decode barcodes at the few sharpest positions and keep the one that reads
   the most confirmed codes (2 frames each, so a misread can't win)
4. try a few sharpness values at that focus

Point the camera at a bag with several codes before starting.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import cv2
import numpy as np

from scanner.camera.manager import CameraManager
from scanner.decode.reader import confirmed, decode_frame, merge

COARSE_STEP = 20
FINE_RANGE = 20          # fine sweep covers best coarse position +- this
FINE_STEP = 2
DECODE_CANDIDATES = 3    # sharpest fine positions that get a full barcode check
SHARPNESS_VALUES = 5
PARK_SECONDS = 1.5       # lens travel from wherever it was to the start of the sweep
SETTLE_SECONDS = 0.25    # after a small focus step
SKIP_FRAMES = 2          # frames that may have been exposed while the lens moved
BAND_FRACTION = 0.6      # "in focus" = at least this much of the peak sharpness
ROI = 0.8                # sharpness is measured on the centre 80% of the frame


@dataclass
class Point:
    stage: str
    focus: int | None
    sharpness: int | None
    codes: int | None        # None = not decoded (sharpness-only positions)
    focus_measure: float


@dataclass
class Result:
    best: dict[str, int] = field(default_factory=dict)
    points: list[Point] = field(default_factory=list)
    best_codes: int = 0
    focus_band: tuple[int, int] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"best": self.best, "best_codes": self.best_codes, "notes": self.notes,
                "focus_band": self.focus_band, "points": [asdict(p) for p in self.points]}


class Cancelled(Exception):
    pass


def focus_measure(img: np.ndarray) -> float:
    """Sharpness of the centre of the image (variance of the Laplacian at ~1600 px wide)."""
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    mh, mw = int(h * (1 - ROI) / 2), int(w * (1 - ROI) / 2)
    g = g[mh:h - mh, mw:w - mw]
    if g.shape[1] > 1600:
        g = cv2.resize(g, (1600, int(g.shape[0] * 1600 / g.shape[1])), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def _steps(lo: int, hi: int, step: int, unit: int = 1) -> list[int]:
    """lo..hi every `step`, rounded to the control's own step, always including hi."""
    step = max(step, unit)
    vals = list(range(lo, hi + 1, step))
    if vals[-1] != hi:
        vals.append(hi)
    return sorted({min(max(lo + round((v - lo) / unit) * unit, lo), hi) for v in vals})


def _spread(lo: int, hi: int, n: int, unit: int = 1) -> list[int]:
    return sorted({min(max(int(round(v / unit) * unit), lo), hi) for v in np.linspace(lo, hi, n)})


def calibrate(mgr: CameraManager, progress: Callable[[float, str], None] = lambda f, m: None,
              cancel: threading.Event | None = None) -> Result:
    with mgr.busy():
        return _calibrate(mgr, progress, cancel or threading.Event())


def _calibrate(mgr: CameraManager, progress: Callable[[float, str], None], cancel: threading.Event) -> Result:
    ctrls = mgr.camera.controls()
    result = Result()
    current = {k: ctrls[k].value if k in ctrls else None for k in ("focus", "sharpness")}

    def check_cancel():
        if cancel.is_set():
            raise Cancelled()

    def move(settle: float = SETTLE_SECONDS, **settings):
        check_cancel()
        for k, v in settings.items():
            mgr.camera.set(k, v)
            current[k] = v
        time.sleep(settle)

    def sharpness_at(stage: str, focus: int) -> Point:
        move(focus=focus)
        p = Point(stage, focus, current["sharpness"], None, focus_measure(mgr.fresh_frame(SKIP_FRAMES, reduce=2, gray=True)))
        result.points.append(p)
        return p

    def codes_at(stage: str, **settings) -> Point:
        """Decode 2 frames; count codes read in both (2D codes count after one)."""
        move(**settings)
        found = []
        frame = None
        for i in range(2):
            frame = mgr.fresh_frame(SKIP_FRAMES if i == 0 else 0)
            codes = decode_frame(frame, frame_id=i)
            mgr.show_codes(codes)
            found = merge(found + codes)
        p = Point(stage, current["focus"], current["sharpness"], len(confirmed(found, 2)), focus_measure(frame))
        result.points.append(p)
        return p

    has_focus = "focus" in ctrls        # "inactive" only means autofocus is on; we turn it off
    sharp_vals = []
    if "sharpness" in ctrls:
        s = ctrls["sharpness"]
        sharp_vals = _spread(s.min, s.max, SHARPNESS_VALUES, s.step)

    coarse_pos: list[int] = []
    if has_focus:
        f = ctrls["focus"]
        coarse_pos = _steps(f.min, f.max, COARSE_STEP, f.step)
    fine_count = (2 * FINE_RANGE) // FINE_STEP + 1
    total = (len(coarse_pos) + fine_count + DECODE_CANDIDATES if has_focus else 0) + len(sharp_vals) or 1
    done = 0

    def step(msg):
        nonlocal done
        done = min(done + 1, total - 1)
        progress(done / total, msg)

    if has_focus:
        f = ctrls["focus"]
        if "autofocus" in ctrls:
            mgr.camera.set("autofocus", 0)
            result.best["autofocus"] = 0
        move(PARK_SECONDS, focus=coarse_pos[0])          # start from a lens that has stopped moving

        coarse = []
        for pos in coarse_pos:
            coarse.append(sharpness_at("coarse", pos))
            step(f"focus {pos}: sharpness {coarse[-1].focus_measure:.0f}")
        peak = max(coarse, key=lambda p: p.focus_measure).focus

        fine = []
        for pos in _steps(max(f.min, peak - FINE_RANGE), min(f.max, peak + FINE_RANGE), FINE_STEP, f.step):
            fine.append(sharpness_at("fine", pos))
            step(f"focus {pos}: sharpness {fine[-1].focus_measure:.0f}")

        sharp = coarse + fine
        top = max(p.focus_measure for p in sharp)
        band = sorted(p.focus for p in sharp if p.focus_measure >= BAND_FRACTION * top)
        result.focus_band = (band[0], band[-1])

        candidates = sorted(fine or coarse, key=lambda p: -p.focus_measure)[:DECODE_CANDIDATES]
        checked = []
        for c in candidates:
            checked.append(codes_at("decode", focus=c.focus))
            step(f"focus {c.focus}: {checked[-1].codes} codes")
        best = max(checked, key=lambda p: (p.codes, p.focus_measure))
        result.best["focus"] = best.focus
        move(focus=best.focus)
        result.notes.append(f"in focus from {band[0]} to {band[-1]}; using {best.focus}")
    else:
        result.notes.append("this camera has no manual focus control; only sharpness was tuned")

    if sharp_vals:
        default = ctrls["sharpness"].default
        tries = []
        for v in sharp_vals:
            tries.append(codes_at("sharpness", sharpness=v))
            step(f"sharpness {v}: {tries[-1].codes} codes")
        best_s = max(tries, key=lambda p: (p.codes, -abs(p.sharpness - default)))
        result.best["sharpness"] = best_s.sharpness
        mgr.camera.set("sharpness", best_s.sharpness)

    result.best_codes = max((p.codes for p in result.points if p.codes is not None), default=0)
    if result.best_codes == 0:
        result.notes.append("no codes decoded at any setting; is a bag label in view and well lit?")
    progress(1.0, "done")
    return result
