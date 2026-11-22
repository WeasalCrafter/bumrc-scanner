"""Where frames come from: the real USB camera, or image files for testing.

CAMERA_DEVICE in .env picks one:
    /dev/video0                     the Arducam (default)
    file:references/captures        test images instead of a camera (no hardware needed)

Both expose the same controls by our names (focus, sharpness, ...), so the
calibration code doesn't care which one it's driving.
"""
from __future__ import annotations

import itertools
import time
from pathlib import Path

import cv2
import numpy as np

from scanner.camera import controls as v4l2
from scanner.camera.controls import Control
from scanner.config import ROOT

FULL_SIZE = (3264, 2448)     # Arducam UC-A52, 8MP
JPEG_START = b"\xff\xd8"


def to_image(frame: np.ndarray | bytes | None, reduce: int = 1, gray: bool = False) -> np.ndarray | None:
    """Decode a grabbed frame. `reduce` = 2, 4 or 8 decodes JPEGs straight to that
    fraction of the size (much faster than decoding fully and shrinking)."""
    if frame is None:
        return None
    if isinstance(frame, bytes):
        flag = {(1, False): cv2.IMREAD_COLOR, (2, False): cv2.IMREAD_REDUCED_COLOR_2,
                (4, False): cv2.IMREAD_REDUCED_COLOR_4, (8, False): cv2.IMREAD_REDUCED_COLOR_8,
                (1, True): cv2.IMREAD_GRAYSCALE, (2, True): cv2.IMREAD_REDUCED_GRAYSCALE_2,
                (4, True): cv2.IMREAD_REDUCED_GRAYSCALE_4, (8, True): cv2.IMREAD_REDUCED_GRAYSCALE_8}[(reduce, gray)]
        return cv2.imdecode(np.frombuffer(frame, np.uint8), flag)
    img = frame
    if gray and img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if reduce > 1:
        h, w = img.shape[:2]
        img = cv2.resize(img, (-(-w // reduce), -(-h // reduce)), interpolation=cv2.INTER_AREA)
    return img


class Camera:
    """Interface shared by the real camera and the file stand-in."""
    description = ""

    def open(self) -> None: ...
    def close(self) -> None: ...
    def read(self) -> np.ndarray | None: ...

    def grab(self) -> np.ndarray | bytes | None:
        """The next frame as cheaply as possible: JPEG bytes if the camera sends
        them (decode later with `to_image`), else a decoded image."""
        return self.read()
    def controls(self) -> dict[str, Control]: return {}
    def set(self, name: str, value: int) -> None: ...

    @property
    def size(self) -> tuple[int, int] | None:
        return None


class V4L2Camera(Camera):
    def __init__(self, device: str = "/dev/video0", size: tuple[int, int] = FULL_SIZE):
        self.device = device
        self.want_size = size
        self.cap: cv2.VideoCapture | None = None
        self.description = f"USB camera {device}"

    def open(self) -> None:
        cap = cv2.VideoCapture(self.device, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError(f"can't open camera {self.device} (plugged in? used by another program?)")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))   # raw 8MP over USB2 would be ~2 fps
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.want_size[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.want_size[1])
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)                              # always hand out a fresh frame
        # Hand over the camera's JPEG untouched: decoding every 8MP frame at 15 fps
        # would eat most of the Jetson's CPU. Frames are decoded only when used.
        cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
        self.cap = cap

    def close(self) -> None:
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def grab(self) -> np.ndarray | bytes | None:
        if self.cap is None:
            return None
        ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        if frame.ndim == 1 or (frame.ndim == 2 and frame.shape[0] == 1):   # a 1-row byte buffer
            buf = frame.reshape(-1)
            if buf[:2].tobytes() == JPEG_START:
                return buf.tobytes()
            return None                  # raw bytes that aren't a JPEG: can't use them
        return frame                     # driver ignored CONVERT_RGB=0 and decoded it already

    def read(self) -> np.ndarray | None:
        return to_image(self.grab())

    @property
    def size(self):
        if self.cap is None:
            return None
        return int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def controls(self) -> dict[str, Control]:
        raw = v4l2.list_controls(self.device)
        out = {}
        for ours in v4l2.ALIASES:
            name = v4l2.resolve(raw, ours)
            if name:
                out[ours] = raw[name]
        return out

    def set(self, name: str, value: int) -> None:
        raw = v4l2.list_controls(self.device)
        if name == "focus":                       # manual focus only sticks with autofocus off
            af = v4l2.resolve(raw, "autofocus")
            if af and raw[af].value != 0:
                v4l2.set_control(self.device, af, 0)
        kernel = v4l2.resolve(raw, name)
        if not kernel:
            raise RuntimeError(f"this camera has no {name} control")
        v4l2.set_control(self.device, kernel, value)


class FileCamera(Camera):
    """Serves test images as if they came from a camera. Focus is simulated by
    blurring the image more the further `focus` is from BEST_FOCUS, so the
    calibration sweep can be exercised without hardware."""
    BEST_FOCUS = 420
    FPS = 10

    def __init__(self, folder: Path):
        self.folder = folder
        self.paths = sorted(p for p in folder.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        if not self.paths:
            raise RuntimeError(f"no test images in {folder}")
        self._cycle = itertools.cycle(self.paths)
        self.current: Path | None = None
        self.image: np.ndarray | None = None
        self.values = {"focus": 0, "sharpness": 50, "autofocus": 0}
        self._last = 0.0
        self.description = f"test images from {folder}"

    def next_image(self) -> str:
        self.current = next(self._cycle)
        self.image = cv2.imread(str(self.current))
        return self.current.name

    def open(self) -> None:
        if self.image is None:
            self.next_image()

    def close(self) -> None:
        pass

    def read(self) -> np.ndarray | None:
        if self.image is None:
            return None
        wait = 1 / self.FPS - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        sigma = abs(self.values["focus"] - self.BEST_FOCUS) / 60
        return cv2.GaussianBlur(self.image, (0, 0), sigma) if sigma > 0.3 else self.image.copy()

    @property
    def size(self):
        return None if self.image is None else (self.image.shape[1], self.image.shape[0])

    def controls(self) -> dict[str, Control]:
        return {
            "focus": Control("focus_absolute", "int", 0, 1000, 5, 0, self.values["focus"]),
            "autofocus": Control("focus_automatic_continuous", "bool", 0, 1, 1, 0, self.values["autofocus"]),
            "sharpness": Control("sharpness", "int", 0, 100, 1, 50, self.values["sharpness"]),
        }

    def set(self, name: str, value: int) -> None:
        if name not in self.values:
            raise RuntimeError(f"this camera has no {name} control")
        self.values[name] = int(value)


def make_camera(device: str | None) -> Camera:
    device = device or "/dev/video0"
    if device.startswith("file:"):
        folder = Path(device.removeprefix("file:"))
        return FileCamera(folder if folder.is_absolute() else ROOT / folder)
    return V4L2Camera(device)
