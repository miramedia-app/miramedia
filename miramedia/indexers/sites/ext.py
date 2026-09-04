"""EXT Torrents native indexer with first-party mirror failover."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import UTC, date, datetime
from typing import Any, ClassVar, TypedDict, cast
from urllib.parse import urlencode, urlparse

import httpx
from selectolax.parser import HTMLParser, Node

from miramedia.cloudflare.session import CloudflareSession
from miramedia.indexers.mirrors import is_allowed_mirror_origin
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import _DEFAULT_USER_AGENT, BaseSite
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)

# Same sentinel 1337x uses: unknown ages must not earn a recency bonus.
UNKNOWN_AGE_DAYS = 36_500

_WHITESPACE_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,:;!?])")
_RELATIVE_AGE_RE = re.compile(
    r"(?:an?\s+)?(\d+)?\s*(minute|hour|day|week|month|year)s?",
    re.IGNORECASE,
)
_RELATIVE_AGE_DAYS = {
    "minute": 1 / (24 * 60),
    "hour": 1 / 24,
    "day": 1.0,
    "week": 7.0,
    "month": 30.0,
    "year": 365.0,
}


class _ExtRow(TypedDict):
    torrent_id: int
    title: str
    seeders: int
    size: int
    age: int


def _now() -> datetime:
    return datetime.now(UTC)


def parse_ext_title(title_link: Node) -> str:
    """Restore spaces EXT's query-highlight ``<span>`` strips from titles."""
    title = _WHITESPACE_RE.sub(" ", title_link.text(separator=" ", strip=True)).strip()
    return _SPACE_BEFORE_PUNCT_RE.sub(r"\1", title)


def parse_ext_age_days(
    age_span: Node | None,
    *,
    now: datetime | None = None,
) -> int:
    """Age in days from an EXT ``Age`` cell.

    Prefers the absolute ``title`` date (``15 January 2022``) and falls
    back to relative phrases (``8 months ago``). Missing/unparseable values
    return ``UNKNOWN_AGE_DAYS`` so they do not score as brand-new.
    """
    if age_span is None:
        return UNKNOWN_AGE_DAYS
    absolute = (age_span.attributes.get("title") or "").strip()
    parsed = _parse_absolute_date(absolute)
    if parsed is not None:
        reference = (now or _now()).date()
        return max(0, (reference - parsed).days)
    return _parse_relative_age(age_span.text(strip=True))


def _parse_absolute_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def _parse_relative_age(value: str) -> int:
    lowered = value.strip().lower()
    if lowered in {"just now", "today", "now"}:
        return 0
    match = _RELATIVE_AGE_RE.search(lowered)
    if not match:
        return UNKNOWN_AGE_DAYS
    count = int(match.group(1) or 1)
    unit = match.group(2).lower()
    return int(count * _RELATIVE_AGE_DAYS[unit])


def _close_session(session: object) -> None:
    closer = getattr(session, "close", None)
    if not callable(closer):
        return
    try:
        closer()
    except Exception:
        log.debug("EXT session close failed", exc_info=True)


class ExtSite(BaseSite):
    name = "ext"
    # Canonical first-party domain. ext.to/ext2.to currently challenge;
    # extto.com (and t.extto.com, which redirects there) currently serve
    # search HTML without a Cloudflare interstitial.
    url = "https://ext.to"
    available_urls: ClassVar[list[str]] = [
        "https://ext.to",
        "https://ext2.to",
        "https://t.extto.com",
        # t.extto.com currently redirects here, so allow and try its target.
        "https://extto.com",
    ]
    supports_tv = True
    supports_movies = True
    cloudflare_protected = True
    default_enabled = True

    _TOKEN_RE = re.compile(r"window\.searchPageToken\s*=\s*['\"]([^'\"]+)")
    _SIZE_RE = re.compile(r"([\d.]+)\s*(KB|MB|GB|TB)", re.IGNORECASE)

    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        return self._search(query)

    def search_show(
        self, query: str, show: Show, season_number: int
    ) -> list[IndexerQueryResult]:
        return self._search(query)

    def search_movie(self, query: str, movie: Movie) -> list[IndexerQueryResult]:
        return self._search(query)

    def _search(self, query: str) -> list[IndexerQueryResult]:
        try:
            html, session, origin = self._fetch_search_page(query)
        except Exception:
            log.exception("EXT search failed")
            return []

        try:
            return self._results_from_page(html, session, origin, query)
        finally:
            _close_session(session)

    def _results_from_page(
        self,
        html: str,
        session: Any,  # noqa: ANN401
        origin: str,
        query: str,
    ) -> list[IndexerQueryResult]:
        tree = HTMLParser(html)
        csrf_meta = tree.css_first('meta[name="csrf-token"]')
        csrf = csrf_meta.attributes.get("content", "") if csrf_meta else ""
        token_match = self._TOKEN_RE.search(html)
        if not csrf or not token_match:
            log.warning("EXT search response did not contain session tokens")
            return []
        page_token = token_match.group(1)

        results: list[IndexerQueryResult] = []
        for row in tree.css("table.search-table tbody tr")[:15]:
            try:
                parsed = self._parse_row(row)
                if parsed is None:
                    continue
                magnet = self._fetch_magnet(
                    session, origin, parsed["torrent_id"], csrf, page_token
                )
                if not magnet:
                    continue
                results.append(
                    IndexerQueryResult(
                        title=parsed["title"],
                        download_url=magnet,
                        seeders=parsed["seeders"],
                        flags=[],
                        size=parsed["size"],
                        usenet=False,
                        age=parsed["age"],
                        indexer=self.name,
                    )
                )
            except Exception:
                log.debug("Failed to parse EXT result", exc_info=True)

        log.info("EXT returned %s results for: %s", len(results), query)
        return results

    def _bypass_available(self) -> bool:
        return self.bypass is not None and self.bypass.config.enabled

    def _build_plain_session(self) -> httpx.Client:
        timeout = httpx.Timeout(float(self.timeout), connect=10.0)
        transport = httpx.HTTPTransport(local_address="0.0.0.0", retries=1)  # noqa: S104
        return httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": _DEFAULT_USER_AGENT},
            transport=transport,
        )

    def _build_cf_session(self) -> CloudflareSession:
        return CloudflareSession(bypass=self.bypass)

    def _is_challenge(self, response: object) -> bool:
        from miramedia.cloudflare.bypass import is_cloudflare_challenge

        return is_cloudflare_challenge(cast(Any, response))

    def _request_search(
        self,
        session: Any,  # noqa: ANN401
        origin: str,
        query: str,
    ) -> tuple[str, str] | None:
        """Return ``(html, final_origin)``, or ``None`` on a Cloudflare challenge."""
        url = f"{origin}/browse/"
        response = session.get(
            url,
            params={"q": query},
            headers={"Referer": f"{origin}/"},
            timeout=self.timeout,
        )
        if self._is_challenge(response):
            return None
        status = getattr(response, "status_code", 200)
        if status >= 400:
            msg = f"EXT {origin} returned HTTP {status}"
            raise RuntimeError(msg)
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            raise_for_status()
        final = urlparse(str(response.url))
        final_origin = f"{final.scheme}://{final.netloc}"
        if not is_allowed_mirror_origin(final_origin, self._mirror_list()):
            msg = "EXT redirected to an unconfigured mirror"
            raise RuntimeError(msg)
        if not HTMLParser(response.text).css("table.search-table"):
            msg = "EXT response contained no result rows"
            raise RuntimeError(msg)
        return response.text, final_origin

    def _solve_and_retry(
        self,
        session: Any,  # noqa: ANN401
        origin: str,
        query: str,
    ) -> tuple[str, str] | None:
        if self.bypass is None:
            return None
        solved = self.bypass.solve(f"{origin}/browse/?{urlencode({'q': query})}")
        result = self._request_search(session, origin, query)
        if result is not None:
            return result
        if isinstance(solved, str) and HTMLParser(solved).css("table.search-table"):
            return solved, origin
        return None

    def _fetch_search_page(self, query: str) -> tuple[str, Any, str]:
        last_error: Exception | None = None
        challenge_origins: list[str] = []
        plain = self._build_plain_session()
        try:
            for origin in self._get_mirror_pref().ordered():
                try:
                    result = self._request_search(plain, origin, query)
                except Exception as exc:
                    last_error = exc
                    log.debug("EXT mirror %s failed: %s", origin, exc)
                    continue
                if result is None:
                    challenge_origins.append(origin)
                    continue
                html, final_origin = result
                self._get_mirror_pref().mark_success(origin)
                return html, plain, final_origin
        except Exception:
            _close_session(plain)
            raise

        _close_session(plain)

        if self._bypass_available():
            for origin in challenge_origins:
                session = self._build_cf_session()
                try:
                    result = self._request_search(session, origin, query)
                    if result is None:
                        result = self._solve_and_retry(session, origin, query)
                except Exception as exc:
                    last_error = exc
                    log.debug("EXT Cloudflare mirror %s failed: %s", origin, exc)
                    _close_session(session)
                    continue
                if result is None:
                    last_error = RuntimeError(
                        "browser session could not be established"
                    )
                    _close_session(session)
                    continue
                html, final_origin = result
                self._get_mirror_pref().mark_success(origin)
                return html, session, final_origin

        if last_error is not None:
            raise last_error
        msg = "EXT has no configured mirrors"
        raise RuntimeError(msg)

    def _fetch_magnet(
        self,
        session: Any,  # noqa: ANN401
        origin: str,
        torrent_id: int,
        csrf: str,
        page_token: str,
    ) -> str | None:
        timestamp = int(time.time())
        signature = hashlib.sha256(
            f"{torrent_id}|{timestamp}|{page_token}".encode()
        ).hexdigest()
        response = session.post(
            f"{origin}/ajax/getSearchMagnet.php",
            data={
                "torrent_id": str(torrent_id),
                "hash": "",
                "name": "",
                "timestamp": str(timestamp),
                "hmac": signature,
                "sessid": csrf,
            },
            headers={
                "Referer": f"{origin}/browse/",
                "X-CSRF-Token": csrf,
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        magnet = payload.get("url") if payload.get("success") else None
        return (
            magnet
            if isinstance(magnet, str) and magnet.startswith("magnet:?")
            else None
        )

    def _parse_row(self, row: Node, *, now: datetime | None = None) -> _ExtRow | None:
        title_link = row.css_first("a.torrent-title-link")
        magnet_link = row.css_first("a.search-magnet-btn[data-id]")
        if title_link is None or magnet_link is None:
            return None
        title = parse_ext_title(title_link)
        try:
            torrent_id = int(magnet_link.attributes.get("data-id") or "")
        except ValueError:
            return None
        if not title or torrent_id <= 0:
            return None

        cells: dict[str, Node] = {}
        for wrapper in row.css("div.add-block-wrapper"):
            label = wrapper.css_first("span.add-block")
            spans = wrapper.css("span")
            if label is not None and len(spans) > 1:
                cells[label.text(strip=True).lower()] = spans[-1]

        seeders_text = cells["seeds"].text(strip=True) if "seeds" in cells else "0"
        try:
            seeders = int(seeders_text.replace(",", "") or 0)
        except ValueError:
            seeders = 0
        size_text = cells["size"].text(strip=True) if "size" in cells else ""
        return {
            "torrent_id": torrent_id,
            "title": title,
            "seeders": seeders,
            "size": self._parse_size(size_text),
            "age": parse_ext_age_days(cells.get("age"), now=now),
        }

    def _parse_size(self, value: str) -> int:
        match = self._SIZE_RE.search(value)
        if not match:
            return 0
        multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(float(match.group(1)) * multipliers[match.group(2).upper()])
