"""Cloudflare-aware HTTP session.

Wraps ``curl_cffi.requests.Session`` (browser TLS fingerprint impersonation)
and transparently triggers ``CloudflareBypass`` when a request hits a
Cloudflare challenge response. Cached cookies + the bypass-harvested
User-Agent are reused for subsequent requests to the same domain.

Usage:

    session = CloudflareSession()
    response = session.get("https://example.com/api/foo")

The session is a drop-in replacement for the request-response surface used
by most providers (``get``, ``post``, ``request``); the response object is
``curl_cffi.requests.Response`` which mirrors the ``requests.Response`` API.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from curl_cffi import requests as cc_requests

from miramedia.cloudflare.singleton import get_cloudflare_bypass

if TYPE_CHECKING:
    from miramedia.cloudflare.bypass import CloudflareBypass

log = logging.getLogger(__name__)

# Subdomains that don't render the Turnstile widget (JSON/raw endpoints).
# When the bypass needs to navigate, strip these so we land on the HTML site
# where Cloudflare actually serves the challenge.
_NON_NAVIGABLE_SUBDOMAINS = ("api.", "cdn.", "static.", "media.")


def _navigable_url(url: str) -> str:
    """Return a URL likely to render an HTML Cloudflare challenge.

    For ``https://api.example.com/v1/foo`` returns ``https://example.com/``.
    For all other hosts returns ``https://<host>/`` (drops the request path).
    The cf_clearance cookie is typically scoped to ``.example.com`` and
    therefore covers ``api.example.com`` as well.
    """
    parsed = urlparse(url)
    host = parsed.netloc
    for prefix in _NON_NAVIGABLE_SUBDOMAINS:
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host}/"


def _cache_key(url: str) -> str:
    """Cache key used for cookie reuse — always the navigable host.

    Lowercased to match ``CloudflareBypass._domain_of``: solve() caches the
    cookie under the lowercased host, so a raw mixed-case netloc here would
    miss the cache and re-solve via the browser every request.
    """
    return urlparse(_navigable_url(url)).netloc.lower()


class CloudflareSession(cc_requests.Session):
    """``curl_cffi`` Session with on-demand Cloudflare-challenge solving."""

    def __init__(
        self,
        *,
        bypass: CloudflareBypass | None = None,
        impersonate: str | None = None,
        **kwargs: Any,  # noqa: ANN401 — passthrough to curl_cffi Session.__init__
    ) -> None:
        self._bypass = bypass or get_cloudflare_bypass()
        profile = impersonate or self._bypass.config.impersonate_profile
        kwargs.setdefault("impersonate", profile)
        # Route replay through the configured residential/mobile proxy so the
        # curl_cffi request shares the egress IP the cf_clearance was minted on
        # (sidecar/remote solvers using the same proxy → cookie actually rides).
        proxy = getattr(self._bypass.config, "proxy", "")
        if proxy:
            kwargs.setdefault("proxies", {"http": proxy, "https": proxy})
        super().__init__(**kwargs)
        self._applied_domain_ua: dict[str, str] = {}

    def _apply_cached_session(self, url: str) -> None:
        key = _cache_key(url)
        request_host = urlparse(url).netloc
        self._bypass.await_pending_solve(key)
        cached = self._bypass.get_cached_session(key)
        if not cached:
            return
        # Apply cookies under the request host AND the base (.example.com) so
        # curl_cffi sends them on both the api subdomain and the bare domain.
        for name, value in cached.cookies.items():
            self.cookies.set(name, value, domain=request_host)
            if request_host != key:
                self.cookies.set(name, value, domain=key)
        if cached.user_agent and self._applied_domain_ua.get(key) != cached.user_agent:
            self.headers["User-Agent"] = cached.user_agent
            self._applied_domain_ua[key] = cached.user_agent
            log.debug(
                "Applied cached CF session for %s (%d cookies, host=%s)",
                key,
                len(cached.cookies),
                request_host,
            )

    def request(
        self,
        method: str,
        url: str,
        *args: Any,  # noqa: ANN401 — passthrough to curl_cffi Session.request
        **kwargs: Any,  # noqa: ANN401 — passthrough to curl_cffi Session.request
    ) -> cc_requests.Response:  # type: ignore[override]
        # curl_cffi's only jobs here: (1) ride a cf_clearance cookie a previous
        # solve already cached for this domain, and (2) surface the challenge
        # response so the caller can detect it. It does NOT drive the solve —
        # the browser bypass owns that (base.py's solve() fallback solves,
        # returns the current page's content, AND caches the cookie for the next
        # search). Retrying curl_cffi inline after a solve was pure waste: a
        # freshly-minted cookie is consumed by the *next* request via
        # _apply_cached_session, not this one.
        self._apply_cached_session(url)
        return super().request(method, url, *args, **kwargs)
