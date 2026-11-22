"""Save camera frames as decoder test cases (calibration page, "Save capture").

Each capture is an image plus a .json sidecar in the format tests/test_captures.py
reads. Fields the user confirmed that the decoder already gets right go in
"expected" (must keep passing); corrections the decoder didn't get go in
"expected_todo" (tracked, but not a failing test until the decoder improves).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from scanner import config

CHECK_FIELDS = ["distributor", "distributor_pn", "mpn", "manufacturer", "original_qty", "lot", "date_code"]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:30] or "capture"


def save_capture(frame: np.ndarray, description: str, distributor: str | None, decoded: dict,
                 folder: Path | None = None) -> str:
    folder = folder or config.CAPTURES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(distributor or '')}-{_slug(description)}"
    # high-quality JPEG: camera frames arrive as MJPEG anyway, and it's ~8x smaller than PNG
    cv2.imwrite(str(folder / f"{name}.jpg"), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    sidecar = {"description": description, "distributor": distributor,
               "saved_at": datetime.now().isoformat(timespec="seconds"),
               "decoded": {k: decoded.get(k) for k in CHECK_FIELDS}, "expected": {}}
    (folder / f"{name}.json").write_text(json.dumps(sidecar, indent=2) + "\n")
    return name


def confirm_capture(name: str, confirmed: dict, folder: Path | None = None) -> dict:
    """Store the user's checked values as the capture's expected results."""
    folder = folder or config.CAPTURES_DIR
    path = folder / f"{Path(name).name}.json"       # Path().name: no escaping the folder
    spec = json.loads(path.read_text())
    decoded = spec.get("decoded", {})
    expected, todo = {}, {}
    for k in CHECK_FIELDS:
        v = confirmed.get(k)
        if v in (None, ""):
            continue
        if k == "original_qty":
            v = int(v)
        (expected if decoded.get(k) == v else todo)[k] = v
    spec["expected"] = expected
    if todo:
        spec["expected_todo"] = todo
        spec["todo_reason"] = "decoder missed or misread these when the capture was saved"
    else:
        spec.pop("expected_todo", None)
        spec.pop("todo_reason", None)
    path.write_text(json.dumps(spec, indent=2) + "\n")
    return spec
