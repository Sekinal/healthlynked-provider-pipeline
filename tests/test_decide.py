"""Decision routing + safe auto-update gates."""

from pipeline.decide import build_recommendation
from pipeline.schemas import Action, Evidence, ProviderRecord


def _rec(**kw):
    base = dict(provider_id="HL", npi="1")
    base.update(kw)
    return ProviderRecord(**base)


def test_no_change_when_record_matches_source():
    record = _rec(phone="(239) 624-4200")
    ev = [Evidence(field="phone", value="+12396244200", source="NPI Registry",
                   weight=0.9, recency=1.0)]
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.no_change
    assert not out.change_detected


def test_auto_update_fresh_authoritative_nonrisky():
    record = _rec(address="100 Main St, Naples, FL 34102")
    ev = [Evidence(field="address", value="250 HEALTH PARK DR, FORT MYERS, FL, 33908",
                   source="NPI Registry", weight=0.9, recency=1.0)]
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.auto_update
    assert out.changes[0].auto_eligible


def test_high_risk_field_always_human_review():
    record = _rec(provider_name="Bob Smith")
    ev = [Evidence(field="provider_name", value="Robert Smith", source="NPI Registry",
                   weight=0.9, recency=1.0)]
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.human_review
    assert not out.changes[0].auto_eligible


def test_single_stale_source_below_threshold_human_review():
    record = _rec(address="100 Main St, Naples, FL 34102")
    ev = [Evidence(field="address", value="1351 PINE ST, NAPLES, FL, 34104",
                   source="NPI Registry", weight=0.9, recency=0.5)]  # 0.45 < 0.85
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.human_review


def test_conflict_routes_to_human_review():
    record = _rec(address="100 Main St, Naples, FL 34102")
    ev = [
        Evidence(field="address", value="A ST", source="NPI Registry", weight=0.9, recency=1.0),
        Evidence(field="address", value="B ST", source="State Medical Board", weight=0.95, recency=1.0),
    ]
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.human_review
    assert out.changes[0].conflicting_sources


# --- regression tests for codex adversarial-review findings ---

def test_contested_confirmation_when_current_wins_flags_review():
    """Codex #2: current value narrowly wins but a strong source disagrees ->
    must NOT silently confirm; route to human review."""
    record = _rec(active_status="active")
    ev = [
        Evidence(field="active_status", value="active", source="NPI Registry", weight=0.9, recency=1.0),
        Evidence(field="active_status", value="inactive", source="State Medical Board", weight=0.8, recency=0.95),
    ]
    out = build_recommendation(record, ev)
    # 'inactive' actually outweighs here -> becomes a high-risk change -> review
    assert out.recommended_action == Action.human_review


def test_high_risk_contest_blocks_other_autoupdates():
    """Codex #3: an unresolved high-risk conflict blocks auto-updating a safe field."""
    record = _rec(active_status="active", address="100 Main St, Naples, FL 34102")
    ev = [
        # active narrowly wins but board strongly contests -> contested high-risk
        Evidence(field="active_status", value="active", source="NPI Registry", weight=0.95, recency=1.0),
        Evidence(field="active_status", value="inactive", source="State Medical Board", weight=0.7, recency=0.9),
        # a clean, fresh authoritative address change that alone would auto-update
        Evidence(field="address", value="250 HEALTH PARK DR, FORT MYERS, FL, 33908",
                 source="NPI Registry", weight=0.9, recency=1.0),
    ]
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.human_review
    assert out.review_flags  # the contested status is surfaced


def test_correlated_web_sources_not_two_independent():
    """Codex #4: Practice Website + Google Business share a family -> not enough
    independence to auto-update without an authoritative source."""
    record = _rec(address="100 Main St, Naples, FL 34102")
    ev = [
        Evidence(field="address", value="250 HEALTH PARK DR, FORT MYERS, FL, 33908",
                 source="Practice Website", weight=0.8, recency=1.0),
        Evidence(field="address", value="250 HEALTH PARK DR, FORT MYERS, FL, 33908",
                 source="Google Business", weight=0.7, recency=1.0),
    ]
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.human_review
    assert not out.changes[0].auto_eligible


# --- regression tests for codex SECOND-ROUND findings ---

def test_unmapped_sources_not_independent_families():
    """Round-2 #1: two unmapped source labels must not count as 2 families."""
    record = _rec(phone="239-555-0000")
    ev = [
        Evidence(field="phone", value="+12393439700", source="Random Blog", weight=0.8, recency=1.0),
        Evidence(field="phone", value="+12393439700", source="Some Aggregator X", weight=0.7, recency=1.0),
    ]
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.human_review
    assert not out.changes[0].auto_eligible


def test_low_confidence_near_tie_on_current_value_flags_review():
    """Round-2 #2: current wins by a hair (low confidence) -> must flag, not
    silently confirm, and must block unrelated auto-updates."""
    record = _rec(active_status="active", address="100 Main St, Naples, FL 34102")
    ev = [
        Evidence(field="active_status", value="active", source="NPI Registry", weight=0.5, recency=1.0),
        Evidence(field="active_status", value="inactive", source="State Medical Board", weight=0.49, recency=1.0),
        Evidence(field="address", value="250 HEALTH PARK DR, FORT MYERS, FL, 33908",
                 source="NPI Registry", weight=0.9, recency=1.0),
    ]
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.human_review
    assert out.review_flags


def test_fragmented_dissent_deflates_confidence():
    """Round-2 #3: many weak conflicting values aggregate to erode confidence."""
    from pipeline.confidence import score_field
    evs = [
        Evidence(field="phone", value="A", source="NPI Registry", weight=0.9, recency=1.0),
        Evidence(field="phone", value="B", source="Third-Party Directory", weight=0.45, recency=0.9),
        Evidence(field="phone", value="C", source="Google Business", weight=0.45, recency=0.9),
        Evidence(field="phone", value="D", source="Practice Website", weight=0.45, recency=0.9),
    ]
    s = score_field("phone", evs)
    assert s.field_confidence < 0.7


def test_baseline_source_never_corroborates():
    """Round-3 #4: evidence labeled as the stale baseline is dropped, so it can
    neither win nor grant independence."""
    record = _rec(address="100 Main St, Naples, FL 34102")
    ev = [
        Evidence(field="address", value="250 HEALTH PARK DR, FORT MYERS, FL, 33908",
                 source="Practice Website", weight=0.8, recency=1.0),
        # a baseline-labeled echo of the new value must NOT count as a 2nd family
        Evidence(field="address", value="250 HEALTH PARK DR, FORT MYERS, FL, 33908",
                 source="HealthLynked", weight=0.5, recency=1.0),
    ]
    out = build_recommendation(record, ev)
    assert out.recommended_action == Action.human_review
    assert out.changes[0].supporting_sources == ["Practice Website"]
