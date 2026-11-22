"""Look a part up on DigiKey and turn the answer into inventory fields.

Uses DigiKey's Product Information API v4 with a client-credentials token
(DIGIKEY_CLIENT_ID / DIGIKEY_CLIENT_SECRET in .env). The token lasts ~10 min
and is reused until it's about to expire.

    dk = DigiKey()
    product = dk.find("497-6610-1-ND")                  # DigiKey PN or MPN
    product = dk.find("1N5819", manufacturer="STMicro")  # MPN shared by several makers
    fields = to_fields(product)
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import quote

import httpx

from scanner.config import load_env

TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
API = "https://api.digikey.com/products/v4"
TIMEOUT = 15


class DigiKeyError(RuntimeError):
    pass


class DigiKey:
    def __init__(self, env: dict | None = None, client: httpx.Client | None = None):
        env = load_env() if env is None else env
        self.client_id = env.get("DIGIKEY_CLIENT_ID") or ""
        self.client_secret = env.get("DIGIKEY_CLIENT_SECRET") or ""
        self.http = client or httpx.Client(timeout=TIMEOUT)
        self._token: str | None = None
        self._token_expires = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _headers(self) -> dict:
        if not self.configured:
            raise DigiKeyError("DigiKey API keys are not set in .env")
        if not self._token or time.time() > self._token_expires - 30:
            try:
                r = self.http.post(TOKEN_URL, data={"client_id": self.client_id, "client_secret": self.client_secret,
                                                    "grant_type": "client_credentials"})
            except httpx.HTTPError as e:
                raise DigiKeyError(f"can't reach DigiKey: {e}") from e
            if r.status_code != 200:
                raise DigiKeyError(f"DigiKey login failed ({r.status_code}); check the API keys in .env")
            body = r.json()
            self._token = body["access_token"]
            self._token_expires = time.time() + int(body.get("expires_in", 600))
        return {"Authorization": f"Bearer {self._token}", "X-DIGIKEY-Client-Id": self.client_id,
                "X-DIGIKEY-Locale-Site": "US", "X-DIGIKEY-Locale-Language": "en",
                "X-DIGIKEY-Locale-Currency": "USD"}

    def _get(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            return self.http.request(method, url, headers=self._headers(), **kw)
        except httpx.HTTPError as e:
            raise DigiKeyError(f"can't reach DigiKey: {e}") from e

    def details(self, part_number: str) -> dict | None:
        """Exact lookup by DigiKey PN or MPN. None if not found or ambiguous."""
        r = self._get("GET", f"{API}/search/{quote(part_number, safe='')}/productdetails")
        if r.status_code == 200:
            return r.json().get("Product")
        if r.status_code == 404:
            return None
        raise DigiKeyError(f"DigiKey lookup failed ({r.status_code})")

    def exact_matches(self, mpn: str) -> list[dict]:
        r = self._get("POST", f"{API}/search/keyword", json={"Keywords": mpn, "Limit": 10})
        if r.status_code != 200:
            raise DigiKeyError(f"DigiKey search failed ({r.status_code})")
        body = r.json()
        return [p for p in (body.get("ExactMatches") or []) + (body.get("Products") or [])
                if (p.get("ManufacturerProductNumber") or "").upper() == mpn.upper()]

    def find(self, part_number: str, manufacturer: str | None = None) -> dict | None:
        """Product for a DigiKey PN or MPN. If several manufacturers make that MPN,
        pick the one whose name matches `manufacturer` (None if still unclear)."""
        if not part_number:
            return None
        product = self.details(part_number)
        if product:
            return product
        matches = self.exact_matches(part_number)
        if manufacturer:
            m = _norm(manufacturer)
            matches = [p for p in matches if _norm(p.get("Manufacturer", {}).get("Name", "")).startswith(m[:5])]
        makers = {p.get("Manufacturer", {}).get("Name") for p in matches}
        if len(makers) == 1:
            # keyword results are summaries; fetch the full record via its DigiKey PN
            variations = matches[0].get("ProductVariations") or []
            if variations:
                return self.details(variations[0]["DigiKeyProductNumber"]) or matches[0]
            return matches[0]
        return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("ü", "u"))


# ---------------------------------------------------------------- DigiKey product -> inventory fields

SI = {"p": 1e-12, "n": 1e-9, "µ": 1e-6, "u": 1e-6, "m": 1e-3, "": 1.0, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}
VALUE_RE = re.compile(r"^\s*([\d.]+)\s*([pnµumkKMG]?)\s*(F|H|Ohms?|Ω)", re.I)

# (word in a category name, our type)  first hit wins
TYPE_RULES = [
    ("ferrite", "INDUCTOR"),      # ferrite beads are filed with inductors
    ("connector", "CONNECTOR"),
    ("resistor", "RESISTOR"),
    ("capacitor", "CAPACITOR"),
    ("inductor", "INDUCTOR"),
    ("diode", "DIODE"),
    ("crystal", "CRYSTAL"),
    ("oscillator", "CRYSTAL"),
    ("sensor", "SENSOR"),
    ("integrated circuits", "IC"),
]


def _categories(cat: dict | None) -> list[str]:
    if not cat:
        return []
    out = [cat.get("Name", "")]
    for child in cat.get("ChildCategories") or []:
        out += _categories(child)
    return out


def _params(product: dict) -> dict[str, str]:
    return {p["ParameterText"]: p["ValueText"] for p in product.get("Parameters") or []
            if p.get("ValueText") not in (None, "", "-")}


def parse_value(text: str | None) -> tuple[float | None, str | None]:
    """'4.99 kOhms' -> (4990.0, '4.99kΩ'); '1 µF' -> (1e-06, '1µF')."""
    if not text:
        return None, None
    m = VALUE_RE.match(text)
    if not m:
        return None, None
    num, prefix, unit = m.groups()
    prefix = "µ" if prefix == "u" else prefix
    unit = "Ω" if unit.lower().startswith("ohm") or unit == "Ω" else unit.upper()
    return float(num) * SI[prefix], f"{num}{prefix}{unit}"


def _package(params: dict[str, str]) -> str | None:
    pkg = params.get("Supplier Device Package") or params.get("Package / Case")
    if not pkg:
        return None
    return re.split(r"\s*[,(]", pkg)[0].strip() or None


def _mount(params: dict[str, str], categories: list[str]) -> str | None:
    text = " ".join([params.get("Mounting Type", "")] + categories).lower()
    if "surface mount" in text or "smd" in text:
        return "SMD"
    if "through hole" in text:
        return "THT"
    return None


def _https(url: str | None) -> str | None:
    if not url:
        return None
    return "https:" + url if url.startswith("//") else url


def to_fields(product: dict | None) -> dict:
    """Inventory fields from a DigiKey product (only the ones DigiKey knows)."""
    if not product:
        return {}
    params = _params(product)
    cats = _categories(product.get("Category"))
    cat_text = " ".join(cats).lower()
    ptype = next((t for word, t in TYPE_RULES if word in cat_text), "OTHER")

    value_param = {"RESISTOR": "Resistance", "CAPACITOR": "Capacitance", "INDUCTOR": "Inductance"}.get(ptype)
    value_num, value_text = parse_value(params.get(value_param)) if value_param else (None, None)

    voltage = params.get("Voltage - Rated") or params.get("Voltage - DC Reverse (Vr) (Max)")
    power = params.get("Power (Watts)")
    desc = product.get("Description") or {}
    return {
        "mpn": product.get("ManufacturerProductNumber"),
        "manufacturer": (product.get("Manufacturer") or {}).get("Name"),
        "name": desc.get("ProductDescription"),
        "description": desc.get("DetailedDescription"),
        "type": ptype,
        "mount": _mount(params, cats),
        "package": _package(params),
        "value_num": value_num,
        "value_text": value_text,
        "tolerance": params.get("Tolerance"),
        "voltage_rating": voltage.replace(" ", "") if voltage else None,
        "power_rating": power.split(",")[0].strip() if power else None,
        "dielectric": params.get("Temperature Coefficient") if ptype == "CAPACITOR" else None,
        "digikey_url": product.get("ProductUrl"),
        "datasheet_url": _https(product.get("DatasheetUrl")),
        "params_json": json.dumps(params, ensure_ascii=False),
    }
