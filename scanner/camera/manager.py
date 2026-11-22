"""Owns the camera while it's open: a background thread keeps grabbing frames,
so the preview stream, scanning and calibration all read the newest frame
without fighting over the device.

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

from scanner.camera.sources import Camera
from scanner.decode.draw import draw_codes
from scanner.decode.reader import Code, decode_frame

PREVIEW_WIDTH = 960       # the browser preview is downscaled; decoding uses full frames
PREVIEW_QUALITY = 70
OVERLAY_SECONDS = 2.0     # boxes stay on the preview this long after the last decode
DETECT_INTERVAL = 0.3     # live detector: pause between decodes (it's CPU-heavy on the Jetson)
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
        self.frame: np.ndarray | None = None
        self.frame_id = 0
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
            self.frame = None
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
            frame = self.camera.read()
            if frame is None:
                failures += 1
                if failures > 50:
                    self.error = "camera stopped sending frames"
                time.sleep(0.05)
                continue
            failures = 0
            with self.new_frame:
                self.frame = frame
                self.frame_id += 1
                self.new_frame.notify_all()

    def wait_frame(self, after_id: int = -1, timeout: float = 5.0) -> tuple[int, np.ndarray]:
        """The first frame newer than after_id (blocks until one arrives)."""
        deadline = time.time() + timeout
        with self.new_frame:
            while self.frame is None or self.frame_id <= after_id:
                left = deadline - time.time()
                if left <= 0 or not self._running:
                    raise RuntimeError(self.error or "no frame from the camera")
                self.new_frame.wait(left)
            return self.frame_id, self.frame

    def fresh_frame(self, skip: int = 1, timeout: float = 5.0) -> np.ndarray:
        """A frame captured after now (skips `skip` frames that may predate a settings change)."""
        fid = self.frame_id
        for _ in range(skip + 1):
            fid, frame = self.wait_frame(fid, timeout)
        return frame

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
            if not self.live_detect or self._busy or self.frame is None or self.frame_id == fid:
                time.sleep(0.05)
                continue
            fid, frame = self.frame_id, self.frame
            try:
                self.show_codes(decode_frame(frame))
            except Exception:             # a bad frame must not kill the detector
                pass
            time.sleep(DETECT_INTERVAL)

    def preview_jpeg(self) -> bytes | None:
        frame, fid = self.frame, self.frame_id
        if frame is None:
            return None
        codes = self._overlay if time.time() - self._overlay_time < OVERLAY_SECONDS else []
        key = (fid, self._overlay_version if codes else -1)
        if self._preview and self._preview[0] == key:
            return self._preview[1]
        h, w = frame.shape[:2]
        k = min(1.0, PREVIEW_WIDTH / w)
        if k < 1:
            frame = cv2.resize(frame, (PREVIEW_WIDTH, int(h * k)), interpolation=cv2.INTER_AREA)
        if codes:
            frame = draw_codes(frame, codes, scale=k)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, PREVIEW_QUALITY])
        if not ok:
            return None
        self._preview = (key, buf.tobytes())
        return self._preview[1]
