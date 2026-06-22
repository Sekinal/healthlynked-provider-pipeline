"""Pipeline orchestrator + CLI.

Runs the cost funnel for one or more provider records and prints a spec-shaped
recommendation. The vertical slice uses the free NPPES source; later stages
(web enrichment, LLM reconciliation) plug into `gather_evidence`.

Usage:
    uv run python -m pipeline.run --npi 1598742017
    uv run python -m pipeline.run --seed data/seed_npis.json
    uv run python -m pipeline.run --npi 1598742017 --enrich --reconcile
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from db.session import init_db
from pipeline import audit
from pipeline.decide import build_recommendation
from pipeline.schemas import Action, Evidence, ProviderRecord, Recommendation
from pipeline.sources import nppes


def gather_evidence(record: ProviderRecord, run_id: int | None,
                    enrich: bool = False) -> list[Evidence]:
    """Collect evidence stage-by-stage (funnel). NPPES first (free); optional
    web enrichment only for fields the free sources miss or that conflict."""
    evidence: list[Evidence] = []

    # Stage 1: free authoritative source.
    payload = nppes.fetch_npi(record.npi)
    snap_hash = nppes.content_hash(payload)
    if run_id is not None:
        audit.save_snapshot(record.npi, "NPI Registry", snap_hash, payload, nppes.API_URL)
        audit.log_cost(run_id, record.npi, "nppes_api", quantity=1, usd=0.0,
                       detail="free public API")
    evidence.extend(nppes.extract_evidence(record.npi, payload))

    # Stage 3: web enrichment (only when a tracked field is still unknown or
    # we want corroboration). Imported lazily so the slice runs without Crawlee.
    if enrich:
        from pipeline.sources import web_enrich

        evidence.extend(web_enrich.enrich(record, evidence, run_id=run_id))

    return evidence


def process_record(record: ProviderRecord, run_id: int | None,
                   enrich: bool = False, reconcile: bool = False) -> Recommendation:
    evidence = gather_evidence(record, run_id, enrich=enrich)
    rec = build_recommendation(record, evidence)

    # Stage 5/6: LLM reconciliation only fires on genuine conflicts.
    if reconcile and any(c.conflicting_sources for c in rec.changes):
        from pipeline.llm import reconcile as rec_mod

        rec = rec_mod.reconcile_conflicts(record, rec, evidence, run_id=run_id)

    if run_id is not None:
        audit.persist_recommendation(record, rec, run_id)
    return rec


def to_spec(rec: Recommendation) -> dict:
    """Emit the exact HealthLynked competition output shape."""
    return {
        "provider_id": rec.provider_id,
        "npi": rec.npi,
        "change_detected": rec.change_detected,
        "changes": [
            {
                "field": c.field,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "confidence_score": c.confidence_score,
                "supporting_sources": c.supporting_sources,
            }
            for c in rec.changes
        ],
        "overall_confidence": rec.overall_confidence,
        "recommended_action": rec.recommended_action.value,
        "reason": rec.reason,
    }


def _load_records(args) -> list[ProviderRecord]:
    if args.npi:
        return [ProviderRecord(provider_id=args.provider_id or f"HL_{args.npi}",
                               npi=args.npi)]
    if args.seed:
        data = json.loads(Path(args.seed).read_text())
        return [ProviderRecord(**r) for r in data]
    raise SystemExit("Provide --npi or --seed")


def main() -> None:
    ap = argparse.ArgumentParser(description="HealthLynked directory update pipeline")
    ap.add_argument("--npi", help="single NPI to check")
    ap.add_argument("--provider-id", help="provider_id for --npi mode")
    ap.add_argument("--seed", help="path to JSON list of provider records")
    ap.add_argument("--enrich", action="store_true", help="enable web enrichment tier")
    ap.add_argument("--reconcile", action="store_true", help="enable LLM conflict reconciliation")
    ap.add_argument("--no-persist", action="store_true", help="skip DB writes")
    args = ap.parse_args()

    init_db()
    records = _load_records(args)
    run_id = None if args.no_persist else audit.start_run(notes="cli run")

    counts = {Action.no_change: 0, Action.auto_update: 0, Action.human_review: 0}
    outputs = []
    for record in records:
        rec = process_record(record, run_id, enrich=args.enrich, reconcile=args.reconcile)
        counts[rec.recommended_action] += 1
        outputs.append(to_spec(rec))

    if run_id is not None:
        audit.finish_run(run_id, len(records), counts[Action.no_change],
                         counts[Action.auto_update], counts[Action.human_review])

    print(json.dumps(outputs if len(outputs) > 1 else outputs[0], indent=2))


if __name__ == "__main__":
    main()
