import json

import pytest

from scanner.config import ROOT
from scanner.labels import printer
from scanner.labels.store import save_label

SAMPLES = json.loads((ROOT / "tests" / "sample_items.json").read_text())


@pytest.mark.parametrize("s", SAMPLES, ids=lambda s: str(s["item_id"]))
def test_label_size_and_mode(s):
    img = printer.create_label(s["item"], s["item_id"])
    assert img.size == (printer.PAPER_WIDTH, printer.PAPER_HEIGHT)
    assert img.mode == "1"


def test_label_barcode_reads_back():
    zxingcpp = pytest.importorskip("zxingcpp")
    img = printer.create_label(SAMPLES[2]["item"], 142).convert("L")
    texts = [r.text for r in zxingcpp.read_barcodes(img)]
    assert "ID: 00000142" in texts


def test_label_needs_an_id():
    with pytest.raises(ValueError):
        printer.create_label({"name": "x"}, None)


def test_only_last_five_labels_kept(tmp_path):
    img = printer.create_label(SAMPLES[0]["item"], 1)
    for i in range(8):
        save_label(img, i, out_dir=tmp_path)
    left = sorted(p.name for p in tmp_path.glob("label_*.png"))
    assert len(left) == 5
    assert left[0].startswith("label_00000003")


def test_missing_printer_is_reported(tmp_path):
    env = {"LABEL_PRINTER": f"file://{tmp_path}/nope"}
    assert "not found" in printer.printer_problem(env)
    with pytest.raises(RuntimeError):
        printer.print_label(printer.create_label(SAMPLES[0]["item"], 1), env)
