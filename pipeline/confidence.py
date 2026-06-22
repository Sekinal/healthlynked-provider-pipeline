"""Source-weighted confidence scoring (noisy-OR with conflict deflation).

For each field we gather candidate values, each supported by one or more
independent sources. Evidence for a value combines via noisy-OR:

    evidence(v) = 1 - prod_{s supports v} (1 - w_s * r_s)

The winner is the value with the most evidence. Its confidence is deflated by
the runner-up's evidence so that genuine conflicts (two well-supported values)
collapse toward zero and get routed to human review:

    field_conf = evidence(winner) * (1 - evidence(runner_up))
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field

from pipeline.config import family_of
from pipeline.schemas import Evidence


@dataclass
class FieldScore:
    field: str
    winning_value: str
    field_confidence: float
    supporting_sources: list[str] = dc_field(default_factory=list)
    conflicting_sources: list[str] = dc_field(default_factory=list)
    runner_up_value: str | None = None
    n_independent: int = 0  # distinct sources backing the winner
    n_families: int = 0  # distinct *known* independent families backing the winner
    winner_evidence: float = 0.0
    runner_up_evidence: float = 0.0
    dissent_evidence: float = 0.0  # aggregate evidence for all non-winner values


def _noisy_or(items: list[float]) -> float:
    prod = 1.0
    for x in items:
        prod *= (1.0 - max(0.0, min(1.0, x)))
    return 1.0 - prod


def score_field(field: str, evidence: list[Evidence]) -> FieldScore | None:
    """Score a single field from its evidence rows. Returns None if no evidence."""
    if not evidence:
        return None

    # Group evidence by normalized value.
    by_value: dict[str, list[Evidence]] = {}
    for e in evidence:
        if not e.value:
            continue
        by_value.setdefault(e.value, []).append(e)
    if not by_value:
        return None

    # evidence(v) per candidate value. Noisy-OR assumes INDEPENDENCE, so we first
    # collapse correlated sources: within a family, take the single strongest
    # observation, then noisy-OR across families. This stops three mirrored web
    # sources from inflating confidence as if they were independent corroboration.
    scored: list[tuple[str, float, set[str], set[str]]] = []
    for value, evs in by_value.items():
        per_family: dict[str, float] = {}
        sources: set[str] = set()
        for e in evs:
            fam = family_of(e.source)
            per_family[fam] = max(per_family.get(fam, 0.0), e.weight * e.recency)
            sources.add(e.source)
        ev_value = _noisy_or(list(per_family.values()))
        known_families = {f for f in per_family if f != "unknown"}
        scored.append((value, ev_value, sources, known_families))

    scored.sort(key=lambda t: t[1], reverse=True)
    winner_value, winner_ev, winner_sources, winner_families = scored[0]
    runner_value, runner_ev = (scored[1][0], scored[1][1]) if len(scored) > 1 else (None, 0.0)

    # Deflate by *aggregate dissent* (noisy-OR of every losing value's evidence),
    # not just the single runner-up — fragmented conflict still erodes confidence.
    dissent = _noisy_or([ev for _, ev, _, _ in scored[1:]])
    field_conf = winner_ev * (1.0 - dissent)

    conflicting = sorted({s for _, _, ss, _ in scored[1:] for s in ss})
    return FieldScore(
        field=field,
        winning_value=winner_value,
        field_confidence=round(field_conf, 4),
        supporting_sources=sorted(winner_sources),
        conflicting_sources=conflicting,
        runner_up_value=runner_value,
        n_independent=len(winner_sources),
        n_families=len(winner_families),  # known families only -> "unknown" can't grant independence
        winner_evidence=round(winner_ev, 4),
        runner_up_evidence=round(runner_ev, 4),
        dissent_evidence=round(dissent, 4),
    )
