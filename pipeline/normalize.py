"""Deterministic, free normalization of provider fields.

Normalizing *before* diffing means cosmetic differences (formatting, casing,
abbreviations) never trigger a "change" -> fewer LLM calls and less human review.
Each `normalize_*` returns a canonical comparable string ("" when empty/unknown).
"""

from __future__ import annotations

import re
from typing import Optional

import phonenumbers
from scourgify import normalize_address_record

# Minimal NUCC-ish specialty synonym map. In prod this is the full taxonomy file.
_SPECIALTY_SYNONYMS = {
    "cardiology": "Cardiovascular Disease",
    "cardiovascular disease": "Cardiovascular Disease",
    "heart doctor": "Cardiovascular Disease",
    "interventional cardiology": "Interventional Cardiology",
    "family medicine": "Family Medicine",
    "family practice": "Family Medicine",
    "internal medicine": "Internal Medicine",
    "pediatrics": "Pediatrics",
    "peds": "Pediatrics",
    "orthopedics": "Orthopaedic Surgery",
    "orthopaedics": "Orthopaedic Surgery",
    "dermatology": "Dermatology",
}

_CREDENTIAL_RE = re.compile(r"[,\s]+(m\.?d\.?|d\.?o\.?|n\.?p\.?|p\.?a\.?|do|md)\b", re.I)
_WS_RE = re.compile(r"\s+")


def _clean(s: Optional[str]) -> str:
    return _WS_RE.sub(" ", (s or "").strip())


def normalize_phone(raw: Optional[str]) -> str:
    raw = _clean(raw)
    if not raw:
        return ""
    try:
        num = phonenumbers.parse(raw, "US")
        if phonenumbers.is_valid_number(num):
            return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        pass
    digits = re.sub(r"\D", "", raw)
    return f"+1{digits}" if len(digits) == 10 else digits


def format_phone_display(canonical: str) -> str:
    try:
        num = phonenumbers.parse(canonical, "US")
        return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.NATIONAL)
    except phonenumbers.NumberParseException:
        return canonical


def normalize_address(raw: Optional[str]) -> str:
    raw = _clean(raw)
    if not raw:
        return ""
    try:
        rec = normalize_address_record(raw)
        parts = [
            rec.get("address_line_1"),
            rec.get("address_line_2"),
            rec.get("city"),
            rec.get("state"),
            (rec.get("postal_code") or "")[:5],
        ]
        canonical = ", ".join(p for p in parts if p)
        return canonical.upper()
    except Exception:
        # Fall back to a lightly cleaned uppercase string so comparison still works.
        return re.sub(r"[.,]+", "", raw).upper()


def normalize_name(raw: Optional[str]) -> str:
    raw = _clean(raw)
    if not raw:
        return ""
    no_cred = _CREDENTIAL_RE.sub("", raw)
    return _clean(no_cred).title()


def normalize_specialty(raw: Optional[str]) -> str:
    raw = _clean(raw)
    if not raw:
        return ""
    return _SPECIALTY_SYNONYMS.get(raw.lower(), raw.title())


def normalize_website(raw: Optional[str]) -> str:
    raw = _clean(raw).lower()
    if not raw:
        return ""
    raw = re.sub(r"^https?://", "", raw)
    raw = re.sub(r"^www\.", "", raw)
    return raw.rstrip("/")


def normalize_status(raw: Optional[str]) -> str:
    raw = _clean(raw).lower()
    if not raw:
        return ""
    if raw in {"active", "a", "true", "1", "yes"}:
        return "active"
    if raw in {"inactive", "i", "false", "0", "no", "deactivated", "retired"}:
        return "inactive"
    return raw


# field -> normalizer, so callers can normalize generically.
NORMALIZERS = {
    "provider_name": normalize_name,
    "specialty": normalize_specialty,
    "practice_name": lambda s: normalize_name(s) if s else "",
    "address": normalize_address,
    "phone": normalize_phone,
    "website": normalize_website,
    "active_status": normalize_status,
}


def normalize_field(field: str, value: Optional[str]) -> str:
    fn = NORMALIZERS.get(field, _clean)
    return fn(value)
