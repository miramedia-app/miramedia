"""Firecrawl scrape-API solver.

Managed scrape service with stealth + proxy built in. Free tier is 500 lifetime
credits then rate-limited, so it's best as a fallback rather than a daily
driver. Returns rendered HTML only (no cookie jar), which is all the indexer
parser needs.

Docs: https://docs.firecrawl.dev/api-reference/endpoint/scrape
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from miramedia.cloudflare.solvers import SolveResult

if TYPE_CHECKING:
    from miramedia.cloudflare.config import FirecrawlConfig
    from miramedia.cloudflare.solvers import ProgressCb

log = logging.getLogger(__name__)


class FirecrawlSolver:
    """Firecrawl ``/v1/scrape`` adapter."""

    def __init__(
        self, config: FirecrawlConfig, proxy: str = "", timeout: float = 180.0
    ) -> None:
        self.api_key = config.api_key
        self.base_url = config.base_url.rstrip("/")
        self.proxy = proxy
        self.timeout = timeout

    def solve(self, url: str, progress: ProgressCb | None = None) -> SolveResult | None:
        if not self.api_key:
            log.error("firecrawl solver missing api_key")
            return None
        if progress is not None:
            try:
                progress("Scraping via Firecrawl…")
            except Exception:  # noqa: S110 — progress sink must never break the solve
                pass
        payload: dict[str, object] = {"url": url, "formats": ["rawHtml"]}
        # Firecrawl's "stealth" proxy mode is the one that beats Cloudflare.
        payload["proxy"] = "stealth" if not self.proxy else self.proxy
        try:
            resp = httpx.post(
                f"{self.base_url}/v1/scrape",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            log.exception("firecrawl request failed for %s", url)
            return None

        if not data.get("success", True):
            log.warning("firecrawl did not scrape %s: %s", url, data)
            return None
        body = data.get("data") or {}
        html = body.get("rawHtml") or body.get("html")
        return SolveResult(html=html)
