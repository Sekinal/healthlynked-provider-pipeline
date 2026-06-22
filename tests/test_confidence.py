"""Confidence scoring: noisy-OR corroboration + conflict deflation."""

from pipeline.confidence import score_field
from pipeline.schemas import Evidence


def _ev(value, source, w, r=1.0):
    return Evidence(field="address", value=value, source=source, weight=w, recency=r)


def test_single_source_equals_weight_times_recency():
    s = score_field("address", [_ev("A", "NPI Registry", 0.9, 0.5)])
    assert abs(s.field_confidence - 0.45) < 1e-6
    assert s.n_independent == 1


def test_corroboration_raises_confidence():
    evs = [_ev("A", "NPI Registry", 0.9, 0.7), _ev("A", "Practice Website", 0.75, 0.9)]
    s = score_field("address", evs)
    # noisy-OR: 1 - (1-0.63)(1-0.675) = ~0.88 > either alone
    assert s.field_confidence > 0.85
    assert s.n_independent == 2


def test_conflict_deflates_confidence():
    evs = [_ev("A", "NPI Registry", 0.9, 1.0), _ev("B", "State Medical Board", 0.95, 1.0)]
    s = score_field("address", evs)
    # two strong but disagreeing sources -> confidence collapses
    assert s.field_confidence < 0.2
    assert s.conflicting_sources  # the losing value's source is flagged


def test_same_source_counts_once():
    evs = [_ev("A", "NPI Registry", 0.9, 1.0), _ev("A", "NPI Registry", 0.9, 1.0)]
    s = score_field("address", evs)
    assert s.n_independent == 1
    assert abs(s.field_confidence - 0.9) < 1e-6


def test_no_evidence_returns_none():
    assert score_field("address", []) is None


def test_correlated_web_family_does_not_inflate():
    """Round-3 #2: three correlated web sources collapse to one family's best
    observation — they must NOT noisy-OR into high independent confidence."""
    evs = [
        _ev("A", "Practice Website", 0.75, 1.0),
        _ev("A", "Google Business", 0.6, 1.0),
        _ev("A", "Third-Party Directory", 0.45, 1.0),
    ]
    s = score_field("address", evs)
    # collapses to the strongest web observation (0.75), not ~0.95
    assert abs(s.field_confidence - 0.75) < 1e-6
    assert s.n_families == 1


def test_known_plus_unknown_family_counts_only_known():
    evs = [_ev("A", "Practice Website", 0.75, 1.0), _ev("A", "Random Blog", 0.8, 1.0)]
    s = score_field("address", evs)
    assert s.n_families == 1  # "unknown" does not grant independence
