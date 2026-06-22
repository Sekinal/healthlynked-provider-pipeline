"""Run the real reconciliation engine over the gold conflicts, logging actual
OpenRouter spend to the cost ledger. Honest real cost — used to give the cost
dashboard real LLM line items for a demo. Needs OPENROUTER_API_KEY.

    uv run python -m eval.populate_cost_demo
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline import audit
from pipeline.llm.reconcile import reconcile_conflicts
from pipeline.schemas import Evidence, FieldChange, ProviderRecord, Recommendation


def main() -> None:
    gold = [json.loads(l) for l in Path("data/gold.jsonl").read_text().splitlines() if l.strip()]
    run_id = audit.start_run(notes="cost demo — gold conflict reconciliation")
    total = 0
    for i, case in enumerate(gold):
        field = case["field"]
        ev = [Evidence(field=field, value=c["value"], source=c["source"],
                       weight=c["weight"], recency=c["recency"]) for c in case["candidates"]]
        sources = sorted({c["source"] for c in case["candidates"]})
        rec = Recommendation(
            provider_id=f"COST_{i}", npi=f"cost{i}", change_detected=True,
            changes=[FieldChange(field=field, old_value=case["current"],
                                 new_value=case["candidates"][0]["value"],
                                 confidence_score=0.2, supporting_sources=sources[:1],
                                 conflicting_sources=sources[1:])])
        record = ProviderRecord(provider_id=f"COST_{i}", npi=f"cost{i}",
                                **{field: case["current"]} if field in
                                {"address", "phone", "specialty", "provider_name",
                                 "practice_name", "website", "active_status"} else {})
        reconcile_conflicts(record, rec, ev, run_id=run_id)
        total += 1
    # Do NOT inflate record/outcome counts — this run only logs LLM cost.
    audit.finish_run(run_id, 0, 0, 0, 0)
    print(f"logged real reconciliation cost for {total} conflicts under run {run_id}")


if __name__ == "__main__":
    main()
