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
