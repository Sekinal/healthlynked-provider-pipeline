"""Web enrichment tier (Stage 3 of the funnel).

Only runs for fields the free sources miss (e.g. website) or to corroborate a
candidate change. Tiered for cost:
  - Tier A (cheap, default): HTTP-first with browser-identical TLS fingerprint
    (curl_cffi / httpcloak). Handles most practice sites.
  - Tier B (last resort): CloakBrowser stealth Chromium via Crawlee, only when
    Tier A is blocked. Enable with HL_ENABLE_CLOAKBROWSER=1.
Produces Evidence rows tagged source="Practice Website" (fresh -> recency 1.0).

Note: for sources with no clean API (e.g. state boards), the cheap path is to
capture a HAR once (Playwright/CloakBrowser), find the hidden JSON endpoint, then
hit it directly with curl_cffi — far cheaper than rendering every page.
"""

from __future__ import annotations

import os
import re

from curl_cffi import requests as creq

from pipeline import audit
from pipeline.config import weight_for
from pipeline.normalize import normalize_field
from pipeline.proxies import proxy_cycle
from pipeline.recency import recency_from_age
from pipeline.schemas import Evidence, ProviderRecord
from pipeline.sources.google_search import find_practice_website

SOURCE = "Practice Website"

_PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_ADDR_RE = re.compile(
    r"\d{1,6}\s+[\w.\- ]{2,40},\s*(?:(?:ste|suite|unit|#)[\w.\- ]{1,10},\s*)?"
    r"[A-Za-z.\- ]{2,30},\s*[A-Z]{2}\s*\d{5}",
    re.I,
)
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_STRIP_RE = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    html = _TAG_RE.sub(" ", html)
    return re.sub(r"\s+", " ", _STRIP_RE.sub(" ", html))


def _fetch_tier_a(url: str) -> tuple[str | None, int]:
    """HTTP-first fetch with Chrome TLS impersonation. Returns (html, bytes)."""
    proxies = proxy_cycle()
    for _ in range(2):
        proxy = next(proxies)
        kw = {"proxies": {"https": proxy, "http": proxy}} if proxy else {}
        try:
            resp = creq.get(url, impersonate="chrome", timeout=20,
                            allow_redirects=True, **kw)
            if resp.status_code == 200 and resp.text:
                return resp.text, len(resp.content)
        except Exception:
            continue
    return None, 0


def _fetch_tier_b(url: str) -> tuple[str | None, int]:
    """CloakBrowser stealth fetch via Crawlee (opt-in; downloads binary)."""
    if os.getenv("HL_ENABLE_CLOAKBROWSER") != "1":
        return None, 0
    try:
        import asyncio

        from pipeline.sources.crawlee_plugin import crawl_pages
        from pipeline.proxies import proxy_list

        pages = asyncio.run(crawl_pages([url], proxies=proxy_list(), max_requests=1))
        html = pages.get(url)
        return (html, len(html.encode())) if html else (None, 0)
    except Exception:
        return None, 0


def fetch_html(url: str) -> tuple[str | None, int]:
    html, nbytes = _fetch_tier_a(url)
    if html:
        return html, nbytes
    return _fetch_tier_b(url)


def _extract(html: str, base_host: str) -> dict[str, str]:
    text = _text(html)
    out: dict[str, str] = {"website": base_host}
    phones = _PHONE_RE.findall(text)
    if phones:
        # most frequent phone on the page is most likely the main line
        best = max(set(phones), key=phones.count)
        out["phone"] = best
    addr = _ADDR_RE.search(text)
    if addr:
        out["address"] = addr.group(0)
    return out


def enrich(record: ProviderRecord, existing: list[Evidence],
           run_id: int | None = None) -> list[Evidence]:
    """Discover/scrape the practice website and return corroborating Evidence."""
    website = record.website or find_practice_website(
        record.practice_name, record.address, record.provider_name
    )
    if not website:
        return []

    url = website if website.startswith("http") else f"https://{website}"
    html, nbytes = fetch_html(url)
    if run_id is not None and nbytes:
        # rough proxy/bandwidth cost; ~$8/GB residential as a conservative figure
        audit.log_cost(run_id, record.npi, "proxy_bytes", quantity=nbytes,
                       usd=round(nbytes / 1e9 * 8.0, 6), detail=url)
    if not html:
        return []

    from urllib.parse import urlparse

    host = urlparse(url).netloc.lstrip("www.")
    found = _extract(html, host)

    recency = recency_from_age(0)  # fresh scrape
    evidence: list[Evidence] = []
    for field, raw in found.items():
        norm = normalize_field(field, raw)
        if not norm:
            continue
        evidence.append(Evidence(
            field=field, value=norm, raw_value=raw, source=SOURCE,
            weight=weight_for(SOURCE, field), recency=recency,
        ))
    return evidence
