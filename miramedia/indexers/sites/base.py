from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING, ClassVar, TypeVar, cast
from urllib.parse import quote, urlencode, urlparse, urlunparse

import httpx

from miramedia.indexers.http_retry import indexer_fanout_deadline, indexer_get
from miramedia.indexers.mirrors import MirrorPreference
from miramedia.indexers.schemas import IndexerQueryResult

_T = TypeVar("_T")

if TYPE_CHECKING:
    from miramedia.cloudflare import CloudflareBypass
    from miramedia.movies.schemas import Movie
    from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


@lru_cache(maxsize=1)
def _get_http_client() -> httpx.Client:
    """Module-level singleton httpx.Client.

    Reuses TCP connections via HTTP/1.1 keep-alive and is safely shared
    across threads (httpx.Client is thread-safe for ``send()``).

    HTTP/2 is NOT enabled: the ``h2`` package isn't a hard dep and lazy
    imports inside httpcore mean an ``http2=True`` flag here breaks every
    request with ``ModuleNotFoundError`` at first send time. HTTP/1.1
    keep-alive is enough for indexer search volume.

    Binds the local socket to ``0.0.0.0`` (AF_INET) so all outbound
    connections use IPv4. Without this, containers that have no IPv6
    interface (Synology default) but receive an AAAA record from DNS
    crash with ``[Errno 99] Cannot assign requested address`` because the
    kernel refuses to bind an AF_INET6 source. The behaviour shows up
    most often on indexer hosts like limetorrents.info / tracker hosts
    that publish v6 records.
    """
    timeout = httpx.Timeout(30.0, connect=10.0)
    headers = {"User-Agent": _DEFAULT_USER_AGENT}
    transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=1)  # noqa: S104 — intentional IPv4 source bind, see docstring
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers=headers,
        transport=transport,
    )


def close_http_client() -> None:
    """Close the cached httpx.Client. Call from app lifespan finally.

    Idempotent: safe to invoke even if the client was never constructed,
    and never raises so it can sit in a shutdown ``finally`` block.
    """
    try:
        info = _get_http_client.cache_info()
        if info.currsize > 0:
            _get_http_client().close()
            _get_http_client.cache_clear()
    except Exception:  # noqa: S110 — best-effort, non-fatal shutdown cleanup
        pass


# Well-known torrent tracker announce URLs for magnet link construction.
# UDP endpoints cover the common case; HTTP(S) mirrors help on VPNs and
# networks where UDP is blocked. HTTP(S) entries were reviewed 2026-08-09
# (dummy-hash announce probes); Torlink candidates were not copied blindly.
DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://tracker.bittor.pw:1337/announce",
    "udp://public.popcorn-tracker.org:6969/announce",
    "udp://tracker.dler.org:6969/announce",
    "udp://exodus.desync.com:6969",
    "udp://open.demonii.com:1337/announce",
    # HTTP(S) — reviewed healthy announces; see plan 324 maintenance notes.
    "http://tracker.opentrackr.org:1337/announce",
    "http://tracker.dler.org:6969/announce",
    "https://tracker.bt4g.com:443/announce",
    "https://tracker.leechshield.link:443/announce",
]

_BTIH_RE = re.compile(r"^(?:[0-9A-Fa-f]{40}|[A-Za-z2-7]{32})$")


def build_magnet(info_hash: str, name: str) -> str:
    """Build a magnet URI from an info hash and display name.

    Raises ``ValueError`` on a malformed info hash — scraped rows with a
    broken hash must be dropped by the caller's existing per-row error
    handling rather than produce a magnet the client can't use.
    """
    cleaned = info_hash.strip()
    if not _BTIH_RE.match(cleaned):
        msg = f"Invalid btih info hash (len={len(cleaned)})"
        raise ValueError(msg)
    trackers = "&".join(f"tr={quote(t, safe='')}" for t in DEFAULT_TRACKERS)
    return f"magnet:?xt=urn:btih:{cleaned}&dn={quote(name, safe='')}&{trackers}"


class BaseSite(ABC):
    """Base class for all native indexer site definitions."""

    name: str
    url: str
    supports_tv: bool = True
    supports_movies: bool = True
    cloudflare_protected: bool = False
    # Whether this site is enabled when first seeded into the DB. Override to
    # False for sites that are unreliable by default (e.g. Cloudflare-walled).
    # Only applies on first seed — user changes are never overwritten.
    default_enabled: bool = True
    # Path appended to ``url`` for connectivity tests. Defaults to the root.
    # Override when the root isn't representative — e.g. a JSON API whose root
    # 403s behind Cloudflare while its data endpoint serves fine, so probing
    # the root would falsely flag the site as Cloudflare-walled.
    test_path: str = ""
    # Seed-only fallback mirror origins. This class list SEEDS the DB row on
    # first load; the live mirror list is ``self.mirror_urls`` (set from the DB
    # column the UI manages — see ``__init__`` / the native backend). Populate
    # per-site from the tracker's OWN first-party domains only — never
    # third-party unblock proxies (proxyninja / mrunblock / etc.), which inject
    # ads and don't serve the JSON/HTML endpoints these scrapers hit.
    available_urls: ClassVar[list[str]] = []
    # Lazily-built, instance-local last-known-good ordering. Shadowed per
    # instance on first use; the class-level None is only the default.
    _mirror_pref: MirrorPreference | None = None

    def _mirror_list(self) -> tuple[str, ...]:
        """De-duplicated mirror origins, ``url`` first, trailing slash stripped."""
        seen: set[str] = set()
        mirrors: list[str] = []
        for candidate in (self.url, *self.mirror_urls):
            normalized = candidate.rstrip("/")
            if normalized and normalized not in seen:
                seen.add(normalized)
                mirrors.append(normalized)
        return tuple(mirrors)

    def _get_mirror_pref(self) -> MirrorPreference:
        if self._mirror_pref is None:
            self._mirror_pref = MirrorPreference(self._mirror_list())
        return self._mirror_pref

    def _fetch_over_mirrors(
        self,
        path: str,
        *,
        params: dict | None = None,
        fetch: Callable[..., _T] | None = None,
    ) -> _T:
        """Fetch ``path`` from each mirror in preference order until one works.

        ``path`` is appended to each mirror origin. The first mirror that
        returns without raising wins and is promoted for future searches, so a
        single dead primary no longer takes the indexer offline. The last
        error is re-raised only when every mirror fails; callers keep their
        existing try/except to turn that into an empty result list.
        """
        fetcher = fetch if fetch is not None else cast("Callable[..., _T]", self._fetch)
        pref = self._get_mirror_pref()
        last_exc: Exception | None = None
        for origin in pref.ordered():
            try:
                payload = fetcher(f"{origin}{path}", params=params)
            except Exception as exc:  # record error and try the next mirror
                last_exc = exc
                log.debug("%s: mirror %s failed: %s", self.name, origin, exc)
                continue
            pref.mark_success(origin)
            return payload
        if last_exc is not None:
            raise last_exc
        msg = f"{self.name}: no mirrors configured"
        raise RuntimeError(msg)

    def __init__(
        self,
        bypass: CloudflareBypass | None = None,
        timeout: int | None = None,
    ) -> None:
        # ``self.timeout`` governs ONLY the non-CF httpx fast path. CF-
        # protected sites route through the chromium bypass, whose timing is
        # owned entirely by the Cloudflare settings (browser launch / page
        # load / challenge wait / solve), so ``_fetch`` passes no timeout for
        # them and lets ``solve`` read its budget from config.
        self.bypass = bypass
        self.timeout = timeout if timeout is not None else 120
        # Per-instance override of the live failover mirror list. The native
        # backend sets this from the DB column the UI edits; when unset the
        # list falls back to the class seed ``available_urls``.
        self._mirror_urls_override: list[str] | None = None

    @property
    def mirror_urls(self) -> list[str]:
        """Live failover mirror list — DB override if set, else the class seed."""
        if self._mirror_urls_override is not None:
            return self._mirror_urls_override
        return list(self.available_urls)

    @mirror_urls.setter
    def mirror_urls(self, value: list[str]) -> None:
        self._mirror_urls_override = list(value)

    def _fetch(self, url: str, params: dict | None = None) -> str:
        """
        Fetch a URL, handling Cloudflare bypass when configured.
        Returns the response body text.

        For Cloudflare-protected sites we route through
        ``CloudflareSession`` which uses curl_cffi to impersonate a real
        Chrome TLS+HTTP/2 fingerprint. Without that, modern CF rejects the
        request even when we replay the correct ``cf_clearance`` cookie —
        cookies alone aren't enough, the JA3/JA4 fingerprint has to match.
        """
        if self.cloudflare_protected:
            full_url = url
            if params:
                full_url = url + ("&" if "?" in url else "?") + urlencode(params)

            # A Cloudflare-protected fetch needs a valid cf_clearance cookie.
            #
            # STEP 1 — curl_cffi via CloudflareSession: rides a cached
            # cf_clearance cookie when one exists (no chromium) and returns the
            # page directly. On a fresh challenge (no cookie yet, or expired) it
            # returns None — it only DETECTS the challenge, it doesn't solve.
            html = self._fetch_via_cloudflare_session(full_url)
            if html is not None:
                return html

            # STEP 2 — browser solve: no usable cookie, so drive the bypass
            # browser. It clears the challenge, returns the page HTML directly,
            # and caches cf_clearance so the NEXT search rides it via curl_cffi
            # (step 1) and skips chromium.
            if self.bypass:
                # No timeout passed — the bypass uses the Cloudflare config
                # (solve_timeout for the whole op, page_load for navigation).
                html = self.bypass.solve(full_url)
                if html:
                    return html

            parsed = urlparse(full_url)
            safe_url = urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
            )
            msg = (
                f"Cloudflare fetch returned no usable HTML for {safe_url} "
                "(curl_cffi challenged and bypass browser empty)"
            )
            raise RuntimeError(msg)

        client = _get_http_client()
        response = indexer_get(
            client,
            url,
            params=params,
            timeout=self.timeout,
            deadline=indexer_fanout_deadline(),
        )
        response.raise_for_status()
        return response.text

    def _fetch_via_cloudflare_session(self, url: str) -> str | None:
        """Fetch ``url`` through ``CloudflareSession`` (curl_cffi).

        Reuses any cached cf_clearance cookie + harvested User-Agent for the
        domain. It does NOT solve — on a fresh challenge it returns ``None`` so
        the caller drives the browser solve. Also returns ``None`` if the
        request errored or returned an HTTP error, so the caller never passes a
        challenge/error page to the parser.
        """
        try:
            from miramedia.cloudflare.bypass import is_cloudflare_challenge
            from miramedia.cloudflare.session import CloudflareSession
        except Exception:
            log.debug(
                "CloudflareSession unavailable for curl_cffi fallback", exc_info=True
            )
            return None

        session = getattr(self, "_cf_session", None)
        if session is None:
            try:
                session = CloudflareSession(bypass=self.bypass)
            except Exception:
                log.debug("Failed to build CloudflareSession", exc_info=True)
                return None
            self._cf_session = session  # type: ignore[attr-defined]

        # Send browser-navigation headers. Some sites (1337x) return their
        # generic "Error something went wrong" page for a bare GET to
        # /search/ even with a valid cf_clearance cookie — they expect the
        # request to look like a same-origin navigation. curl_cffi's
        # impersonate profile already sets UA + sec-ch-ua; we add the Referer
        # and Sec-Fetch-* set a real document navigation carries.
        from urllib.parse import urlparse

        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}/"
        headers = {
            "Referer": origin,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

        try:
            response = session.get(url, headers=headers, timeout=self.timeout)
        except Exception as exc:
            log.warning("curl_cffi request failed for %s: %s", url, exc)
            return None

        if is_cloudflare_challenge(response):
            # First request to this domain (or the cached cookie expired): it's
            # behind a Cloudflare challenge. Hand off to the browser solve.
            log.info("Cloudflare challenge on %s; handing off to browser solve", url)
            return None
        if response.status_code >= 400:
            log.info(
                "curl_cffi got HTTP %s for %s",
                response.status_code,
                url,
            )
            return None
        # Success without a challenge: fold the (possibly rotated) CF cookies
        # from this response back into the shared cache so the next request —
        # even from a cold session after a restart — keeps riding them and
        # skips the browser solve / Turnstile.
        if self.bypass is not None:
            try:
                self.bypass.refresh_cache_from_cookies(
                    urlparse(url).netloc,
                    dict(session.cookies.items()),
                    session.headers.get("User-Agent"),
                )
            except Exception:
                log.debug(
                    "CF cache refresh from curl_cffi response failed", exc_info=True
                )
        return response.text

    def _fetch_json(self, url: str, params: dict | None = None) -> dict | list:
        """Fetch a URL and parse the JSON response."""
        client = _get_http_client()
        response = indexer_get(
            client,
            url,
            params=params,
            timeout=self.timeout,
            deadline=indexer_fanout_deadline(),
        )
        response.raise_for_status()
        return response.json()

    @abstractmethod
    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        """Generic keyword search."""

    @abstractmethod
    def search_show(
        self, query: str, show: Show, season_number: int
    ) -> list[IndexerQueryResult]:
        """Search for TV content."""

    @abstractmethod
    def search_movie(self, query: str, movie: Movie) -> list[IndexerQueryResult]:
        """Search for movie content."""
