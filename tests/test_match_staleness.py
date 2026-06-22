"""Duplicate detection (blocking + fuzzy) and staleness scoring."""

from pipeline.match import find_duplicates
from pipeline.schemas import ProviderRecord
from pipeline.stage0_staleness import is_stale, staleness_score


def _rec(pid, npi, name, addr, phone):
    return ProviderRecord(provider_id=pid, npi=npi, provider_name=name,
                          address=addr, phone=phone)


def test_same_npi_is_hard_duplicate():
    a = _rec("HL_1", "123", "John Smith", "1 A St, Naples, FL 34102", "239-555-1000")
    b = _rec("HL_2", "123", "J Smith", "1 A St, Naples, FL 34102", "239-555-1000")
    dups = find_duplicates([a, b])
    assert any(d.verdict == "duplicate" for d in dups)


def test_same_person_same_address_diff_npi_flagged():
    a = _rec("HL_1", "111", "Robert Smith", "1 A St, Naples, FL 34102", "239-555-1000")
    b = _rec("HL_2", "222", "Robert Smith", "1 A St, Naples, FL 34102", "239-555-1000")
    dups = find_duplicates([a, b])
    assert dups and dups[0].score >= 0.92


def test_different_block_not_compared():
    a = _rec("HL_1", "111", "Robert Smith", "1 A St, Naples, FL 34102", "239-555-1000")
    b = _rec("HL_2", "222", "Robert Smith", "9 B St, Miami, FL 33101", "305-555-2000")
    dups = find_duplicates([a, b])
    assert dups == []  # no shared zip/phone -> not even compared


def test_staleness_old_record_is_stale():
    old = ProviderRecord(provider_id="HL", npi="1", phone="x", address="y",
                         website="z", last_verified_date="2020-01-01")
    assert is_stale(old)
    assert staleness_score(old) >= 0.5


def test_staleness_never_verified_is_stale():
    rec = ProviderRecord(provider_id="HL", npi="1", last_verified_date=None)
    assert is_stale(rec)
