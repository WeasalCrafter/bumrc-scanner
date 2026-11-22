"""Fill in catalog details (name, type, package, links...) for a scanned bag.

What's printed on the bag (distributor, quantity, lot, date code) always wins;
DigiKey fills in what the label can't know. The bag's MPN wins over DigiKey's
when both exist, so a lookup can never silently change the part number.
"""
from __future__ import annotations

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


def enrich(fields: dict, dk: DigiKey | None = None) -> tuple[dict, str | None]:
    """(updated fields, problem message or None). Never raises for network/API trouble."""
    dk = dk or DigiKey()
    out = dict(fields)
    if not dk.configured:
        return out, "DigiKey API keys not set; catalog details not filled in"
    keys = lookup_keys(fields)
    if not keys:
        return out, "no part number to look up"
    try:
        product = None
        for k in keys:
            product = dk.find(k, manufacturer=fields.get("manufacturer"))
            if product:
                break
    except DigiKeyError as e:
        return out, str(e)
    if not product:
        return out, f"{keys[-1]} not found on DigiKey (or several makers use that number)"

    found = to_fields(product)
    for k in CATALOG:
        if found.get(k) not in (None, ""):
            out[k] = found[k]
    if not out.get("mpn"):
        out["mpn"] = found.get("mpn")
    return out, None


def missing(fields: dict) -> list[str]:
    return [k for k in REQUIRED if fields.get(k) in (None, "")]
