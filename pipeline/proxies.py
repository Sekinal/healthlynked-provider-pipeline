"""Tiered proxy pool, read from the HL_PROXIES env var (comma-separated).

Tier 0 = no proxy (free), Tier 1 = whatever is configured (datacenter/residential).
Keeping this in one place lets every fetcher share the same rotation + cost model.
"""

from __future__ import annotations

import os
from itertools import cycle
from typing import Iterator, Optional


def proxy_list() -> list[str]:
    raw = os.getenv("HL_PROXIES", "").strip()
    return [p.strip() for p in raw.split(",") if p.strip()]


def proxy_cycle() -> Iterator[Optional[str]]:
    """Yield None first (free), then rotate configured proxies forever."""
    proxies = proxy_list()
    if not proxies:
        return cycle([None])
    return cycle([None, *proxies])
