"""Pluggable Cloudflare solver backends.

Every backend's job reduces to the same thing: produce a fresh ``cf_clearance``
(plus the page HTML and the User-Agent the clearance is bound to) for a URL
behind a Cloudflare challenge. The caller (:class:`CloudflareBypass`) folds the
cookies into its existing per-domain cache so curl_cffi can ride them on the
next request, exactly as the native browser path already does.

Two families:

* **In-browser** (``native``, ``remote``) — run the full nodriver solve loop in
  :class:`CloudflareBypass`. ``remote`` only changes *where* Chrome runs (a
  real-GPU box) so it isn't a class here; it's a launch-path branch.
* **External HTTP** (``byparr``, ``flaresolverr``, ``browser_run``,
  ``firecrawl``) — hand the whole fetch to a service over HTTP and map its
  response onto :class:`SolveResult`. Those live in this package.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from miramedia.cloudflare.config import CloudflareConfig

    ProgressCb = Callable[[str], None]


@dataclass
class SolveResult:
    """What an external solver returns for one URL.

    ``html`` is the rendered page body (what the indexer parser consumes).
    ``cookies`` should carry ``cf_clearance``/``__cf_bm`` when the backend
    exposes them so they can be cached for curl_cffi replay; some backends only
    return HTML, in which case ``cookies`` is empty and every request goes back
    through the solver (correct, just not cached).
    """

    html: str | None = None
    cookies: dict[str, str] = field(default_factory=dict)
    user_agent: str = ""


@runtime_checkable
class CloudflareSolver(Protocol):
    """An external solver. ``solve`` must be safe to call from a worker thread
    (it's invoked off the request path) and must never raise — return ``None``
    on any failure so the caller can fall through / trip the breaker."""

    def solve(
        self, url: str, progress: ProgressCb | None = None
    ) -> SolveResult | None: ...


def get_solver(config: CloudflareConfig) -> CloudflareSolver:
    """Build the external solver named by ``config.solver``.

    Only valid for the external HTTP backends (``config.uses_external_solver``);
    ``native``/``remote`` are driven by :class:`CloudflareBypass` directly.
    """
    name = config.solver_name
    timeout = config.total_timeout_seconds
    if name in ("byparr", "flaresolverr"):
        from miramedia.cloudflare.solvers.proxy import FlareSolverrSolver

        sub = config.byparr if name == "byparr" else config.flaresolverr
        return FlareSolverrSolver(
            endpoint=sub.url,
            proxy=config.proxy,
            timeout=timeout,
            label=name,
        )
    if name == "browser_run":
        from miramedia.cloudflare.solvers.browser_run import BrowserRunSolver

        return BrowserRunSolver(config.browser_run, timeout=timeout)
    if name == "firecrawl":
        from miramedia.cloudflare.solvers.firecrawl import FirecrawlSolver

        return FirecrawlSolver(config.firecrawl, proxy=config.proxy, timeout=timeout)
    msg = f"unknown external Cloudflare solver: {name!r}"
    raise ValueError(msg)


__all__ = ["CloudflareSolver", "SolveResult", "get_solver"]
