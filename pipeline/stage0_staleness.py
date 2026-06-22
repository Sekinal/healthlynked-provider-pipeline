"""Stage 0 — staleness / risk scoring (free, deterministic).

The first funnel filter: only records that look stale or risky enter the costly
stages. This is the single biggest volume (and therefore cost) lever.
"""

from __future__ import annotations

from datetime import date, datetime

from pipeline.config import settings
from pipeline.schemas import ProviderRecord

# Relative volatility of each field — higher means re-check sooner.
FIELD_VOLATILITY = {
    "phone": 1.0,
    "address": 1.0,
    "practice_name": 0.8,
    "website": 0.6,
    "active_status": 0.7,
    "specialty": 0.3,
    "provider_name": 0.1,
}


def _days_since(d) -> int:
    if not d:
        return 10_000  # never verified -> very stale
    if isinstance(d, date):
        dt = d
    else:
        try:
            dt = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
        except ValueError:
            return 10_000
    return (date.today() - dt).days


def staleness_score(record: ProviderRecord) -> float:
    """0..1; higher = more in need of a refresh."""
    age = _days_since(record.last_verified_date)
    age_ratio = min(age / max(settings.stale_after_days, 1), 2.0) / 2.0  # cap at 1.0
    # missing high-volatility fields add risk
    missing = sum(
        FIELD_VOLATILITY.get(f, 0.2)
        for f in ("phone", "address", "website")
        if not getattr(record, f, None)
    ) / sum(FIELD_VOLATILITY[f] for f in ("phone", "address", "website"))
    return round(min(0.7 * age_ratio + 0.3 * missing, 1.0), 3)


def is_stale(record: ProviderRecord) -> bool:
    return _days_since(record.last_verified_date) >= settings.stale_after_days or \
        staleness_score(record) >= 0.5
