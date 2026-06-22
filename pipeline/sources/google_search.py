"""Search-based discovery of a practice's official website.

Used only when a record has no website on file. We run a cheap HTTP search
(browser-identical TLS fingerprint via curl_cffi) behind the shared tiered-proxy
pool, then heuristically pick the most likely *official* domain. The default
engine is DuckDuckGo's HTML endpoint (scrape-friendly, free); the same call
swaps to Google + residential proxies at scale via `HL_PROXIES`.
"""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

from curl_cffi import requests as creq

from pipeline.proxies import proxy_cycle

SEARCH_URL = "https://html.duckduckgo.com/html/"

# Domains that are never the practice's own site.
_AGGREGATORS = {
    "healthgrades.com", "vitals.com", "webmd.com", "zocdoc.com", "yelp.com",
    "facebook.com", "linkedin.com", "doximity.com", "npidb.org", "wellness.com",
    "ratemds.com", "sharecare.com", "yellowpages.com", "mapquest.com",
    "google.com", "duckduckgo.com", "bing.com", "npino.com", "hipaaspace.com",
}

_HREF_RE = re.compile(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', re.I)


def _clean_ddg_url(href: str) -> str:
    """DuckDuckGo wraps results in a redirect (/l/?uddg=...)."""
    href = unescape(href)
    if "duckduckgo.com/l/" in href or href.startswith("//duckduckgo.com/l/"):
        q = parse_qs(urlparse(href).query)
        if "uddg" in q:
            return unquote(q["uddg"][0])
    return href


_STOPWORDS = {"the", "group", "associates", "medical", "center", "health",
              "care", "clinic", "inc", "llc", "pa", "and", "of", "for", "md"}


def _is_official(domain: str) -> bool:
    domain = domain.lower().lstrip("www.")
    return not any(domain == a or domain.endswith("." + a) for a in _AGGREGATORS)


def _name_tokens(*names: str | None) -> set[str]:
    toks: set[str] = set()
    for n in names:
        for t in re.findall(r"[a-z]{4,}", (n or "").lower()):
            if t not in _STOPWORDS:
                toks.add(t)
    return toks


def _domain_matches_name(domain: str, tokens: set[str]) -> bool:
    """Require a practice/provider name token in the domain -> precision over
    recall. Picking the wrong site is worse than finding none (avoids bad updates)."""
    if not tokens:
        return False
    d = domain.lower().replace("-", "")
    return any(t in d for t in tokens)


def find_practice_website(practice_name: str | None, address: str | None,
                          provider_name: str | None = None) -> str | None:
    query = " ".join(p for p in [practice_name or provider_name, address, "official site"] if p)
    if not query.strip():
        return None
    tokens = _name_tokens(practice_name, provider_name)

    proxies = proxy_cycle()
    for _ in range(2):  # tier 0 (no proxy) then a proxy if configured
        proxy = next(proxies)
        kw = {"proxies": {"https": proxy, "http": proxy}} if proxy else {}
        try:
            resp = creq.post(
                SEARCH_URL, data={"q": query}, impersonate="chrome", timeout=20, **kw
            )
            if resp.status_code != 200 or not resp.text:
                continue
            for href in _HREF_RE.findall(resp.text):
                url = _clean_ddg_url(href)
                host = urlparse(url).netloc.lstrip("www.")
                if host and _is_official(host) and _domain_matches_name(host, tokens):
                    return f"https://{host}"
            return None
        except Exception:
            continue
    return None
