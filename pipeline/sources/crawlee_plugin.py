"""Crawlee orchestration with the CloakBrowser stealth plugin (tier B).

This is the batch-crawl path: Crawlee owns the request queue, retries, and
tiered proxy rotation, while CloakBrowser's patched Chromium handles bot
detection. Used only when the cheap HTTP-first tier (curl_cffi/httpcloak) is
blocked. Pattern follows the official Crawlee `PlaywrightBrowserPlugin` override.
"""

from __future__ import annotations

from typing import Iterable, Optional

from cloakbrowser.config import IGNORE_DEFAULT_ARGS, get_default_stealth_args
from cloakbrowser.download import ensure_binary

from crawlee.browsers import (
    BrowserPool,
    PlaywrightBrowserController,
    PlaywrightBrowserPlugin,
)
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext
from crawlee.proxy_configuration import ProxyConfiguration


class CloakBrowserPlugin(PlaywrightBrowserPlugin):
    """PlaywrightCrawler plugin that launches CloakBrowser's patched Chromium."""

    async def new_browser(self) -> PlaywrightBrowserController:
        if not self._playwright:
            raise RuntimeError("Playwright browser plugin is not initialized.")
        binary_path = ensure_binary()
        stealth_args = get_default_stealth_args()

        launch_options = dict(self._browser_launch_options)
        launch_options.pop("executable_path", None)
        launch_options.pop("chromium_sandbox", None)
        launch_options["args"] = [*launch_options.pop("args", []), *stealth_args]

        return PlaywrightBrowserController(
            browser=await self._playwright.chromium.launch(
                executable_path=binary_path,
                ignore_default_args=IGNORE_DEFAULT_ARGS,
                **launch_options,
            ),
            max_open_pages_per_browser=1,
            header_generator=None,  # CloakBrowser handles fingerprints at binary level
        )


def make_proxy_configuration(proxies: Optional[list[str]]) -> Optional[ProxyConfiguration]:
    """Tiered proxies: try no-proxy, then cheap datacenter, then residential."""
    if not proxies:
        return None
    return ProxyConfiguration(tiered_proxy_urls=[[None], proxies])


async def crawl_pages(urls: Iterable[str], proxies: Optional[list[str]] = None,
                      max_requests: int = 20) -> dict[str, str]:
    """Fetch a batch of URLs with the stealth browser; return {url: html}."""
    results: dict[str, str] = {}
    crawler = PlaywrightCrawler(
        max_requests_per_crawl=max_requests,
        browser_pool=BrowserPool(plugins=[CloakBrowserPlugin()]),
        proxy_configuration=make_proxy_configuration(proxies),
    )

    @crawler.router.default_handler
    async def handler(context: PlaywrightCrawlingContext) -> None:
        results[context.request.url] = await context.page.content()

    await crawler.run(list(urls))
    return results
