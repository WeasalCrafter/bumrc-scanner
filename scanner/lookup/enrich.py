"""Fill in catalog details (name, type, package, links...) for a scanned bag.

What's printed on the bag (distributor, quantity, lot, date code) always wins;
DigiKey fills in what the label can't know. The bag's MPN wins over DigiKey's
when both exist, so a lookup can never silently change the part number.
"""
from __future__ import annotations

from typing import NamedTuple

from scanner.decode.parse import REQUIRED
from scanner.lookup.digikey import DigiKey, DigiKeyError, to_fields

# fields DigiKey describes better than the label (overwrite)
CATALOG = ["name", "description", "manufacturer", "type", "mount", "package", "value_num", "value_text",
           "tolerance", "voltage_rating", "power_rating", "dielectric", "digikey_url", "datasheet_url",
           "params_json"]


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
    if not keys:
        return Enriched(out, "no part number to look up")
    product, candidates = None, []
    try:
        for k in keys:
            product, candidates = dk.find(k, manufacturer=fields.get("manufacturer"))
            if product or candidates:
                break
    except DigiKeyError as e:
        return Enriched(out, str(e))
    if candidates:
        return Enriched(out, f"{keys[-1]} is made by {len(candidates)} manufacturers on DigiKey; pick the right one",
                        candidates)
    if not product:
        return Enriched(out, f"{keys[-1]} not found on DigiKey")

    found = to_fields(product)
    for k in CATALOG:
        if found.get(k) not in (None, ""):
            out[k] = found[k]
    if not out.get("mpn"):
        out["mpn"] = found.get("mpn")
    return Enriched(out)


def missing(fields: dict) -> list[str]:
    return [k for k in REQUIRED if fields.get(k) in (None, "")]
