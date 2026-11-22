"""Owns the camera while it's open: a background thread keeps grabbing frames,
so the preview stream, scanning and calibration all read the newest frame
without fighting over the device.

Frames are kept as the camera's JPEG bytes and decoded only when used: the
full 8MP image once per frame (shared by everyone who asks), and the preview
straight at 1/4 size.

Saved settings (camera.json, written by calibration) are applied on open.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from scanner.camera.sources import Camera, to_image
from scanner.decode.draw import draw_codes
from scanner.decode.reader import FAST_PASSES, Code, decode_frame

PREVIEW_WIDTH = 800       # the browser preview is downscaled (3264 / 4 = 816); decoding uses full frames
PREVIEW_QUALITY = 70
OVERLAY_SECONDS = 2.0     # boxes stay on the preview this long after the last decode
DETECT_INTERVAL = 0.5     # live detector: pause between decodes, so the Jetson's CPU stays free
# The live boxes are a guide, not the scan: 2 cheap passes (full-size read + a
# shrunk 2D-only pass) instead of the scan's full set.
DETECT_PASSES = [p for p in FAST_PASSES if (p.name, p.binarizer.name) in
                 {("gray", "LocalAverage"), ("scale0.3", "GlobalHistogram")}]
SETTLE_SECONDS = 0.6      # after applying saved settings: let the focus motor get there


def load_settings(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(path: Path, controls: dict[str, int]) -> dict:
    data = {"controls": {k: int(v) for k, v in controls.items()},
            "saved_at": datetime.now().isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data


class CameraManager:
    def __init__(self, camera: Camera, settings_file: Path):
        self.camera = camera
        self.settings_file = settings_file
        self.lock = threading.Lock()
        self.new_frame = threading.Condition()
        self._raw: np.ndarray | bytes | None = None    # newest frame as grabbed (usually JPEG bytes)
        self.frame_id = 0
        self._decoded: tuple[int, np.ndarray] | None = None
        self._decode_lock = threading.Lock()
        self.error: str | None = None
        self.warnings: list[str] = []
        self._thread: threading.Thread | None = None
        self._running = False
        self._preview: tuple[tuple, bytes] | None = None
        self._busy = 0                       # >0 while a scan/calibration decodes frames itself
        self._overlay: list[Code] = []
        self._overlay_time = 0.0
        self._overlay_version = 0
        self._detector: threading.Thread | None = None
        self.live_detect = True
        self.settings_applied = True

    # ------------------------------------------------------------ open / close

    @property
    def is_open(self) -> bool:
        return self._running

    def open(self) -> None:
        with self.lock:
            if self._running:
                return
            self.error = None
            self.camera.open()
            self._running = True
            self._thread = threading.Thread(target=self._grab_loop, name="camera", daemon=True)
            self._thread.start()
            # Saved settings go on only once frames are flowing: USB cameras may reset
            # controls (or turn autofocus back on) when the stream starts.
            try:
                self.wait_frame(timeout=8)
            except RuntimeError:
                self._running = False
                self._thread.join(timeout=3)
                self.camera.close()
                raise
            self.warnings = self.apply_saved()
            self._detector = threading.Thread(target=self._detect_loop, name="detect", daemon=True)
            self._detector.start()

    def close(self) -> None:
        with self.lock:
            self._running = False
            for t in (self._thread, self._detector):
                if t:
                    t.join(timeout=5)
            self._thread = self._detector = None
            self.camera.close()
            self._raw = self._decoded = None
            self._preview = None
            self.show_codes([])

    def apply(self, controls: dict[str, int]) -> list[str]:
        """Set controls; returns a message for each one the camera refused."""
        problems = []
        # auto modes first, so manual focus/exposure values aren't overridden by them
        for name in sorted(controls, key=lambda n: n not in ("autofocus", "auto_exposure")):
            try:
                self.camera.set(name, controls[name])
            except RuntimeError as e:
                problems.append(str(e))
        return problems

    def _not_applied(self, wanted: dict[str, int]) -> dict[str, tuple[int, int]]:
        """Controls whose value on the camera differs from `wanted`: name -> (wanted, actual)."""
        actual = {k: c.value for k, c in self.camera.controls().items()}
        return {k: (v, actual[k]) for k, v in wanted.items() if k in actual and actual[k] != v}

    def apply_saved(self) -> list[str]:
        """Apply camera.json, check the camera really took the values, retry once,
        and wait for the lens to finish moving. Returns warnings for the UI."""
        wanted = {k: int(v) for k, v in load_settings(self.settings_file).get("controls", {}).items()}
        if not wanted:
            return []
        problems = self.apply(wanted)
        time.sleep(SETTLE_SECONDS)
        off = self._not_applied(wanted)
        if off:                                   # some cameras need a second try once streaming
            problems = self.apply({k: wanted[k] for k in off})
            time.sleep(SETTLE_SECONDS)
            off = self._not_applied(wanted)
        problems += [f"camera kept {k} at {got} instead of the saved {want}" for k, (want, got) in off.items()]
        self.settings_applied = not off
        return problems

    # ------------------------------------------------------------ frames

    def _grab_loop(self) -> None:
        failures = 0
        while self._running:
            frame = self.camera.grab()
            if frame is None:
                failures += 1
                if failures > 50:
                    self.error = "camera stopped sending frames"
                time.sleep(0.05)
                continue
            failures = 0
            with self.new_frame:
                self._raw = frame
                self.frame_id += 1
                self.new_frame.notify_all()

    def _full(self, fid: int, raw) -> np.ndarray:
        """Full-size image of frame `fid`, decoded once however many callers want it."""
        with self._decode_lock:
            if self._decoded and self._decoded[0] == fid:
                return self._decoded[1]
            img = to_image(raw)
            if img is None:
                raise RuntimeError("the camera sent a frame that can't be decoded")
            self._decoded = (fid, img)
            return img

    @property
    def frame(self) -> np.ndarray | None:
        """Newest frame, full size (decoded on first use)."""
        raw, fid = self._raw, self.frame_id
        return None if raw is None else self._full(fid, raw)

    def _wait_raw(self, after_id: int, timeout: float):
        deadline = time.time() + timeout
        with self.new_frame:
            while self._raw is None or self.frame_id <= after_id:
                left = deadline - time.time()
                if left <= 0 or not self._running:
                    raise RuntimeError(self.error or "no frame from the camera")
                self.new_frame.wait(left)
            return self.frame_id, self._raw

    def wait_frame(self, after_id: int = -1, timeout: float = 5.0) -> tuple[int, np.ndarray]:
        """The first frame newer than after_id (blocks until one arrives)."""
        fid, raw = self._wait_raw(after_id, timeout)
        return fid, self._full(fid, raw)

    def fresh_frame(self, skip: int = 1, timeout: float = 5.0, reduce: int = 1,
                    gray: bool = False) -> np.ndarray:
        """A frame captured after now (skips `skip` frames that may predate a settings
        change). `reduce`/`gray` decode a smaller copy, e.g. for sharpness checks."""
        fid = self.frame_id
        for _ in range(skip + 1):
            fid, raw = self._wait_raw(fid, timeout)
        if reduce == 1 and not gray:
            return self._full(fid, raw)
        return to_image(raw, reduce, gray)

    # ------------------------------------------------------------ detected-code boxes

    @contextmanager
    def busy(self):
        """Pause the live detector while the caller decodes frames itself."""
        self._busy += 1
        try:
            yield
        finally:
            self._busy -= 1

    def show_codes(self, codes: list[Code]) -> None:
        """Codes to box on the preview (from the latest decoded frame)."""
        self._overlay = codes
        self._overlay_time = time.time()
        self._overlay_version += 1

    def _detect_loop(self) -> None:
        fid = -1
        while self._running:
            if not self.live_detect or self._busy or self._raw is None or self.frame_id == fid:
                time.sleep(0.05)
                continue
            try:
                fid, frame = self.frame_id, self.frame
                codes = decode_frame(frame, passes=DETECT_PASSES)
                if not self._busy:           # a scan started meanwhile: its boxes win
                    self.show_codes(codes)
            except Exception:             # a bad frame must not kill the detector
                pass
            time.sleep(DETECT_INTERVAL)

    def preview_jpeg(self) -> bytes | None:
        raw, fid = self._raw, self.frame_id
        if raw is None:
            return None
        codes = self._overlay if time.time() - self._overlay_time < OVERLAY_SECONDS else []
        key = (fid, self._overlay_version if codes else -1)
        if self._preview and self._preview[0] == key:
            return self._preview[1]
        w = self.camera.size[0] if isinstance(raw, bytes) and self.camera.size else raw.shape[1]
        reduce = max([r for r in (8, 4, 2) if w // r >= PREVIEW_WIDTH] or [1])
        frame = to_image(raw, reduce)
        if frame is None:
            return None
        k = 1 / reduce
        if frame.shape[1] > PREVIEW_WIDTH * 1.25:          # no exact JPEG reduction fits: shrink the rest
            h2, w2 = frame.shape[:2]
            k2 = PREVIEW_WIDTH / w2
            frame = cv2.resize(frame, (PREVIEW_WIDTH, int(h2 * k2)), interpolation=cv2.INTER_AREA)
            k *= k2
        if codes:
            frame = draw_codes(frame, codes, scale=k)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_QUALITY])
        if not ok:
            return None
        self._preview = (key, buf.tobytes())
        return self._preview[1]
