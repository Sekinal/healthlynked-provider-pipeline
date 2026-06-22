"""Shared recency-factor helper used by every source.

r_s = floor + (1 - floor) * exp(-age_days / tau), clamped to [floor, 1].
The floor prevents old-but-authoritative timestamps from collapsing evidence
weight to zero, while fresher data still scores higher.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional

from pipeline.config import settings

_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y", "%Y/%m/%d")


def recency_from_age(age_days: float) -> float:
    floor = settings.recency_floor
    decay = math.exp(-max(age_days, 0) / settings.recency_tau_days)
    return floor + (1 - floor) * decay


def recency_from_date(value: Optional[str], default: float = 0.85) -> float:
    """Parse a date string and return its recency factor. `default` is used when
    the date is missing/unparseable (treated as moderately fresh)."""
    if not value:
        return default
    for fmt in _FORMATS:
        try:
            dt = datetime.strptime(value[:19], fmt).replace(tzinfo=timezone.utc)
            break
        except ValueError:
            continue
    else:
        return default
    age_days = (datetime.now(timezone.utc) - dt).days
    return recency_from_age(age_days)
