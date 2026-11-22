"""Camera code against the file stand-in (no hardware)."""
import pytest

from scanner.camera.calibrate import calibrate
from scanner.camera.controls import parse_ctrls
from scanner.camera.manager import CameraManager, load_settings, save_settings
from scanner.camera.sources import FileCamera, make_camera
from scanner.config import REFERENCE_CAPTURES

V4L2_OUTPUT = """
User Controls
                     brightness 0x00980900 (int)    : min=-64 max=64 step=1 default=0 value=0 flags=0x00001000
                      sharpness 0x0098091b (int)    : min=0 max=100 step=1 default=50 value=50
Camera Controls
                 focus_absolute 0x009a090a (int)    : min=0 max=1023 step=1 default=0 value=300 flags=inactive
     focus_automatic_continuous 0x009a090c (bool)   : default=1 value=1
                  auto_exposure 0x009a0901 (menu)   : min=0 max=3 default=3 value=3 (Aperture Priority Mode)
"""


def test_parse_v4l2_controls():
    c = parse_ctrls(V4L2_OUTPUT)
    assert c["focus_absolute"].max == 1023 and c["focus_absolute"].value == 300 and c["focus_absolute"].inactive
    assert c["focus_automatic_continuous"].kind == "bool" and c["focus_automatic_continuous"].value == 1
    assert c["auto_exposure"].value == 3 and not c["sharpness"].inactive


def test_make_camera_file_source():
    cam = make_camera("file:references/captures")
    assert isinstance(cam, FileCamera) and cam.paths


@pytest.fixture
def mgr(tmp_path):
    cam = FileCamera(REFERENCE_CAPTURES)
    cam.FPS = 100
    cam.paths = [REFERENCE_CAPTURES / "newark1.png"]       # one bag whose 2D code reads
    cam._cycle = iter(cam.paths * 100)
    m = CameraManager(cam, tmp_path / "camera.json")
    m.open()
    yield m
    m.close()


def test_frames_and_preview(mgr):
    fid, frame = mgr.wait_frame()
    assert frame.ndim == 3
    jpg = mgr.preview_jpeg()
    assert jpg[:2] == b"\xff\xd8"


def test_settings_applied_on_open(tmp_path):
    save_settings(tmp_path / "camera.json", {"focus": 123, "sharpness": 70})
    cam = FileCamera(REFERENCE_CAPTURES)
    m = CameraManager(cam, tmp_path / "camera.json")
    m.open()
    try:
        assert cam.values["focus"] == 123 and cam.values["sharpness"] == 70
    finally:
        m.close()
    assert load_settings(tmp_path / "camera.json")["controls"]["focus"] == 123


def test_calibration_finds_simulated_focus(mgr, monkeypatch):
    monkeypatch.setattr("scanner.camera.calibrate.SETTLE_SECONDS", 0)
    seen = []
    r = calibrate(mgr, progress=lambda f, m: seen.append(f))
    # the file camera is sharpest at 420; anything that decodes the code nearby is fine
    assert abs(r.best["focus"] - FileCamera.BEST_FOCUS) <= 120
    assert r.best_codes >= 1 and "sharpness" in r.best
    assert seen[-1] == 1.0


def test_live_detector_boxes_codes(mgr):
    import time
    mgr.camera.set("focus", FileCamera.BEST_FOCUS)
    deadline = time.time() + 10
    while time.time() < deadline and not mgr._overlay:
        time.sleep(0.1)
    assert any(c.is_2d and c.corners for c in mgr._overlay)
    assert mgr.preview_jpeg()[:2] == b"\xff\xd8"


def test_detector_pauses_while_busy(mgr):
    import time
    with mgr.busy():
        time.sleep(1.0)              # a decode already in progress may still finish
        v = mgr._overlay_version
        time.sleep(1.0)
        assert mgr._overlay_version == v


class ResettingCamera(FileCamera):
    """Like the Arducam may do: controls set before streaming starts are lost
    when the first frame is read, and autofocus comes back on."""
    def __init__(self, folder):
        super().__init__(folder)
        self.streaming = False

    def open(self):
        super().open()
        self.streaming = False

    def read(self):
        if not self.streaming:
            self.streaming = True
            self.values.update(focus=0, autofocus=1, sharpness=50)
        return super().read()


def test_saved_settings_survive_stream_start_reset(tmp_path, monkeypatch):
    monkeypatch.setattr("scanner.camera.manager.SETTLE_SECONDS", 0)
    save_settings(tmp_path / "camera.json", {"autofocus": 0, "focus": 420, "sharpness": 80})
    cam = ResettingCamera(REFERENCE_CAPTURES)
    m = CameraManager(cam, tmp_path / "camera.json")
    m.open()
    try:
        assert cam.values == {"focus": 420, "autofocus": 0, "sharpness": 80}
        assert m.warnings == [] and m.settings_applied
    finally:
        m.close()


class StubbornCamera(FileCamera):
    """Accepts a focus value but silently keeps its own."""
    def set(self, name, value):
        if name != "focus":
            super().set(name, value)


def test_setting_that_wont_stick_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("scanner.camera.manager.SETTLE_SECONDS", 0)
    save_settings(tmp_path / "camera.json", {"focus": 420})
    m = CameraManager(StubbornCamera(REFERENCE_CAPTURES), tmp_path / "camera.json")
    m.open()
    try:
        assert m.warnings == ["camera kept focus at 0 instead of the saved 420"]
        assert not m.settings_applied
    finally:
        m.close()
