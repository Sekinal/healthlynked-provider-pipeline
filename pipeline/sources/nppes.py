"""Live NPPES NPI Registry client — the free, authoritative top of the funnel.

Public CMS API, no key, rate-limited. We add polite backoff + an on-disk cache
so re-runs cost nothing (idempotent lookups). Maps the response to normalized
Evidence rows tagged source="NPI Registry".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from pipeline.config import weight_for
from pipeline.normalize import normalize_field
from pipeline.recency import recency_from_date
from pipeline.schemas import Evidence

API_URL = "https://npiregistry.cms.hhs.gov/api/"
SOURCE = "NPI Registry"
_CACHE_DIR = Path(".cache/nppes")


def _cache_path(npi: str) -> Path:
    return _CACHE_DIR / f"{npi}.json"


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=1, max=20))
def _fetch_raw(npi: str) -> dict:
    params = {"version": "2.1", "number": npi}
    with httpx.Client(timeout=20) as client:
        resp = client.get(API_URL, params=params)
        resp.raise_for_status()
        return resp.json()


def fetch_npi(npi: str, use_cache: bool = True) -> dict:
    """Return the raw NPPES payload for an NPI, caching on disk."""
    path = _cache_path(npi)
    if use_cache and path.exists():
        return json.loads(path.read_text())
    data = _fetch_raw(npi)
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def content_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _pick_location(addresses: list[dict]) -> Optional[dict]:
    loc = [a for a in addresses if a.get("address_purpose") == "LOCATION"]
    return loc[0] if loc else (addresses[0] if addresses else None)


def extract_evidence(npi: str, payload: Optional[dict] = None) -> list[Evidence]:
    """Map an NPPES payload to normalized Evidence rows."""
    payload = payload or fetch_npi(npi)
    results = payload.get("results") or []
    if not results:
        return []
    r = results[0]
    basic = r.get("basic", {}) or {}
    recency = recency_from_date(basic.get("last_updated"))

    values: dict[str, str] = {}

    # name
    if basic.get("organization_name"):
        values["practice_name"] = basic["organization_name"]
        values["provider_name"] = basic.get("authorized_official_first_name", "")
    name = " ".join(
        p for p in [basic.get("first_name"), basic.get("last_name")] if p
    )
    if name:
        values["provider_name"] = name

    # specialty (primary taxonomy)
    taxonomies = r.get("taxonomies", []) or []
    primary = next((t for t in taxonomies if t.get("primary")), taxonomies[0] if taxonomies else None)
    if primary and primary.get("desc"):
        values["specialty"] = primary["desc"]

    # address + phone (practice location)
    loc = _pick_location(r.get("addresses", []) or [])
    if loc:
        addr = ", ".join(
            p for p in [loc.get("address_1"), loc.get("address_2"), loc.get("city"),
                        loc.get("state"), loc.get("postal_code")] if p
        )
        if addr:
            values["address"] = addr
        if loc.get("telephone_number"):
            values["phone"] = loc["telephone_number"]

    # active status: basic.status "A" = active
    status = basic.get("status")
    if status:
        values["active_status"] = "active" if status == "A" else "inactive"

    evidence: list[Evidence] = []
    for field, raw in values.items():
        if not raw:
            continue
        evidence.append(
            Evidence(
                field=field,
                value=normalize_field(field, str(raw)),
                raw_value=str(raw),
                source=SOURCE,
                weight=weight_for(SOURCE, field),
                recency=recency,
            )
        )
    return evidence
