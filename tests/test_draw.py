import cv2
import numpy as np

from scanner.config import REFERENCE_CAPTURES
from scanner.decode.draw import _box, draw_codes
from scanner.decode.reader import Code, decode_frame


def test_corners_are_in_original_pixels():
    img = cv2.imread(str(REFERENCE_CAPTURES / "newark1.png"))
    dm = next(c for c in decode_frame(img) if c.is_2d)
    xs = [x for x, _ in dm.corners]
    ys = [y for _, y in dm.corners]
    # the DataMatrix sits in the lower right of this photo (about x 960-1200, y 890-1130)
    assert 900 < min(xs) < 1000 and 1150 < max(xs) < 1250
    assert 850 < min(ys) < 950 and 1080 < max(ys) < 1180


def test_1d_scan_line_becomes_a_box():
    c = Code("Code128", b"12345", False, corners=[(100, 50), (300, 50), (300, 52), (100, 52)])
    pts = _box(c, 1.0)
    h = pts[:, 1].max() - pts[:, 1].min()
    assert h >= 45


def test_draw_does_not_modify_input():
    img = np.full((400, 400, 3), 255, np.uint8)
    c = Code("DataMatrix", b"x", False, corners=[(50, 50), (150, 50), (150, 150), (50, 150)])
    out = draw_codes(img, [c], scale=0.5)
    assert img.min() == 255 and out.min() < 255
