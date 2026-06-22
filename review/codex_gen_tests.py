"""Use codex-cli as an adversarial TEST AUTHOR.

Complements the adversarial code review (`review/codex_review.py`): instead of
critiquing the logic, codex writes hard, edge-case tests against the pipeline's
safety-critical public APIs. Generated into tests/test_hard_codex.py, then run
with pytest. A failing generated test is a signal — triage as a real bug vs. an
over-strict expectation.

    uv run python -m review.codex_gen_tests
"""

from __future__ import annotations

import subprocess
import sys

PROMPT = r"""You are an adversarial test author for a healthcare provider-directory
update pipeline. Write HARD, edge-case pytest tests that try to break the
safety-critical logic. Create exactly one file: tests/test_hard_codex.py
Do NOT modify any other file.

Public APIs to test (import these):
- pipeline.confidence.score_field(field, list[Evidence]) -> FieldScore|None
  FieldScore has: winning_value, field_confidence, supporting_sources,
  conflicting_sources, n_independent, n_families, winner_evidence, runner_up_evidence
- pipeline.decide.build_recommendation(ProviderRecord, list[Evidence]) -> Recommendation
  Recommendation has: recommended_action (Action.no_change/auto_update/human_review),
  changes (list of FieldChange with auto_eligible), review_flags, overall_confidence
- pipeline.normalize.normalize_phone/address/name/specialty/status/field
- pipeline.match.find_duplicates(list[ProviderRecord])
- pipeline.stage0_staleness.is_stale/staleness_score
- pipeline.schemas.Evidence(field,value,source,weight,recency), ProviderRecord, Action

Intended SAFETY semantics (your assertions must encode these as CORRECT behavior):
- High-risk fields (npi, provider_name, active_status) NEVER auto_update.
- A field auto-updates only if field_confidence >= 0.85 AND (>=2 independent
  source FAMILIES or an authoritative source: NPI Registry / State Medical Board /
  CMS Doctors & Clinicians). Correlated web sources (Practice Website, Google
  Business, Third-Party Directory) share one family.
- Two well-supported disagreeing values deflate confidence (conflict -> human_review).
- The existing record value is a possibly-stale baseline, never corroboration.
- Normalization is idempotent and collapses formatting differences.

Cover: empty/None inputs, unicode, extreme weights/recency (0 and 1), many
conflicting values, malformed phones/addresses, duplicate-source dedup, blocking
that prevents cross-record comparison. Tests MUST be deterministic and need NO
network. After writing, run `uv run pytest tests/test_hard_codex.py -q` and fix
ONLY your own test file until all pass (do not change pipeline code)."""


def main() -> int:
    try:
        proc = subprocess.run(
            ["codex", "exec", "--skip-git-repo-check",
             "--sandbox", "workspace-write", PROMPT],
            text=True, timeout=900,
        )
    except FileNotFoundError:
        print("codex-cli not found on PATH; skipping.", file=sys.stderr)
        return 0
    except subprocess.TimeoutExpired:
        print("codex test generation timed out.", file=sys.stderr)
        return 1
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
