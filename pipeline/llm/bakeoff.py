"""Model bake-off: pick the cheapest LLM that clears the accuracy bar.

Runs the hand-labeled conflict gold set (data/gold.jsonl) through each candidate
model and reports accuracy, latency, and cost. The winner becomes the Tier-1
default; GLM 5.2 is the Tier-2 escalation. This is the core cost lever.

    uv run python -m pipeline.llm.bakeoff
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from rapidfuzz import fuzz

from pipeline.llm.openrouter import MODEL_PRICES, complete_json
from pipeline.llm.reconcile import _SYSTEM, _prompt

CONTENDERS = [
    "deepseek/deepseek-v4-flash",
    "stepfun/step-3.7-flash",
    "google/gemini-3.1-flash-lite",
    "minimax/minimax-m3",
    "z-ai/glm-5.2",
]


def _load_gold(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _to_evidence_like(cands: list[dict]):
    # build the same prompt the pipeline uses, from gold candidate dicts
    class _E:
        pass

    grouped: dict[str, list] = {}
    for c in cands:
        e = _E()
        e.source, e.weight, e.recency, e.value = c["source"], c["weight"], c["recency"], c["value"]
        grouped.setdefault(c["value"], []).append(e)
    return grouped


def _match(got: str, expected: str) -> bool:
    return fuzz.ratio(str(got).strip().upper(), expected.strip().upper()) >= 90


def run_bakeoff(gold_path: str = "data/gold.jsonl", models: list[str] | None = None) -> list[dict]:
    gold = _load_gold(gold_path)
    models = models or CONTENDERS
    rows = []
    for model in models:
        correct = 0
        total_cost = 0.0
        total_latency = 0.0
        tok = 0
        for case in gold:
            cands = _to_evidence_like(case["candidates"])
            user = _prompt(case["field"], case["current"], cands)
            t0 = time.time()
            try:
                r = complete_json(_SYSTEM, user, model=model)
            except Exception as e:
                print(f"  [{model}] error: {e}")
                continue
            total_latency += time.time() - t0
            total_cost += r.usd
            tok += r.prompt_tokens + r.completion_tokens
            if _match(r.data.get("value", ""), case["expected_value"]):
                correct += 1
        n = len(gold)
        pin, pout = MODEL_PRICES.get(model, (0, 0))
        rows.append({
            "model": model,
            "accuracy": round(correct / n, 3) if n else 0,
            "avg_latency_s": round(total_latency / n, 2) if n else 0,
            "cost_usd": round(total_cost, 6),
            "cost_per_1k_conflicts": round(total_cost / n * 1000, 4) if n else 0,
            "price_in_out": f"${pin}/${pout}",
        })
    return rows


def main() -> None:
    rows = run_bakeoff()
    rows.sort(key=lambda r: (-r["accuracy"], r["cost_per_1k_conflicts"]))
    hdr = f"{'model':32} {'acc':>5} {'lat(s)':>7} {'cost/1k':>9} {'price(in/out)':>14}"
    print("\n" + hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['model']:32} {r['accuracy']:>5} {r['avg_latency_s']:>7} "
              f"{r['cost_per_1k_conflicts']:>9} {r['price_in_out']:>14}")
    print("\nWinner (cheapest at top accuracy):", rows[0]["model"])


if __name__ == "__main__":
    main()
