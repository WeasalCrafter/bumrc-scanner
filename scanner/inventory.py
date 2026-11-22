"""Client for the inventory web API (api/items.php on the web server).

    INVENTORY_API_URL   e.g. https://example.org/inventory/api/items.php
    INVENTORY_API_KEY   must match api_key in the server's config.php

upload() returns the new item's ID or raises UploadError with a message that
can be shown to the user as-is. Nothing is queued: a failed upload is an error.
"""
from __future__ import annotations

import httpx

from scanner.config import load_env
from scanner.lookup.digikey import parse_value

TIMEOUT = 15

# columns the API stores (keep in sync with $COLUMNS in api/items.php)
COLUMNS = ["distributor", "distributor_pn", "mpn", "manufacturer", "type", "mount", "package",
           "value_num", "value_text", "tolerance", "voltage_rating", "power_rating", "dielectric",
           "name", "description", "location", "lot", "date_code", "original_qty", "qty_remaining",
           "digikey_url", "datasheet_url", "label_hash", "label_json", "params_json", "dk_raw_json"]


class UploadError(RuntimeError):
    def __init__(self, message: str, existing_id: int | None = None):
        super().__init__(message)
        self.existing_id = existing_id


def payload(fields: dict, member: str) -> dict:
    body = {c: fields.get(c) for c in COLUMNS}
    # value_num follows whatever value text the user ended up with
    if body.get("value_text"):
        num, _ = parse_value(body["value_text"])
        body["value_num"] = num
    for k in ("original_qty", "qty_remaining"):
        if body.get(k) not in (None, ""):
            body[k] = int(body[k])
    if body.get("qty_remaining") is None:
        body["qty_remaining"] = body.get("original_qty")
    body["member"] = member
    return body


def check(fields: dict, member: str) -> list[str]:
    """Problems that would make the server refuse the upload, in plain words."""
    problems = []
    if not member.strip():
        problems.append("enter your name (who is adding this bag)")
    if not fields.get("mpn") and not fields.get("name"):
        problems.append("enter an MPN or a name")
    qty = fields.get("qty_remaining", fields.get("original_qty"))
    try:
        if qty in (None, "") or int(qty) < 0:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("quantity must be a whole number, 0 or more")
    return problems


def configured(env: dict | None = None) -> bool:
    env = load_env() if env is None else env
    return bool(env.get("INVENTORY_API_URL") and env.get("INVENTORY_API_KEY"))


def upload(fields: dict, member: str, env: dict | None = None, client: httpx.Client | None = None) -> int:
    env = load_env() if env is None else env
    if not configured(env):
        raise UploadError("INVENTORY_API_URL / INVENTORY_API_KEY are not set in .env")
    problems = check(fields, member)
    if problems:
        raise UploadError("; ".join(problems))
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        r = http.post(env["INVENTORY_API_URL"], json=payload(fields, member),
                      headers={"X-API-Key": env["INVENTORY_API_KEY"]})
    except httpx.HTTPError as e:
        raise UploadError(f"can't reach the inventory server: {e}") from e
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 201 and isinstance(body.get("id"), int):
        return body["id"]
    if r.status_code == 409:
        raise UploadError(f"this bag is already in the inventory as BUMRC-{body.get('id', 0):06d}", body.get("id"))
    if r.status_code == 401:
        raise UploadError("the server rejected the API key (check INVENTORY_API_KEY)")
    raise UploadError(f"server refused the upload ({r.status_code}): {body.get('error') or r.text[:200]}")
