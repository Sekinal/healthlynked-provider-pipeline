"""Adversarial safety tests for the provider-directory update pipeline."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from pipeline.confidence import score_field
from pipeline.decide import build_recommendation
from pipeline.match import find_duplicates
from pipeline.normalize import (
    normalize_address,
    normalize_field,
    normalize_name,
    normalize_phone,
    normalize_specialty,
    normalize_status,
)
from pipeline.schemas import Action, Evidence, ProviderRecord
from pipeline.stage0_staleness import is_stale, staleness_score


def _ev(
    field: str,
    value: str,
    source: str,
    weight: float = 0.9,
    recency: float = 1.0,
) -> Evidence:
    return Evidence(field=field, value=value, source=source, weight=weight, recency=recency)


def _record(**overrides) -> ProviderRecord:
    base = {
        "provider_id": "HL_HARD",
        "npi": "1234567890",
        "provider_name": "Jane Example",
        "specialty": "Family Medicine",
        "practice_name": "Example Clinic",
        "address": "100 Main St, Naples, FL 34102",
        "phone": "239-555-0100",
        "website": "https://example.test",
        "active_status": "active",
        "last_verified_date": date.today(),
    }
    base.update(overrides)
    return ProviderRecord(**base)


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_name", "Jane Example-Garcia, MD"),
        ("active_status", "inactive"),
    ],
)
def test_high_risk_tracked_fields_never_auto_update_even_with_clean_authority(field, value):
    record = _record()
    evidence = [_ev(field, normalize_field(field, value), "State Medical Board", 1.0, 1.0)]

    rec = build_recommendation(record, evidence)

    assert rec.recommended_action == Action.human_review
    assert len(rec.changes) == 1
    assert rec.changes[0].field == field
    assert rec.changes[0].confidence_score >= 0.85
    assert rec.changes[0].auto_eligible is False


def test_npi_evidence_cannot_trigger_an_auto_update_path():
    rec = build_recommendation(
        _record(npi="1111111111"),
        [_ev("npi", "2222222222", "NPI Registry", 1.0, 1.0)],
    )

    assert rec.recommended_action != Action.auto_update
    assert all(change.field != "npi" for change in rec.changes)


def test_correlated_web_echoes_do_not_meet_independence_gate_despite_high_confidence():
    new_phone = normalize_phone("239-444-9898")
    rec = build_recommendation(
        _record(phone="239-555-0100"),
        [
            _ev("phone", new_phone, "Practice Website", 0.99, 1.0),
            _ev("phone", new_phone, "Google Business", 0.99, 1.0),
            _ev("phone", new_phone, "Third-Party Directory", 0.99, 1.0),
        ],
    )

    assert rec.changes[0].confidence_score >= 0.85
    assert rec.changes[0].auto_eligible is False
    assert rec.recommended_action == Action.human_review


def test_authoritative_source_can_auto_update_safe_field_at_threshold_without_second_family():
    rec = build_recommendation(
        _record(phone="239-555-0100"),
        [_ev("phone", normalize_phone("239-444-9898"), "CMS Doctors & Clinicians", 0.85, 1.0)],
    )

    assert rec.recommended_action == Action.auto_update
    assert rec.changes[0].auto_eligible is True
    assert rec.changes[0].confidence_score == pytest.approx(0.85)


def test_conflicting_strong_values_deflate_to_review_instead_of_auto_update():
    rec = build_recommendation(
        _record(address="100 Main St, Naples, FL 34102"),
        [
            _ev("address", "250 HEALTH PARK DR, FORT MYERS, FL, 33908", "NPI Registry", 0.95, 1.0),
            _ev("address", "1000 TAMIAMI TRL N, NAPLES, FL, 34102", "State Medical Board", 0.94, 1.0),
        ],
    )

    assert rec.recommended_action == Action.human_review
    assert rec.changes[0].confidence_score < 0.1
    assert rec.changes[0].conflicting_sources == ["State Medical Board"]
    assert rec.changes[0].auto_eligible is False


def test_many_fragmented_conflicts_aggregate_and_block_overconfident_winner():
    evidence = [_ev("address", "WINNER", "NPI Registry", 0.95, 1.0)]
    evidence.extend(
        _ev("address", f"LOSER-{i}", source, 0.45, 1.0)
        for i, source in enumerate(
            [
                "Practice Website",
                "Google Business",
                "Third-Party Directory",
                "Random Blog",
                "Another Unknown Scraper",
            ]
        )
    )

    score = score_field("address", evidence)

    assert score is not None
    assert score.winning_value == "WINNER"
    assert score.winner_evidence == pytest.approx(0.95)
    assert score.dissent_evidence > score.runner_up_evidence
    assert score.field_confidence < 0.35
    assert set(score.conflicting_sources) >= {"Practice Website", "Random Blog"}


def test_existing_record_and_baseline_labeled_evidence_are_not_corroboration():
    new_address = "250 HEALTH PARK DR, FORT MYERS, FL, 33908"
    rec = build_recommendation(
        _record(address="100 Main St, Naples, FL 34102"),
        [
            _ev("address", new_address, "Practice Website", 0.99, 1.0),
            _ev("address", new_address, "HealthLynked", 1.0, 1.0),
            _ev("address", new_address, "existing_record", 1.0, 1.0),
            _ev("address", new_address, "baseline", 1.0, 1.0),
        ],
    )

    assert rec.sources_consulted == ["Practice Website"]
    assert rec.changes[0].supporting_sources == ["Practice Website"]
    assert rec.changes[0].auto_eligible is False
    assert rec.recommended_action == Action.human_review


def test_duplicate_same_source_rows_do_not_inflate_support_or_independence():
    evidence = [
        _ev("phone", "+12395550199", "NPI Registry", 0.9, 1.0),
        _ev("phone", "+12395550199", "NPI Registry", 0.9, 1.0),
        _ev("phone", "+12395550199", "NPI Registry", 0.1, 1.0),
    ]

    score = score_field("phone", evidence)

    assert score is not None
    assert score.supporting_sources == ["NPI Registry"]
    assert score.n_independent == 1
    assert score.n_families == 1
    assert score.field_confidence == pytest.approx(0.9)


def test_empty_none_and_zero_strength_evidence_are_safe_noops_or_zero_confidence():
    assert score_field("phone", []) is None
    assert score_field("phone", None) is None  # type: ignore[arg-type]
    assert score_field("phone", [_ev("phone", "", "NPI Registry", 1.0, 1.0)]) is None

    zero_weight = score_field("phone", [_ev("phone", "+12395550199", "NPI Registry", 0.0, 1.0)])
    zero_recency = score_field("phone", [_ev("phone", "+12395550199", "NPI Registry", 1.0, 0.0)])

    assert zero_weight is not None and zero_weight.field_confidence == 0.0
    assert zero_recency is not None and zero_recency.field_confidence == 0.0


@pytest.mark.parametrize(
    "raw,normalizer",
    [
        ("+1 (239) 555-0100 ext. 77", normalize_phone),
        ("  José   Álvarez-García, M.D.  ", normalize_name),
        ("  peds  ", normalize_specialty),
        (" Retired ", normalize_status),
        ("1600 Pennsylvania Ave NW, Washington, DC 20500", normalize_address),
    ],
)
def test_normalizers_are_idempotent_for_unicode_and_messy_formatting(raw, normalizer):
    once = normalizer(raw)
    twice = normalizer(once)

    assert once == twice


def test_normalize_field_collapses_none_empty_and_malformed_values_deterministically():
    assert normalize_field("provider_name", None) == ""
    assert normalize_field("address", "") == ""
    assert normalize_phone("CALL-ME-NOW") == ""
    assert normalize_phone("239-555-0100 x999") == "+12395550100"
    assert normalize_phone("12345") == "12345"
    assert normalize_address("@@ not,,, an address ###") == "@@ NOT AN ADDRESS ###"
    assert normalize_status("temporarily closed?") == "temporarily closed?"


def test_current_value_winning_with_strong_dissent_sets_review_flag_and_blocks_safe_update():
    rec = build_recommendation(
        _record(active_status="active", phone="239-555-0100"),
        [
            _ev("active_status", "active", "NPI Registry", 0.95, 1.0),
            _ev("active_status", "inactive", "State Medical Board", 0.70, 1.0),
            _ev("phone", normalize_phone("239-444-9898"), "CMS Doctors & Clinicians", 0.9, 1.0),
        ],
    )

    assert rec.review_flags
    assert rec.changes and rec.changes[0].field == "phone"
    assert rec.changes[0].auto_eligible is True
    assert rec.recommended_action == Action.human_review


def test_duplicate_matching_blocks_cross_record_comparison_without_shared_zip_or_phone():
    a = _record(
        provider_id="A",
        npi="1111111111",
        provider_name="Maria-Jose Alvarez Garcia MD",
        address="10 North St, Naples, FL 34102",
        phone="239-555-0100",
    )
    b = _record(
        provider_id="B",
        npi="2222222222",
        provider_name="Dr. María José Álvarez-García",
        address="10 North St, Miami, FL 33101",
        phone="305-555-0100",
    )

    assert find_duplicates([a, b]) == []


def test_duplicate_matching_still_hard_matches_same_npi_even_when_blocking_disagrees():
    a = _record(provider_id="A", npi="1111111111", address="", phone="")
    b = _record(provider_id="B", npi="1111111111", address="No Shared Block, Tampa, FL 33602", phone="813-555-0100")

    matches = find_duplicates([a, b])

    assert len(matches) == 1
    assert matches[0].verdict == "duplicate"
    assert matches[0].score == 1.0


def test_staleness_extremes_are_bounded_and_missing_volatility_fields_matter():
    fresh_complete = _record(last_verified_date=date.today())
    ancient = _record(last_verified_date=date.today() - timedelta(days=3650))
    never_verified_complete = _record(last_verified_date=None)
    fresh_missing_high_volatility = _record(
        phone=None,
        address=None,
        website=None,
        last_verified_date=date.today(),
    )
    never_verified_missing_high_volatility = _record(
        phone=None,
        address=None,
        website=None,
        last_verified_date=None,
    )

    assert staleness_score(fresh_complete) == 0.0
    assert staleness_score(ancient) == 0.7
    assert staleness_score(never_verified_complete) == 0.7
    assert staleness_score(never_verified_missing_high_volatility) == 1.0
    assert is_stale(ancient)
    assert is_stale(never_verified_complete)
    assert staleness_score(fresh_missing_high_volatility) > staleness_score(fresh_complete)
    assert 0.0 <= staleness_score(fresh_missing_high_volatility) <= 1.0
