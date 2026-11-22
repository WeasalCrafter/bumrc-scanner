from scanner.decode.parse import interpret, label_hash, parse_ecia
from scanner.decode.reader import Code

RS, GS, EOT = "\x1e", "\x1d", "\x04"


def ecia(*fields: str) -> Code:
    text = "[)>" + RS + "06" + GS + GS.join(fields) + RS + EOT
    return Code("DataMatrix", text.encode("latin-1"), vertical=False)


def test_parse_ecia_fields():
    di = parse_ecia(ecia("1PEHR-3", "Q50", "1T").text)
    assert di == {"1P": "EHR-3", "Q": "50", "1T": ""}


def test_not_ecia():
    assert parse_ecia("%0101RNOP0302937") is None


def test_digikey_label():
    r = interpret([ecia("P497-6610-1-ND", "1P1N5819", "30P497-6610-1-ND", "K", "1K102489272",
                        "10K135192030", "9D2612", "1T4611401C00", "4LCN", "Q20", "11ZPICK", "12Z1866421")])
    f = r.fields
    assert (f["distributor"], f["distributor_pn"], f["mpn"], f["original_qty"]) == \
           ("DIGIKEY", "497-6610-1-ND", "1N5819", 20)
    assert f["qty_remaining"] == 20 and f["lot"] == "4611401C00" and f["date_code"] == "2612"
    assert r.missing == []


def test_unknown_distributor_is_reported_missing():
    r = interpret([ecia("1PABC123", "Q10")])
    assert r.fields["distributor"] is None
    assert r.missing == ["distributor"]


def test_vertical_code128_is_digikey_lookup_candidate():
    codes = [Code("Code128", b"444076656", vertical=True), Code("Code128", b"022447867", vertical=False)]
    r = interpret(codes)
    assert r.digikey_barcodes[0] == "444076656"


def test_label_hash_same_label_same_hash_different_lot_different_hash():
    a = ecia("1PX", "Q5", "1TLOT1")
    b = ecia("1PX", "Q5", "1TLOT2")
    assert label_hash([a]) == label_hash([ecia("1PX", "Q5", "1TLOT1")])
    assert label_hash([a]) != label_hash([b])


# ---------------------------------------------------------------- shipment label ("%0101...")

def code1d(text, x, y, hits=2):
    """A 1D code centered at (x, y), 200 px wide."""
    return Code("Code39", text.encode(), False, hits, corners=[(x - 100, y), (x + 100, y), (x + 100, y + 4), (x - 100, y + 4)])


def shipment(*codes):
    dm = Code("DataMatrix", b"%0101RNOP0302961", False, corners=[(0, 0), (50, 0), (50, 50), (0, 50)])
    return [dm, *codes]


def test_shipment_label_fields_by_shape():
    # layout from the real label: PO, PN, then QTY + COO, DATE CODE, TRANS going down
    r = interpret(shipment(code1d("ATTN MACK KEATING", 500, 100), code1d("ERJ-UP3F4701V", 500, 300),
                           code1d("47", 300, 420), code1d("JP", 700, 420), code1d("2018", 300, 560),
                           code1d("P0302961", 300, 700)))
    f = r.fields
    assert (f["mpn"], f["original_qty"], f["date_code"]) == ("ERJ-UP3F4701V", 47, "2018")
    assert '"country_of_origin": "JP"' in f["label_json"] and '"transaction": "P0302961"' in f["label_json"]
    # the label doesn't say who shipped it: the user picks the distributor
    assert f["distributor"] is None and r.missing == ["distributor"]


def test_shipment_label_qty_that_looks_like_a_date_code():
    # qty 1250 is also a valid YYWW; the one nearest the part number is the quantity
    r = interpret(shipment(code1d("RCS06034K99FKEA", 500, 300), code1d("2325", 300, 560), code1d("1250", 300, 420)))
    assert (r.fields["original_qty"], r.fields["date_code"]) == (1250, "2325")


def test_shipment_label_upside_down():
    # same label rotated 180 degrees: positions mirrored, answer unchanged
    r = interpret(shipment(code1d("RCS06034K99FKEA", 500, 700), code1d("2325", 700, 440), code1d("1250", 700, 580)))
    assert (r.fields["original_qty"], r.fields["date_code"]) == (1250, "2325")


def test_shipment_label_only_date_code_read():
    r = interpret(shipment(code1d("ILHB0805ER121V", 500, 300), code1d("2431", 300, 560)))
    assert r.fields["date_code"] == "2431" and r.fields["original_qty"] is None
    assert r.missing == ["distributor", "original_qty"]


# ---------------------------------------------------------------- Mouser part number

def mouser(*extra, mpn=True):
    fields = ["K31868955", "14K004"] + (["1PCRCW04021M60FKED"] if mpn else []) + ["Q100", "11K078440455", "4LIL", "1VVishay"]
    return [ecia(*fields), *extra]


def test_mouser_pn_matched_to_mpn():
    r = interpret(mouser(Code("Code128", b"71-CRCW04021M60FKED", False), Code("Code128", b"31868955", False)))
    assert r.fields["distributor_pn"] == "71-CRCW04021M60FKED" and r.fields["mpn"] == "CRCW04021M60FKED"


def test_mouser_pn_fills_missing_mpn():
    r = interpret(mouser(Code("Code128", b"710-744071039", False), mpn=False))
    assert (r.fields["distributor_pn"], r.fields["mpn"]) == ("710-744071039", "744071039")


def test_mouser_pn_not_matching_mpn_is_ignored():
    r = interpret(mouser(Code("Code128", b"12-SOMETHINGELSE", False)))
    assert r.fields["distributor_pn"] is None and r.fields["mpn"] == "CRCW04021M60FKED"
