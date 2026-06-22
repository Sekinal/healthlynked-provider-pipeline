"""Decision routing: turn scored evidence into a spec-shaped Recommendation.

The existing HealthLynked value is treated as a *possibly-stale baseline*, not as
counter-evidence — we're checking whether it still holds. A field changes only
when external sources point to a different normalized value than the record.
"""

from __future__ import annotations

from statistics import mean

from pipeline.config import (
    AUTHORITATIVE_SOURCES,
    BASELINE_SOURCES,
    HIGH_RISK_FIELDS,
    settings,
)
from pipeline.confidence import score_field
from pipeline.normalize import format_phone_display, normalize_field
from pipeline.schemas import (
    Action,
    Evidence,
    FieldChange,
    ProviderRecord,
    Recommendation,
    TRACKED_FIELDS,
)


def _display(field: str, value: str) -> str:
    return format_phone_display(value) if field == "phone" else value


def _auto_eligible(score, field: str) -> bool:
    if field in HIGH_RISK_FIELDS:
        return False  # never auto-update high-risk fields
    if score.field_confidence < settings.auto_update_threshold:
        return False
    has_authoritative = any(s in AUTHORITATIVE_SOURCES for s in score.supporting_sources)
    # Require either an authoritative source or >= 2 *independent families*
    # (correlated web sources don't count twice).
    return score.n_families >= 2 or has_authoritative


def build_recommendation(record: ProviderRecord, evidence: list[Evidence]) -> Recommendation:
    # Hard guarantee: the stale baseline can never act as corroborating evidence.
    evidence = [e for e in evidence if e.source not in BASELINE_SOURCES]
    by_field: dict[str, list[Evidence]] = {}
    for e in evidence:
        by_field.setdefault(e.field, []).append(e)

    sources_consulted = sorted({e.source for e in evidence})
    changes: list[FieldChange] = []
    review_flags: list[str] = []
    high_risk_conflict = False

    for field in TRACKED_FIELDS:
        score = score_field(field, by_field.get(field, []))
        if score is None:
            continue
        current_norm = normalize_field(field, getattr(record, field, None))

        if score.winning_value == current_norm:
            # Current value won — but a competing source may still contest it.
            # Don't silently confirm a meaningful conflict (codex finding #2/#3).
            # Flag when a strong competitor exists OR when the win is a low-confidence
            # near-tie (field_confidence below the human-review floor).
            contested = bool(score.conflicting_sources) and (
                score.runner_up_evidence >= settings.contest_threshold
                or score.dissent_evidence >= settings.contest_threshold
                or score.field_confidence < settings.human_review_floor
            )
            if contested:
                review_flags.append(
                    f"{field}: current value contested by {', '.join(score.conflicting_sources)} "
                    f"(competing evidence {score.runner_up_evidence:.2f})"
                )
                if field in HIGH_RISK_FIELDS:
                    high_risk_conflict = True
            continue

        auto = _auto_eligible(score, field)
        changes.append(
            FieldChange(
                field=field,
                old_value=_display(field, getattr(record, field, None) or ""),
                new_value=_display(field, score.winning_value),
                confidence_score=score.field_confidence,
                supporting_sources=score.supporting_sources,
                conflicting_sources=score.conflicting_sources,
                auto_eligible=auto,
            )
        )

    rec = Recommendation(
        provider_id=record.provider_id,
        npi=record.npi,
        change_detected=bool(changes),
        review_flags=review_flags,
        changes=changes,
        sources_consulted=sources_consulted,
    )

    if not changes and not review_flags:
        rec.recommended_action = Action.no_change
        rec.overall_confidence = 1.0
        covered = sorted(by_field.keys())
        rec.reason = (
            f"No contradicting evidence found in consulted sources for: {', '.join(covered)}."
            if covered else "No external evidence retrieved; nothing to update."
        )
        return rec

    rec.overall_confidence = round(mean(c.confidence_score for c in changes), 4) if changes else 0.0

    # An unresolved contested field (esp. high-risk) blocks auto-update of the
    # whole record, even if other changed fields are individually auto-eligible.
    if changes and all(c.auto_eligible for c in changes) and not review_flags:
        rec.recommended_action = Action.auto_update
        fields = ", ".join(c.field for c in changes)
        rec.reason = (
            f"Updated {fields} confirmed by reliable sources "
            f"(>= {settings.auto_update_threshold:.2f} confidence)."
        )
    else:
        rec.recommended_action = Action.human_review
        reasons = []
        for c in changes:
            if c.auto_eligible:
                continue
            if c.field in HIGH_RISK_FIELDS:
                reasons.append(f"{c.field} is high-risk (manual confirmation required)")
            elif c.conflicting_sources:
                reasons.append(f"{c.field} has conflicting sources")
            else:
                reasons.append(f"{c.field} confidence below auto threshold")
        reasons.extend(review_flags)
        if high_risk_conflict:
            reasons.append("unresolved high-risk conflict blocks auto-update")
        rec.reason = "; ".join(reasons) + "." if reasons else "Routed to human review."

    return rec
