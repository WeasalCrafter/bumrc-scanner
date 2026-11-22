"""Decoder accuracy: tilted labels, which reads to trust, shrunk passes."""
import cv2
import numpy as np
import pytest

from scanner.config import REFERENCE_CAPTURES
from scanner.decode.reader import Code, _rotation, confirmed, decode_frame, estimate_skew

TILTED = ["mouser-4.jpg", "panasonic-1.jpg", "vishay-3.jpg"]     # full-res Jetson captures, many long 1D codes


def tilt(img: np.ndarray, degrees: float) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return _rotation(g, degrees)[0]          # grown canvas: nothing near the edges is cut off


@pytest.fixture(scope="module")
def straight():
    return {name: cv2.imread(str(REFERENCE_CAPTURES / name)) for name in TILTED}


@pytest.mark.parametrize("name", TILTED)
@pytest.mark.parametrize("degrees", [-5, 3, 8])
def test_tilted_label_reads_like_straight(straight, name, degrees):
    """A few degrees of tilt used to lose the long 1D codes (the part number first)."""
    want = {(c.format, c.raw) for c in decode_frame(straight[name])}
    got = {(c.format, c.raw) for c in decode_frame(tilt(straight[name], degrees))}
    assert want <= got, f"lost {sorted(c[1] for c in want - got)}"


def test_skew_estimate(straight):
    g = cv2.cvtColor(straight["panasonic-1.jpg"], cv2.COLOR_BGR2GRAY)
    base = estimate_skew(g)
    for d in (-6, 4):
        assert abs(estimate_skew(_rotation(g, d)[0]) - (base - d)) < 1.5


def test_boxes_map_back_after_straightening(straight):
    """Boxes from the straightened image must land on the code in the original frame."""
    img = tilt(straight["mouser-4.jpg"], 5)
    raw = {c.raw: c for c in decode_frame(img)}
    dm = next(c for c in raw.values() if c.is_2d)
    x, y, w, h = cv2.boundingRect(np.array(dm.corners))
    crop = img[max(0, y - 20):y + h + 20, max(0, x - 20):x + w + 20]
    assert any(c.raw == dm.raw for c in decode_frame(crop))


def test_shrunk_passes_only_report_2d(straight):
    """At 0.5x and below, narrow 1D bars misread: those passes look for 2D codes only."""
    for img in straight.values():
        for c in decode_frame(img, thorough=True):
            if not c.is_2d:
                assert not all(by.split("/")[-2].startswith(("scale0.5", "scale0.3", "scale0.25"))
                               for by in c.found_by), c.text


# ---------------------------------------------------------------- confirmed()

def code(text, fmt="Code128", frames=(1, 2), hits=10, at=(500, 500)):
    x, y = at
    return Code(fmt, text.encode(), False, hits, corners=[(x - 100, y), (x + 100, y), (x + 100, y + 4), (x - 100, y + 4)],
                frames=set(frames))


def texts(codes):
    return sorted(c.text for c in codes)


def test_1d_needs_two_frames():
    codes = [code("ONCE", frames={3}, at=(500, 100)), code("TWICE", at=(500, 300))]
    assert texts(confirmed(codes, frames_seen=4)) == ["TWICE"]


def test_2d_trusted_after_one_read():
    dm = code("[)>...", fmt="DataMatrix", frames={3}, hits=1)
    assert confirmed([dm], frames_seen=4) == [dm]


def test_single_image_skips_frame_rule():
    assert texts(confirmed([code("ONCE", frames={0})], frames_seen=1)) == ["ONCE"]


def test_misread_overshadowed_by_stronger_read_in_same_spot():
    # seen live on a Mouser bag: line item "006" also read as "0(6" in the same place
    good, bad = code("006", hits=35), code("0(6", hits=11, at=(505, 502))
    elsewhere = code("100", hits=11, at=(500, 900))
    assert texts(confirmed([good, bad, elsewhere], frames_seen=5)) == ["006", "100"]


def test_similar_hit_counts_both_kept():
    # two real codes printed close together: neither has 3x the hits
    a, b = code("006", hits=20), code("31868955", hits=12, at=(520, 505))
    assert texts(confirmed([a, b], frames_seen=5)) == ["006", "31868955"]
