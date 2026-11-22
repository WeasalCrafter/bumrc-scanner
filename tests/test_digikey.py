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
    out, problem, _ = enrich(bag, FakeDK())
    assert problem is None
    assert out["original_qty"] == 1000 and out["lot"] == "L1" and out["distributor"] == "MOUSER"
    assert out["name"] == "RES 1K OHM 1% 1/16W 0402" and out["type"] == "RESISTOR"
    assert out["manufacturer"] == "Vishay Dale"


def test_enrich_reports_not_found():
    out, problem, _ = enrich({"mpn": "NOPE-123"}, FakeDK())
    assert "not found" in problem and out == {"mpn": "NOPE-123"}


def test_enrich_without_keys():
    out, problem, _ = enrich({"mpn": "X"}, DigiKey(env={}))
    assert "not set" in problem


def test_unreachable_digikey_is_a_message_not_a_crash():
    def boom(request):
        raise httpx.ConnectError("no network")
    dk = DigiKey(env={"DIGIKEY_CLIENT_ID": "x", "DIGIKEY_CLIENT_SECRET": "y"},
                 client=httpx.Client(transport=httpx.MockTransport(boom)))
    out, problem, _ = enrich({"mpn": "X"}, dk)
    assert "can't reach DigiKey" in problem


@pytest.mark.network
def test_live_digikey_lookup():
    dk = DigiKey()
    if not dk.configured:
        pytest.skip("no DigiKey keys in .env")
    product, _ = dk.find("1N5819", manufacturer="STMicroelectronics")
    assert product["ManufacturerProductNumber"] == "1N5819"
    product, makers = dk.find("LM358DR")                       # TI and Rohm both make it
    assert product is None and "Texas Instruments" in makers
    product, _ = dk.find("LM358DR", manufacturer="TI")
    assert product["Manufacturer"]["Name"] == "Texas Instruments"


def summary(mpn, maker, dkpn):
    return {"ManufacturerProductNumber": mpn, "Manufacturer": {"Name": maker},
            "ProductVariations": [{"DigiKeyProductNumber": dkpn}]}


class AmbiguousDK(FakeDK):
    """LM358DR is made by TI and Rohm; productdetails refuses it, keyword search lists both."""
    def details(self, pn):
        return {"TI-PN": product("CRCW04021K00FKEDC")}.get(pn)    # any full record will do

    def exact_matches(self, mpn):
        return [summary("LM358DR", "Texas Instruments", "TI-PN"), summary("LM358DR", "Rohm Semiconductor", "ROHM-PN")]


def test_ambiguous_mpn_offers_manufacturer_choices():
    out, problem, choices = enrich({"mpn": "LM358DR"}, AmbiguousDK())
    assert choices == ["Rohm Semiconductor", "Texas Instruments"] and "pick" in problem


@pytest.mark.parametrize("label_maker", ["TI", "TEXAS INSTRUMENTS", "Texas Instruments Inc."])
def test_ambiguous_mpn_resolved_by_label_manufacturer(label_maker):
    out, problem, choices = enrich({"mpn": "LM358DR", "manufacturer": label_maker}, AmbiguousDK())
    assert problem is None and choices == [] and out["digikey_url"]


@pytest.mark.parametrize("a,b,same", [
    ("TI", "Texas Instruments", True), ("Vishay", "Vishay Dale", True), ("Wurth", "Würth Elektronik", True),
    ("SCIENTIFIC COMPONENTS CORPORATION DBA MINI-CIRCUITS (VA)", "Mini-Circuits", True),
    ("Panasonic", "Panasonic Industry", True), ("JST(JAPAN SOLDERLESS TERMINAL)", "JST Sales America Inc.", True),
    ("Analog Devices", "Analog Power Inc.", False), ("TI", "Rohm Semiconductor", False),
    ("Good-Ark Semiconductor", "GOODWORK", False)])
def test_same_manufacturer(a, b, same):
    from scanner.lookup.digikey import same_manufacturer
    assert same_manufacturer(a, b) is same
