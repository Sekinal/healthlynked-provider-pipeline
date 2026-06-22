"""Normalization: cosmetic differences must collapse so they never trigger diffs."""

from pipeline.normalize import (
    normalize_address,
    normalize_field,
    normalize_phone,
    normalize_specialty,
    normalize_name,
    normalize_status,
)


def test_phone_formats_collapse():
    canon = "+12395551234"
    assert normalize_phone("239-555-1234") == canon
    assert normalize_phone("(239) 555-1234") == canon
    assert normalize_phone("239.555.1234") == canon
    assert normalize_phone("+1 239 555 1234") == canon


def test_phone_idempotent():
    once = normalize_phone("(239) 555-1234")
    assert normalize_phone(once) == once


def test_address_idempotent_and_canonical():
    a = normalize_address("100 Main St, Naples, FL 34102")
    assert normalize_address(a) == a
    assert "NAPLES" in a and "FL" in a


def test_specialty_synonyms():
    assert normalize_specialty("Cardiology") == "Cardiovascular Disease"
    assert normalize_specialty("family practice") == "Family Medicine"


def test_name_strips_credentials():
    assert normalize_name("John Smith, MD") == "John Smith"
    assert normalize_name("Jane Doe DO") == "Jane Doe"


def test_status_maps():
    assert normalize_status("A") == "active"
    assert normalize_status("retired") == "inactive"


def test_empty_values():
    for f in ("phone", "address", "specialty", "provider_name"):
        assert normalize_field(f, None) == ""
        assert normalize_field(f, "") == ""
