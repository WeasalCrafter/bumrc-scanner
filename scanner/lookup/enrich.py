"""Fill in catalog details (name, type, package, links...) for a scanned bag.

What's printed on the bag (distributor, quantity, lot, date code) always wins;
DigiKey fills in what the label can't know. The bag's MPN wins over DigiKey's
when both exist, so a lookup can never silently change the part number.
"""
from __future__ import annotations

import json
from typing import NamedTuple

from scanner.decode.parse import REQUIRED, part_number_candidates
from scanner.lookup.digikey import DigiKey, DigiKeyError, to_fields

# fields DigiKey describes better than the label (overwrite)
CATALOG = ["name", "description", "manufacturer", "type", "mount", "package", "value_num", "value_text",
           "tolerance", "voltage_rating", "power_rating", "dielectric", "digikey_url", "datasheet_url",
           "params_json"]


MAX_GUESSES = 6          # each miss costs 1-2 DigiKey requests


def lookup_keys(fields: dict) -> list[str]:
    keys = []
    if fields.get("distributor") == "DIGIKEY" and fields.get("distributor_pn"):
        keys.append(fields["distributor_pn"])
    if fields.get("mpn"):
        keys.append(fields["mpn"])
    return keys


class Enriched(NamedTuple):
    fields: dict
    problem: str | None = None                       # shown to the user; None = all good
    candidates: list[str] = []                       # makers to choose from when the MPN is ambiguous


def enrich(fields: dict, dk: DigiKey | None = None) -> Enriched:
    """Look the bag up on DigiKey and fill in catalog fields. Never raises for network/API trouble."""
    dk = dk or DigiKey()
    out = dict(fields)
    if not dk.configured:
        return Enriched(out, "DigiKey API keys not set; catalog details not filled in")
    keys = lookup_keys(fields)
    # then every other decoded value shaped like a part number (manufacturer labels
    # don't say which barcode is the MPN, and a layout guess can pick the wrong one)
    guesses = [g for g in part_number_candidates(_label_codes(fields)) if g not in keys][:MAX_GUESSES]
    if not keys and not guesses:
        return Enriched(out, "no part number to look up (type the MPN in, then press Look up on DigiKey)")
    product, makers, hit = None, [], None
    try:
        for k in keys + guesses:
            product, makers = dk.find(k, manufacturer=fields.get("manufacturer"))
            if product or makers:
                hit = k
                break
    except DigiKeyError as e:
        return Enriched(out, str(e))
    if makers:
        if hit not in keys:
            out["mpn"] = hit
        return Enriched(out, f"{hit} is made by {len(makers)} manufacturers on DigiKey; pick the right one", makers)
    if not product:
        tried = ", ".join(keys + guesses)
        return Enriched(out, f"not found on DigiKey (tried {tried})")

    found = to_fields(product)
    for k in CATALOG:
        if found.get(k) not in (None, ""):
            out[k] = found[k]
    if hit not in keys or not out.get("mpn"):
        out["mpn"] = found.get("mpn") or hit    # the barcode DigiKey recognised is the MPN
    return Enriched(out)


def _label_codes(fields: dict) -> list[str]:
    try:
        return json.loads(fields.get("label_json") or "{}").get("codes") or []
    except (ValueError, AttributeError):
        return []


def missing(fields: dict) -> list[str]:
    return [k for k in REQUIRED if fields.get(k) in (None, "")]
