"""FlareSolverr / Byparr sidecar solver.

Both speak the same HTTP API: ``POST {endpoint}/v1`` with
``{"cmd": "request.get", "url": ..., "maxTimeout": ms}`` and reply with a
``solution`` object carrying the page HTML, cookies, and the User-Agent the
challenge was solved under. Byparr (Camoufox-based) is the
higher-success-rate, actively-maintained successor; FlareSolverr
(undetected-chromedriver) is the classic. One adapter covers both.

Run the sidecar on a box with a real GPU for a real WebGL fingerprint, and set
``[cloudflare].proxy`` to give it a residential egress IP.

* https://github.com/ThePhaseless/Byparr
* https://github.com/FlareSolverr/FlareSolverr
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from miramedia.cloudflare.solvers import SolveResult

if TYPE_CHECKING:
    from miramedia.cloudflare.solvers import ProgressCb

log = logging.getLogger(__name__)


def _emit(progress: ProgressCb | None, message: str) -> None:
    if progress is None:
        return
    try:
        progress(message)
    except Exception:  # noqa: S110 — progress sink must never break the solve
        pass


class FlareSolverrSolver:
    """Adapter for FlareSolverr- and Byparr-compatible sidecars."""

    def __init__(
        self,
        endpoint: str,
        proxy: str = "",
        timeout: float = 180.0,
        label: str = "flaresolverr",
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.proxy = proxy
        # The solver does its own internal timeout; give the HTTP call headroom
        # over it so we read the result rather than cutting the connection.
        self.timeout = timeout
        self.label = label

    def solve(self, url: str, progress: ProgressCb | None = None) -> SolveResult | None:
        payload: dict[str, object] = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": int(self.timeout * 1000),
        }
        # FlareSolverr/Byparr accept a per-request proxy for their OWN egress.
        if self.proxy:
            payload["proxy"] = {"url": self.proxy}

        _emit(progress, f"Handing off to {self.label} sidecar…")
        try:
            resp = httpx.post(
                f"{self.endpoint}/v1",
                json=payload,
                timeout=self.timeout + 30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            log.exception("%s request failed for %s", self.label, url)
            _emit(progress, f"{self.label} sidecar request failed")
            return None

        if str(data.get("status", "")).lower() != "ok":
            log.warning(
                "%s did not solve %s: %s",
                self.label,
                url,
                data.get("message") or data,
            )
            _emit(progress, f"{self.label} could not solve the challenge")
            return None

        solution = data.get("solution") or {}
        cookies = {
            c["name"]: c["value"]
            for c in solution.get("cookies", [])
            if isinstance(c, dict) and c.get("name")
        }
        _emit(progress, f"{self.label} cleared the challenge")
        return SolveResult(
            html=solution.get("response"),
            cookies=cookies,
            user_agent=solution.get("userAgent", ""),
        )
