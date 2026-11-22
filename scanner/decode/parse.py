"""Turn decoded codes into inventory fields (MPN, quantity, distributor, ...).

Most distributors put an ECIA / ANSI MH10.8.2 DataMatrix on the bag:
    [)> RS 06 GS 1PC1608X7R2A105K080AC GS Q4 GS ... RS EOT
Each GS-separated field starts with a "data identifier" (digits + one letter)
that says what the rest of the field is. That is parsed here; 1D barcodes on
their own carry no identifiers, so they are only kept as raw values (and the
sideways DigiKey one as a candidate for DigiKey's barcode lookup).

Field names match the columns accepted by the web API (api/items.php).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from scanner.decode.reader import Code

RS, GS, EOT = "\x1e", "\x1d", "\x04"
ECIA_HEADER = "[)>" + RS + "06" + GS

# data identifier -> our field name (only the ones distributors actually use)
DI_FIELDS = {
    "P": "customer_pn",       # the part number/note the buyer typed when ordering
    "1P": "mpn",              # manufacturer part number
    "30P": "digikey_pn",      # DigiKey's own part number
    "3P": "distributor_pn",   # Newark puts its SKU here
    "K": "customer_po",
    "1K": "order_number",
    "10K": "invoice",
    "11K": "invoice",
    "14K": "line_item",
    "4K": "line_item",
    "Q": "qty",
    "9D": "date_code",
    "10D": "date_code",
    "1T": "lot",
    "4L": "country_of_origin",
    "1V": "manufacturer",
}

DI_RE = re.compile(r"^(\d{0,3}[A-Z])(.*)$", re.S)

DISTRIBUTORS = ["DIGIKEY", "MOUSER", "NEWARK", "VISHAY", "OTHER"]


def parse_ecia(text: str) -> dict[str, str] | None:
    """Data-identifier fields of an ECIA DataMatrix, e.g. {"1P": "EHR-3", "Q": "50"}.
    None if the text isn't in that format."""
    if not text.startswith(ECIA_HEADER):
        return None
    body = text[len(ECIA_HEADER):].split(RS)[0]
    out: dict[str, str] = {}
    for part in body.split(GS):
        m = DI_RE.match(part)
        if m and m.group(1) not in out:      # first one wins if a DI repeats
            out[m.group(1)] = m.group(2).strip()
    return out


def guess_distributor(di: dict[str, str], codes: list[Code]) -> str | None:
    """Best guess from which identifiers a label uses. None = ask the user."""
    if "30P" in di or any(k in di for k in ("11Z", "12Z", "13Z")):
        return "DIGIKEY"
    if "3P" in di:
        return "NEWARK"
    if "1V" in di and "14K" in di:
        return "MOUSER"
    # the "%0101..." shipment label is used by several manufacturers (Vishay, Panasonic, ...)
    # and doesn't say who shipped it, so the user picks the distributor
    return None


SHIPMENT_LABEL_PREFIX = "%0101"
YYWW_RE = re.compile(r"^\d{2}(0[1-9]|[1-4]\d|5[0-3])$")       # date code: year + week 01-53
MOUSER_PN_RE = re.compile(r"^(\d{2,4})-(\S+)$")                 # e.g. 71-CRCW04021M60FKED


def _center(c: Code) -> tuple[float, float] | None:
    if not c.corners:
        return None
    return (sum(x for x, _ in c.corners) / 4, sum(y for _, y in c.corners) / 4)


def _distance(a: Code, b: Code) -> float:
    ca, cb = _center(a), _center(b)
    if not ca or not cb:
        return 0.0
    return ((ca[0] - cb[0]) ** 2 + (ca[1] - cb[1]) ** 2) ** 0.5


def parse_shipment_label(codes: list[Code]) -> dict[str, str]:
    """Fields from the label whose 2D code is just a transaction number ("%0101RNOP0302961").
    Everything useful is in its unlabeled 1D barcodes, so each value is recognised by
    its shape: PO has spaces, country is 2 letters, date code is YYWW, the part
    number mixes letters and digits, and the quantity is the number printed right
    below the part number (the closest numeric barcode to it)."""
    dm = next((c.text for c in codes if c.is_2d and c.text.startswith(SHIPMENT_LABEL_PREFIX)), None)
    if dm is None:
        return {}
    out: dict[str, str] = {}
    numeric: list[Code] = []
    parts: list[Code] = []
    for c in sorted((c for c in codes if not c.is_2d), key=lambda c: -c.hits):
        t = c.text.strip()
        if t in dm:
            out.setdefault("transaction", t)
        elif " " in t:
            out.setdefault("customer_po", t)
        elif re.fullmatch(r"[A-Z]{2}", t):
            out.setdefault("country_of_origin", t)
        elif t.isdigit():
            numeric.append(c)
        elif re.search(r"[A-Z]", t, re.I) and re.search(r"\d", t) and len(t) >= 4:
            parts.append(c)
    mpn = max(parts, key=lambda c: (c.hits, len(c.text)), default=None)
    if mpn:
        out["mpn"] = mpn.text.strip()
        numeric.sort(key=lambda c: _distance(c, mpn))    # quantity row sits right under the PN
    dates = [c for c in numeric if YYWW_RE.match(c.text)]
    if mpn and len(numeric) >= 2:
        qty, rest = numeric[0], numeric[1:]
        date = next((c for c in rest if YYWW_RE.match(c.text)), None)
    else:
        date = dates[0] if dates else None
        qty = next((c for c in numeric if c is not date), None)
    if qty:
        out["qty"] = qty.text
    if date:
        out["date_code"] = date.text
    return out


def mouser_pn(codes: list[Code], mpn: str | None) -> tuple[str | None, str | None]:
    """(Mouser part number, MPN) from Mouser's 1D barcodes. Mouser PNs are a
    number, a dash, then usually the MPN (71-CRCW04021M60FKED), so a Mouser PN
    can also fill in the MPN when Mouser's 2D code leaves it out."""
    found = [(c.text, m.group(2)) for c in codes if not c.is_2d and (m := MOUSER_PN_RE.match(c.text))]
    if mpn:
        for text, tail in found:
            if tail.upper() == mpn.upper():
                return text, mpn
        return None, mpn
    if len(found) == 1:
        return found[0][0], found[0][1]
    return None, None


@dataclass
class ScanResult:
    fields: dict                      # API column -> value (None = not found)
    codes: list[Code]
    ecia: dict[str, str]              # raw data identifiers from the best DataMatrix
    digikey_barcodes: list[str] = field(default_factory=list)   # 1D codes worth a DigiKey lookup
    missing: list[str] = field(default_factory=list)            # important fields still empty

    def to_dict(self) -> dict:
        return {"fields": self.fields, "codes": [c.to_dict() for c in self.codes],
                "ecia": self.ecia, "digikey_barcodes": self.digikey_barcodes, "missing": self.missing}


REQUIRED = ["distributor", "mpn", "original_qty"]


def _int(s: str | None) -> int | None:
    try:
        return int(s.replace(",", "")) if s else None
    except ValueError:
        return None


def label_hash(codes: list[Code]) -> str | None:
    """Fingerprint of this exact bag label, so the server can refuse re-uploading the same bag.
    Two bags of the same part have different labels (lot/order/invoice), so they get different hashes."""
    two_d = [c for c in codes if c.is_2d]
    basis = sorted(c.text for c in (two_d or codes))
    if not basis:
        return None
    return hashlib.sha256("\n".join(basis).encode("latin-1")).hexdigest()


def interpret(codes: list[Code]) -> ScanResult:
    # the ECIA DataMatrix with the most fields is the main source of truth
    best: dict[str, str] = {}
    for c in codes:
        if c.is_2d:
            di = parse_ecia(c.text)
            if di and len(di) > len(best):
                best = di

    named = {}
    for di, name in DI_FIELDS.items():
        if best.get(di) and name not in named:
            named[name] = best[di]

    distributor = guess_distributor(best, codes)
    if not best:                                     # no ECIA code: try the shipment label format
        for k, v in parse_shipment_label(codes).items():
            named.setdefault(k, v)
    distributor_pn = named.get("digikey_pn") if distributor == "DIGIKEY" else named.get("distributor_pn")
    if distributor == "MOUSER" and not distributor_pn:
        distributor_pn, mpn_from_pn = mouser_pn(codes, named.get("mpn"))
        if mpn_from_pn:
            named.setdefault("mpn", mpn_from_pn)
    qty = _int(named.get("qty"))

    fields = {
        "distributor": distributor,
        "distributor_pn": distributor_pn,
        "mpn": named.get("mpn"),
        "manufacturer": named.get("manufacturer"),
        "name": named.get("customer_pn"),           # buyer's own note is a good starting name
        "description": None,
        "original_qty": qty,
        "qty_remaining": qty,
        "lot": named.get("lot"),
        "date_code": named.get("date_code"),
        "digikey_url": None,
        "datasheet_url": None,
        "label_hash": label_hash(codes),
        "label_json": json.dumps({
            "codes": [c.text for c in codes],
            "country_of_origin": named.get("country_of_origin"),
            "customer_po": named.get("customer_po"),
            "invoice": named.get("invoice"),
            "transaction": named.get("transaction"),
        }),
    }

    # DigiKey's sideways 1D barcode identifies the product; the horizontal one is just shipping
    dk = [c.text for c in codes if c.format == "Code128" and c.vertical and c.text.isdigit()]
    if distributor in (None, "DIGIKEY"):
        dk += [c.text for c in codes if c.format == "Code128" and not c.vertical and c.text.isdigit()
               and c.text not in dk]

    missing = [k for k in REQUIRED if fields.get(k) in (None, "")]
    return ScanResult(fields, codes, best, dk, missing)
