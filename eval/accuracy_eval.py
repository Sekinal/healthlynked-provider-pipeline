"""Honest accuracy evaluation on LIVE NPPES data.

We have no human-verified ground truth, so we measure two things we *can* verify
against the authoritative registry itself:

1. False-positive rate: seed records that exactly match current NPPES, run the
   pipeline, and expect `no_change`. Any detected change is a false positive.
2. Detection + value-correctness: corrupt one field (phone) to a known-wrong
   value, run the pipeline, and expect it to (a) detect the change and (b) propose
   the real NPPES value.

This isolates the deterministic + free-source core (no web/LLM), which is what
makes the auto/representation decisions. Usage:

    uv run python -m eval.accuracy_eval --n 30
"""

from __future__ import annotations

import argparse

import httpx

from pipeline.decide import build_recommendation
from pipeline.normalize import normalize_field
from pipeline.schemas import ProviderRecord
from pipeline.sources import nppes

CITIES = ["Naples", "Fort Myers", "Miami", "Orlando", "Tampa", "Jacksonville",
          "Sarasota", "Gainesville", "Tallahassee", "Cape Coral"]


def fetch_npis(n: int) -> list[str]:
    npis: list[str] = []
    for city in CITIES:
        if len(npis) >= n:
            break
        r = httpx.get(nppes.API_URL, params={
            "version": "2.1", "taxonomy_description": "Cardiovascular Disease",
            "state": "FL", "city": city, "limit": 20,
        }, timeout=30)
        for res in r.json().get("results", []):
            if res.get("basic", {}).get("status") == "A":
                npis.append(res["number"])
            if len(npis) >= n:
                break
    return npis[:n]


def current_values(npi: str) -> dict[str, str]:
    """Normalized current NPPES values, keyed by field."""
    return {e.field: e.value for e in nppes.extract_evidence(npi)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()

    npis = fetch_npis(args.n)
    print(f"Evaluating on {len(npis)} live NPIs\n")

    fp_total = fp_clean = 0          # false-positive test
    det_total = det_hit = val_hit = 0  # detection + value-correctness test

    for npi in npis:
        cur = current_values(npi)
        if "phone" not in cur:
            continue

        # --- Test 1: exact-match record should yield no_change ---
        # Seed EVERY field NPPES returns; omitting one would look like a change.
        rec_match = ProviderRecord(
            provider_id=f"EVAL_{npi}", npi=npi,
            **{f: cur.get(f) for f in ("provider_name", "specialty", "practice_name",
                                       "address", "phone", "website", "active_status")
               if f in cur},
        )
        out = build_recommendation(rec_match, nppes.extract_evidence(npi))
        fp_total += 1
        if not out.change_detected:
            fp_clean += 1

        # --- Test 2: corrupt the phone; expect detection + correct proposal ---
        rec_bad = rec_match.model_copy(update={"phone": "(111) 111-1111"})
        out2 = build_recommendation(rec_bad, nppes.extract_evidence(npi))
        det_total += 1
        phone_change = next((c for c in out2.changes if c.field == "phone"), None)
        if phone_change is not None:
            det_hit += 1
            if normalize_field("phone", phone_change.new_value) == cur["phone"]:
                val_hit += 1

    print("=== Test 1: false positives on already-correct records ===")
    print(f"  no_change correctly returned: {fp_clean}/{fp_total} "
          f"({fp_clean / fp_total * 100:.1f}%)  -> false-positive rate "
          f"{(fp_total - fp_clean) / fp_total * 100:.1f}%")
    print("\n=== Test 2: detection of an injected wrong phone ===")
    print(f"  change detected:            {det_hit}/{det_total} "
          f"({det_hit / det_total * 100:.1f}%)")
    print(f"  proposed the correct value: {val_hit}/{det_total} "
          f"({val_hit / det_total * 100:.1f}%)")


if __name__ == "__main__":
    main()
