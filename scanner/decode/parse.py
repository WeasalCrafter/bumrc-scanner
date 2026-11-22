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
    if any(c.is_2d and c.text.startswith("%0101") for c in codes):
        return "VISHAY"
    return None


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
    distributor_pn = named.get("digikey_pn") if distributor == "DIGIKEY" else named.get("distributor_pn")
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
        }),
    }

    # DigiKey's sideways 1D barcode identifies the product; the horizontal one is just shipping
    dk = [c.text for c in codes if c.format == "Code128" and c.vertical and c.text.isdigit()]
    if distributor in (None, "DIGIKEY"):
        dk += [c.text for c in codes if c.format == "Code128" and not c.vertical and c.text.isdigit()
               and c.text not in dk]

    missing = [k for k in REQUIRED if fields.get(k) in (None, "")]
    return ScanResult(fields, codes, best, dk, missing)
