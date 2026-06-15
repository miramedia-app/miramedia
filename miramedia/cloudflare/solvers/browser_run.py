"""Cloudflare Browser Rendering ("Browser Run") solver.

Renders the page with real Chrome inside Cloudflare's own infra via the REST
``/content`` endpoint and returns the post-JS HTML. Free tier: 10 minutes of
browser time/day, 3 concurrent browsers.

The REST ``/content`` endpoint returns HTML only — no cookie jar — so
``SolveResult.cookies`` is empty and each fetch goes through the service. That's
fine: the indexer parser only needs the HTML. Egress is a Cloudflare IP, which
may be trusted or self-flagged depending on the target — test before relying on
it.

Docs: https://developers.cloudflare.com/browser-run/
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from miramedia.cloudflare.solvers import SolveResult

if TYPE_CHECKING:
    from miramedia.cloudflare.config import BrowserRunConfig
    from miramedia.cloudflare.solvers import ProgressCb

log = logging.getLogger(__name__)


class BrowserRunSolver:
    """Cloudflare Browser Rendering REST ``/content`` adapter."""

    def __init__(self, config: BrowserRunConfig, timeout: float = 180.0) -> None:
        self.account_id = config.account_id
        self.api_token = config.api_token
        self.timeout = timeout

    def solve(self, url: str, progress: ProgressCb | None = None) -> SolveResult | None:
        if not (self.account_id and self.api_token):
            log.error("browser_run solver missing account_id/api_token")
            return None
        endpoint = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/browser-rendering/content"
        )
        if progress is not None:
            try:
                progress("Rendering via Cloudflare Browser Rendering…")
            except Exception:  # noqa: S110 — progress sink must never break the solve
                pass
        try:
            resp = httpx.post(
                endpoint,
                headers={"Authorization": f"Bearer {self.api_token}"},
                json={"url": url},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            log.exception("browser_run request failed for %s", url)
            return None

        if not data.get("success"):
            log.warning("browser_run did not render %s: %s", url, data.get("errors"))
            return None
        result = data.get("result")
        html = result if isinstance(result, str) else None
        return SolveResult(html=html)
