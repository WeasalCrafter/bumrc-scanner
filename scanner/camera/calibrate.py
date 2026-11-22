"""Find the focus (and sharpness) setting that decodes the most codes.

1. coarse sweep across the camera's whole focus range
2. fine sweep around the best coarse position
3. try a few sharpness values at the best focus
Each position is scored by how many distinct codes decode, with image
sharpness (variance of the Laplacian) as the tie-breaker. Point the camera at
a bag with several codes before starting.
"""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable

import cv2
import numpy as np

from scanner.camera.manager import CameraManager
from scanner.decode.reader import decode_frame

COARSE_STEPS = 11
FINE_STEPS = 6
SETTLE_SECONDS = 0.4     # lens motor needs a moment after a focus change


@dataclass
class Point:
    stage: str
    focus: int | None
    sharpness: int | None
    codes: int
    focus_measure: float

    @property
    def score(self) -> tuple[int, float]:
        return self.codes, self.focus_measure


@dataclass
class Result:
    best: dict[str, int] = field(default_factory=dict)
    points: list[Point] = field(default_factory=list)
    best_codes: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"best": self.best, "best_codes": self.best_codes, "notes": self.notes,
                "points": [asdict(p) for p in self.points]}


class Cancelled(Exception):
    pass


def focus_measure(img: np.ndarray) -> float:
    g = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    if w > 800:
        g = cv2.resize(g, (800, int(h * 800 / w)), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def _positions(lo: int, hi: int, n: int, step: int = 1) -> list[int]:
    vals = np.linspace(lo, hi, n)
    out = sorted({int(round(v / step) * step) for v in vals})
    return [min(max(v, lo), hi) for v in out]


def calibrate(mgr: CameraManager, progress: Callable[[float, str], None] = lambda f, m: None,
              cancel: threading.Event | None = None) -> Result:
    with mgr.busy():
        return _calibrate(mgr, progress, cancel or threading.Event())


def _calibrate(mgr: CameraManager, progress: Callable[[float, str], None], cancel: threading.Event) -> Result:
    ctrls = mgr.camera.controls()
    result = Result()
    current = {"focus": ctrls["focus"].value if "focus" in ctrls else None,
               "sharpness": ctrls["sharpness"].value if "sharpness" in ctrls else None}

    def measure(stage: str, **settings) -> Point:
        if cancel.is_set():
            raise Cancelled()
        for k, v in settings.items():
            mgr.camera.set(k, v)
            current[k] = v
        time.sleep(SETTLE_SECONDS)
        frame = mgr.fresh_frame(skip=1)
        codes = decode_frame(frame)
        mgr.show_codes(codes)
        p = Point(stage, current["focus"], current["sharpness"], len(codes), focus_measure(frame))
        result.points.append(p)
        return p

    plan_focus = "focus" in ctrls        # "inactive" only means autofocus is on; we turn it off
    sharp_vals = []
    if "sharpness" in ctrls:
        s = ctrls["sharpness"]
        sharp_vals = _positions(s.min, s.max, 5, s.step)
    total = (COARSE_STEPS + FINE_STEPS if plan_focus else 0) + len(sharp_vals) or 1
    done = 0

    def step(msg):
        nonlocal done
        done += 1
        progress(done / total, msg)

    if plan_focus:
        if "autofocus" in ctrls:
            mgr.camera.set("autofocus", 0)
            result.best["autofocus"] = 0
        f = ctrls["focus"]
        coarse = []
        for pos in _positions(f.min, f.max, COARSE_STEPS, f.step):
            coarse.append(measure("coarse", focus=pos))
            step(f"focus {pos}: {coarse[-1].codes} codes")
        best = max(coarse, key=lambda p: p.score)
        span = (f.max - f.min) / (COARSE_STEPS - 1)
        lo, hi = max(f.min, int(best.focus - span)), min(f.max, int(best.focus + span))
        fine = [best]
        for pos in _positions(lo, hi, FINE_STEPS, f.step):
            fine.append(measure("fine", focus=pos))
            step(f"focus {pos}: {fine[-1].codes} codes")
        best = max(fine, key=lambda p: p.score)
        result.best["focus"] = best.focus
        mgr.camera.set("focus", best.focus)
        current["focus"] = best.focus
    else:
        result.notes.append("this camera has no manual focus control; only sharpness was tuned")

    if sharp_vals:
        default = ctrls["sharpness"].default
        tries = []
        for v in sharp_vals:
            tries.append(measure("sharpness", sharpness=v))
            step(f"sharpness {v}: {tries[-1].codes} codes")
        best_s = max(tries, key=lambda p: (p.codes, -abs(p.sharpness - default)))
        result.best["sharpness"] = best_s.sharpness
        mgr.camera.set("sharpness", best_s.sharpness)

    result.best_codes = max((p.codes for p in result.points), default=0)
    if result.best_codes == 0:
        result.notes.append("no codes decoded at any setting; is a bag label in view and well lit?")
    progress(1.0, "done")
    return result
