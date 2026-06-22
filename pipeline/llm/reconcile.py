"""Two-tier LLM reconciliation — fires ONLY on genuine conflicts.

Tier 1 (cheap, e.g. DeepSeek V4 Flash) resolves the conflict. If it is unsure
(self-confidence below threshold) or disagrees with the deterministic winner, we
escalate to Tier 2 (GLM 5.2). If still unsure, the field stays in human_review.
Every call is cost-logged; the chosen tier is recorded for the audit trail.
"""

from __future__ import annotations

from statistics import mean

from pipeline import audit
from pipeline.config import (
    AUTHORITATIVE_SOURCES,
    HIGH_RISK_FIELDS,
    family_of,
    settings,
)
from pipeline.llm.openrouter import complete_json
from pipeline.normalize import format_phone_display
from pipeline.schemas import Action, Evidence, FieldChange, ProviderRecord, Recommendation

_SYSTEM = (
    "You are a healthcare provider-data reconciliation engine. Multiple sources "
    "disagree about one field. Choose the single most likely CURRENT correct value, "
    "weighing source reliability and recency. Authoritative government sources (NPI "
    "Registry, State Medical Board, CMS) outrank practice websites and aggregators. "
    "Reply with JSON only: {\"value\": str, \"confidence\": number (0-1), "
    "\"supporting_sources\": [str], \"rationale\": str}."
)


def _candidates(field: str, evidence: list[Evidence]) -> dict[str, list[Evidence]]:
    out: dict[str, list[Evidence]] = {}
    for e in evidence:
        if e.field == field and e.value:
            out.setdefault(e.value, []).append(e)
    return out


def _prompt(field: str, current: str, cands: dict[str, list[Evidence]]) -> str:
    lines = [f"Field: {field}", f"Current record value: {current or '(none)'}", "", "Candidate values:"]
    for value, evs in cands.items():
        for e in evs:
            lines.append(f"- {value!r}  [source={e.source}, reliability={e.weight}, recency={e.recency:.2f}]")
    return "\n".join(lines)


def _reconcile_field(field: str, current: str, cands: dict[str, list[Evidence]],
                     run_id: int | None, npi: str) -> tuple[str, float, list[str], str]:
    """Return (value, confidence, supporting_sources, tier_used)."""
    user = _prompt(field, current, cands)
    deterministic_best = max(
        cands, key=lambda v: max(e.weight * e.recency for e in cands[v])
    )

    r1 = complete_json(_SYSTEM, user, model=settings.llm_tier1_model)
    if run_id is not None:
        audit.log_cost(run_id, npi, "llm_tier1", quantity=r1.prompt_tokens + r1.completion_tokens,
                       usd=r1.usd, detail=f"{r1.model}:{field}")
    val1 = str(r1.data.get("value", "")).strip()
    conf1 = float(r1.data.get("confidence", 0) or 0)

    agree = val1 and (val1 == deterministic_best or val1 in cands)
    if agree and conf1 >= settings.tier1_escalate_below:
        return val1, conf1, list(r1.data.get("supporting_sources", [])), "tier1"

    # Escalate to Tier 2 for the hard residual.
    r2 = complete_json(_SYSTEM, user, model=settings.llm_tier2_model)
    if run_id is not None:
        audit.log_cost(run_id, npi, "llm_tier2", quantity=r2.prompt_tokens + r2.completion_tokens,
                       usd=r2.usd, detail=f"{r2.model}:{field}")
    val2 = str(r2.data.get("value", "")).strip() or val1
    conf2 = float(r2.data.get("confidence", 0) or 0)
    return val2, conf2, list(r2.data.get("supporting_sources", [])), "tier2"


def reconcile_conflicts(record: ProviderRecord, rec: Recommendation,
                        evidence: list[Evidence], run_id: int | None = None) -> Recommendation:
    tier_used = None
    for ch in rec.changes:
        if not ch.conflicting_sources:
            continue
        cands = _candidates(ch.field, evidence)
        if len(cands) < 2:
            continue
        current = getattr(record, ch.field, None) or ""
        value, conf, sources, tier = _reconcile_field(
            ch.field, current, cands, run_id, record.npi
        )
        tier_used = tier
        disp = format_phone_display(value) if ch.field == "phone" else value
        ch.new_value = disp
        ch.confidence_score = round(conf, 4)
        ch.supporting_sources = sources or ch.supporting_sources
        ch.conflicting_sources = []  # resolved by the LLM
        # Re-evaluate auto-eligibility post-resolution.
        has_auth = any(s in AUTHORITATIVE_SOURCES for s in ch.supporting_sources)
        n_families = len({family_of(s) for s in ch.supporting_sources})
        ch.auto_eligible = (
            ch.field not in HIGH_RISK_FIELDS
            and conf >= settings.auto_update_threshold
            and (n_families >= 2 or has_auth)
        )

    rec.llm_tier_used = tier_used
    if rec.changes:
        rec.overall_confidence = round(mean(c.confidence_score for c in rec.changes), 4)
        if all(c.auto_eligible for c in rec.changes):
            rec.recommended_action = Action.auto_update
            rec.reason = "Conflicts resolved by LLM reconciliation with high confidence."
        else:
            rec.recommended_action = Action.human_review
            low = [c.field for c in rec.changes if not c.auto_eligible]
            rec.reason = f"LLM reconciliation left fields below auto threshold: {', '.join(low)}."
    return rec
