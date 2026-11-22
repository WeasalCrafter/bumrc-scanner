"""DigiKey field mapping, using saved API responses (no network)."""
import json

import httpx
import pytest

from scanner.config import ROOT
from scanner.lookup.digikey import DigiKey, parse_value, to_fields
from scanner.lookup.enrich import enrich

FIX = ROOT / "tests" / "fixtures" / "digikey"


def product(name):
    return json.loads((FIX / f"{name}.json").read_text())["Product"]


@pytest.mark.parametrize("text,num,label", [
    ("4.99 kOhms", 4990.0, "4.99kΩ"), ("1 µF", 1e-6, "1µF"), ("3.9 µH", 3.9e-6, "3.9µH"),
    ("100 Ohms", 100.0, "100Ω"), ("-", None, None), (None, None, None)])
def test_parse_value(text, num, label):
    n, t = parse_value(text)
    assert t == label
    assert n == pytest.approx(num) if num else n is None


@pytest.mark.parametrize("name,expect", [
    ("C1608X7R2A105K080AC", {"type": "CAPACITOR", "mount": "SMD", "package": "0603", "value_text": "1µF",
                             "voltage_rating": "100V", "dielectric": "X7R", "tolerance": "±10%"}),
    ("CRCW04021K00FKEDC", {"type": "RESISTOR", "mount": "SMD", "package": "0402", "value_num": 1000.0,
                           "power_rating": "0.063W", "manufacturer": "Vishay Dale"}),
    ("CF14JT1K00CT-ND", {"type": "RESISTOR", "mount": "THT", "package": "Axial", "power_rating": "0.25W"}),
    ("497-6610-1-ND", {"type": "DIODE", "mount": "THT", "package": "DO-41", "voltage_rating": "40V"}),
    ("744071039", {"type": "INDUCTOR", "mount": "SMD", "value_text": "3.9µH"}),
    ("ILHB0805ER121V", {"type": "INDUCTOR", "mount": "SMD", "package": "0805"}),
    ("EHR-3", {"type": "CONNECTOR", "mount": None, "mpn": "EHR-3"}),
])
def test_to_fields(name, expect):
    f = to_fields(product(name))
    assert {k: f[k] for k in expect} == expect
    assert f["digikey_url"].startswith("https://www.digikey.com/")


class FakeDK(DigiKey):
    """DigiKey client that answers from the fixtures instead of the network."""
    def __init__(self):
        super().__init__(env={"DIGIKEY_CLIENT_ID": "x", "DIGIKEY_CLIENT_SECRET": "y"})

    def details(self, pn):
        f = FIX / f"{pn}.json"
        return json.loads(f.read_text())["Product"] if f.exists() else None

    def exact_matches(self, mpn):
        return []


def test_enrich_keeps_bag_data_and_fills_catalog():
    bag = {"distributor": "MOUSER", "mpn": "CRCW04021K00FKEDC", "manufacturer": "Vishay", "original_qty": 1000,
           "qty_remaining": 1000, "lot": "L1", "name": None}
    out, problem = enrich(bag, FakeDK())
    assert problem is None
    assert out["original_qty"] == 1000 and out["lot"] == "L1" and out["distributor"] == "MOUSER"
    assert out["name"] == "RES 1K OHM 1% 1/16W 0402" and out["type"] == "RESISTOR"
    assert out["manufacturer"] == "Vishay Dale"


def test_enrich_reports_not_found():
    out, problem = enrich({"mpn": "NOPE-123"}, FakeDK())
    assert "not found" in problem and out == {"mpn": "NOPE-123"}


def test_enrich_without_keys():
    out, problem = enrich({"mpn": "X"}, DigiKey(env={}))
    assert "not set" in problem


def test_unreachable_digikey_is_a_message_not_a_crash():
    def boom(request):
        raise httpx.ConnectError("no network")
    dk = DigiKey(env={"DIGIKEY_CLIENT_ID": "x", "DIGIKEY_CLIENT_SECRET": "y"},
                 client=httpx.Client(transport=httpx.MockTransport(boom)))
    out, problem = enrich({"mpn": "X"}, dk)
    assert "can't reach DigiKey" in problem


@pytest.mark.network
def test_live_digikey_lookup():
    dk = DigiKey()
    if not dk.configured:
        pytest.skip("no DigiKey keys in .env")
    assert dk.find("1N5819", manufacturer="STMicroelectronics")["ManufacturerProductNumber"] == "1N5819"
