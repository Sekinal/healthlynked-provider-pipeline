"""Entity resolution / duplicate detection (free, deterministic + LLM tie-break).

Blocking keeps it cheap: only compare records that share a ZIP or phone, then
score names with rapidfuzz. Clear matches/non-matches resolve for free; only the
ambiguous band (fuzzy_low..fuzzy_high) is escalated to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

from pipeline.config import settings
from pipeline.normalize import normalize_address, normalize_name, normalize_phone
from pipeline.schemas import ProviderRecord


@dataclass
class MatchResult:
    a: str  # provider_id
    b: str
    score: float
    verdict: str  # "duplicate" | "not_duplicate" | "needs_llm"


def _block_key(r: ProviderRecord) -> tuple[str, str]:
    zip5 = ""
    addr = normalize_address(r.address)
    for tok in addr.split():
        if tok.isdigit() and len(tok) == 5:
            zip5 = tok
    return zip5, normalize_phone(r.phone)


def name_similarity(a: ProviderRecord, b: ProviderRecord) -> float:
    return fuzz.token_set_ratio(normalize_name(a.provider_name),
                                normalize_name(b.provider_name)) / 100.0


def find_duplicates(records: list[ProviderRecord]) -> list[MatchResult]:
    """Pairwise duplicate candidates within blocks. Different NPIs with the same
    person/practice signal a likely duplicate; same NPI twice is a hard dupe."""
    results: list[MatchResult] = []
    n = len(records)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = records[i], records[j]
            if a.npi and a.npi == b.npi:
                results.append(MatchResult(a.provider_id, b.provider_id, 1.0, "duplicate"))
                continue
            ka, kb = _block_key(a), _block_key(b)
            # require a shared block signal (zip or phone) to compare at all
            if not ((ka[0] and ka[0] == kb[0]) or (ka[1] and ka[1] == kb[1])):
                continue
            score = name_similarity(a, b)
            if score >= settings.fuzzy_high:
                verdict = "duplicate"
            elif score < settings.fuzzy_low:
                verdict = "not_duplicate"
            else:
                verdict = "needs_llm"
            results.append(MatchResult(a.provider_id, b.provider_id, round(score, 3), verdict))
    return results
