"""1337x — Popular torrent site with mirror failover and optional CF bypass."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, cast
from urllib.parse import quote_plus, urlparse

import httpx
from selectolax.parser import HTMLParser, Node

from miramedia.indexers.http_retry import (
    indexer_fanout_deadline_seconds,
    indexer_get,
)
from miramedia.indexers.mirrors import MirrorPreference, is_allowed_mirror_origin
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite, _get_http_client
from miramedia.indexers.utils import sanitize_search_query
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)

UNKNOWN_AGE_DAYS = 36_500

_MONTHS: dict[str, int] = {
    "jan": 0,
    "feb": 1,
    "mar": 2,
    "apr": 3,
    "may": 4,
    "jun": 5,
    "jul": 6,
    "aug": 7,
    "sep": 8,
    "oct": 9,
    "nov": 10,
    "dec": 11,
}

_UPLOAD_DATE_RE = re.compile(
    r"Date uploaded</strong>.*?([A-Za-z]{3})\.?\s+(\d{1,2})[a-z]{2}\s*'(\d{2})",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class _PageAttempt:
    html: str | None
    origin: str | None
    hard_error: bool = False
    challenge_confirmed: bool = False
    no_results: bool = False

    @property
    def success(self) -> bool:
        return self.html is not None and self.origin is not None


def parse_upload_age_days(
    html: str,
    *,
    now: datetime | None = None,
) -> int:
    """Return non-negative age in days from a 1337x detail page.

    Missing or malformed upload dates return ``UNKNOWN_AGE_DAYS`` so they earn
    no recency bonus. Small future clock skew is clamped to zero.
    """
    match = _UPLOAD_DATE_RE.search(html)
    if not match:
        return UNKNOWN_AGE_DAYS

    month_name = match.group(1).lower()
    month = _MONTHS.get(month_name)
    if month is None:
        return UNKNOWN_AGE_DAYS

    try:
        day = int(match.group(2))
        year = 2000 + int(match.group(3))
        uploaded = datetime(year, month + 1, day, tzinfo=UTC).date()
    except ValueError:
        return UNKNOWN_AGE_DAYS

    reference = (now or datetime.now(UTC)).date()
    age_days = (reference - uploaded).days
    return max(0, age_days)


class X1337Site(BaseSite):
    name = "1337x"
    url = "https://1337x.to"
    available_urls: ClassVar[list[str]] = [
        "https://1337x.to",
        "https://1337x.st",
        "https://x1337x.ws",
        "https://1337xx.to",
    ]
    supports_tv = True
    supports_movies = True
    cloudflare_protected = True
    default_enabled = True
    _mirror_pref: MirrorPreference | None = None

    def _solver_deadline_seconds(self) -> float:
        return indexer_fanout_deadline_seconds()

    def _mirror_list(self) -> tuple[str, ...]:
        seen: set[str] = set()
        mirrors: list[str] = []
        for candidate in (self.url, *self.available_urls):
            normalized = candidate.rstrip("/")
            if normalized and normalized not in seen:
                seen.add(normalized)
                mirrors.append(normalized)
        return tuple(mirrors)

    def _get_mirror_pref(self) -> MirrorPreference:
        if self._mirror_pref is None:
            self._mirror_pref = MirrorPreference(self._mirror_list())
        return self._mirror_pref

    def _bypass_available(self) -> bool:
        return self.bypass is not None and self.bypass.config.enabled

    @staticmethod
    def _origin_from_response(response: httpx.Response, fallback: str) -> str:
        parsed = urlparse(str(response.url))
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return fallback.rstrip("/")

    def _safe_origin_from_response(
        self, response: httpx.Response, requested: str
    ) -> str:
        """Adopt the post-redirect origin only when it is an allowlisted mirror."""
        candidate = self._origin_from_response(response, requested)
        if is_allowed_mirror_origin(candidate, self._mirror_list()):
            return candidate
        if candidate.rstrip("/") != requested.rstrip("/"):
            log.warning(
                "1337x redirect landed on untrusted origin %s; keeping %s",
                candidate,
                requested,
            )
        return requested.rstrip("/")

    def _classify_plain_response(
        self, response: httpx.Response, origin: str
    ) -> _PageAttempt:
        from miramedia.cloudflare.bypass import is_cloudflare_challenge

        if is_cloudflare_challenge(cast(Any, response)):
            return _PageAttempt(
                html=None,
                origin=origin,
                challenge_confirmed=True,
            )

        if response.status_code >= 400:
            return _PageAttempt(
                html=None,
                origin=None,
                hard_error=True,
            )

        html = response.text
        tree = HTMLParser(html)
        if tree.css("table.table-list tbody tr"):
            final_origin = self._safe_origin_from_response(response, origin)
            return _PageAttempt(html=html, origin=final_origin)

        body = tree.body
        page_text = (
            body.text(separator=" ", strip=True) if body else (html or "")
        ).lower()
        if "no results" in page_text:
            final_origin = self._safe_origin_from_response(response, origin)
            return _PageAttempt(
                html=html,
                origin=final_origin,
                no_results=True,
            )
        if "bad request" in page_text:
            return _PageAttempt(html=None, origin=None, hard_error=True)

        return _PageAttempt(html=None, origin=None, hard_error=True)

    def _plain_get(self, url: str, *, deadline: float | None = None) -> httpx.Response:
        client = _get_http_client()
        return indexer_get(client, url, timeout=self.timeout, deadline=deadline)

    def _try_search_paths_plain(
        self,
        origin: str,
        search_paths: tuple[str, ...],
        *,
        deadline: float | None = None,
    ) -> _PageAttempt:
        hard_error = False
        for path in search_paths:
            url = f"{origin}{path}"
            try:
                response = self._plain_get(url, deadline=deadline)
            except Exception as exc:
                log.warning("1337x plain request failed for %s: %s", url, exc)
                hard_error = True
                continue

            attempt = self._classify_plain_response(response, origin)
            if attempt.success or attempt.no_results:
                return attempt
            if attempt.challenge_confirmed:
                return attempt
            hard_error = hard_error or attempt.hard_error
        return _PageAttempt(html=None, origin=None, hard_error=hard_error)

    def _fetch_via_bypass_session(self, url: str) -> str | None:
        if not self._bypass_available():
            return None

        try:
            from miramedia.cloudflare.bypass import is_cloudflare_challenge
            from miramedia.cloudflare.session import CloudflareSession
        except Exception:
            log.debug("CloudflareSession unavailable", exc_info=True)
            return None

        session = getattr(self, "_cf_session", None)
        if session is None:
            try:
                session = CloudflareSession(bypass=self.bypass)
            except Exception:
                log.debug("Failed to build CloudflareSession", exc_info=True)
                return None
            self._cf_session = session  # type: ignore[attr-defined]

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
            log.warning("1337x curl_cffi request failed for %s: %s", url, exc)
            return None

        if is_cloudflare_challenge(cast(Any, response)):
            return None
        if response.status_code >= 400:
            return None

        if self.bypass is not None:
            try:
                self.bypass.refresh_cache_from_cookies(
                    parsed.netloc,
                    dict(session.cookies.items()),
                    session.headers.get("User-Agent"),
                )
            except Exception:
                log.debug("CF cache refresh failed", exc_info=True)
        return response.text

    def _fetch_with_bypass(self, url: str, timeout: float) -> str | None:
        if not self._bypass_available():
            return None

        html = self._fetch_via_bypass_session(url)
        if html is not None:
            return html

        bypass = self.bypass
        if bypass is None:
            return None
        return bypass.solve(url, timeout=timeout)

    def _try_search_paths_bypass(
        self,
        origin: str,
        search_paths: tuple[str, ...],
        *,
        timeout: float,
    ) -> _PageAttempt:
        hard_error = False
        for path in search_paths:
            url = f"{origin}{path}"
            try:
                html = self._fetch_with_bypass(url, timeout=timeout)
            except Exception as exc:
                log.warning("1337x bypass request failed for %s: %s", url, exc)
                hard_error = True
                continue

            if not html:
                hard_error = True
                continue

            tree = HTMLParser(html)
            if tree.css("table.table-list tbody tr"):
                return _PageAttempt(html=html, origin=origin)
            body = tree.body
            page_text = (
                body.text(separator=" ", strip=True) if body else (html or "")
            ).lower()
            if "no results" in page_text:
                return _PageAttempt(html=html, origin=origin, no_results=True)
            hard_error = True
        return _PageAttempt(html=None, origin=None, hard_error=hard_error)

    def _search_paths(self, query: str, category: str) -> tuple[str, ...]:
        encoded = quote_plus(query)
        return (
            f"/sort-category-search/{encoded}/{category}/seeders/desc/1/",
            f"/search/{encoded}/1/",
        )

    def _fetch_search_page(
        self,
        query: str,
        category: str,
        *,
        deadline: float,
    ) -> tuple[_PageAttempt, list[str], list[str]]:
        """Try every mirror with plain HTTP, then optional bypass for CF mirrors."""
        search_paths = self._search_paths(query, category)
        mirrors = self._get_mirror_pref().ordered()

        challenge_mirrors: list[str] = []
        plain_tried: list[str] = []
        hard_error = False

        for origin in mirrors:
            plain_tried.append(origin)
            attempt = self._try_search_paths_plain(
                origin,
                search_paths,
                deadline=deadline,
            )
            if attempt.success:
                self._get_mirror_pref().mark_success(origin)
                return attempt, plain_tried, []
            if attempt.no_results:
                self._get_mirror_pref().mark_success(origin)
                return attempt, plain_tried, []
            if attempt.challenge_confirmed:
                challenge_mirrors.append(origin)
            hard_error = hard_error or attempt.hard_error

        if not challenge_mirrors or not self._bypass_available():
            if plain_tried or challenge_mirrors:
                log.warning(
                    "1337x: all mirrors failed (plain=%s, challenge=%s)",
                    plain_tried,
                    challenge_mirrors,
                )
            return (
                _PageAttempt(html=None, origin=None, hard_error=True),
                plain_tried,
                [],
            )

        solver_tried: list[str] = []
        for origin in challenge_mirrors:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            solver_tried.append(origin)
            attempt = self._try_search_paths_bypass(
                origin, search_paths, timeout=remaining
            )
            if attempt.success or attempt.no_results:
                self._get_mirror_pref().mark_success(origin)
                return attempt, plain_tried, solver_tried
            hard_error = hard_error or attempt.hard_error

        log.warning(
            "1337x: all mirrors failed (plain=%s, solver=%s)",
            plain_tried,
            solver_tried,
        )
        return (
            _PageAttempt(html=None, origin=None, hard_error=True),
            plain_tried,
            solver_tried,
        )

    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        cat_path = "TV" if category == "tv" else "Movies"
        results, hard_error = self._search(query, cat_path)
        if not results and hard_error:
            results = self._search_trending(cat_path, query)
        return results

    def search_show(
        self, query: str, show: Show, season_number: int
    ) -> list[IndexerQueryResult]:
        results, hard_error = self._search(query, "TV")
        if results:
            return results

        clean_name = sanitize_search_query(show.name)
        short_query = f"{clean_name} S{season_number:02d}"
        if short_query != query:
            log.info(
                "1337x: no results for %r, falling back to broader query %r",
                query,
                short_query,
            )
            broad_results, broad_error = self._search(short_query, "TV")
            if broad_results:
                return broad_results
            hard_error = hard_error or broad_error

        if hard_error:
            log.info("1337x: search errored for %r, trying trending fallback", query)
            return self._search_trending("TV", short_query)
        return []

    def search_movie(
        self,
        query: str,
        movie: Movie,
    ) -> list[IndexerQueryResult]:
        clean_name = sanitize_search_query(movie.name)
        short_query = f"{clean_name} {movie.year}" if movie.year else clean_name
        results, hard_error = self._search(short_query, "Movies")
        if not results and hard_error:
            results = self._search_trending("Movies", short_query)
        return results

    def _search(
        self, query: str, category: str
    ) -> tuple[list[IndexerQueryResult], bool]:
        deadline = time.monotonic() + self._solver_deadline_seconds()
        attempt, _plain_tried, _solver_tried = self._fetch_search_page(
            query,
            category,
            deadline=deadline,
        )
        if attempt.no_results:
            log.info("1337x: 0 results (no matches) for: %s", query)
            return [], False
        if not attempt.success or attempt.origin is None:
            return [], attempt.hard_error

        results, err = self._parse_results(
            attempt.html or "",
            query,
            origin=attempt.origin,
            deadline=deadline,
        )
        if results:
            return results, False
        return [], err

    def _search_trending(
        self, category: str, filter_query: str
    ) -> list[IndexerQueryResult]:
        cat_path = category.lower()
        trending_slug = "television" if cat_path == "tv" else cat_path
        trending_path = f"/top-100-{trending_slug}"

        mirrors = self._get_mirror_pref().ordered()
        html: str | None = None
        origin: str | None = None
        deadline = time.monotonic() + self._solver_deadline_seconds()
        for mirror in mirrors:
            try:
                response = self._plain_get(
                    f"{mirror}{trending_path}", deadline=deadline
                )
            except Exception as exc:
                log.warning("1337x trending fetch failed for %s: %s", mirror, exc)
                continue
            attempt = self._classify_plain_response(response, mirror)
            if attempt.success and attempt.origin:
                html = attempt.html
                origin = attempt.origin
                self._get_mirror_pref().mark_success(mirror)
                break

        if html is None or origin is None:
            return []

        all_results, _ = self._parse_results(
            html, f"trending/{cat_path}", origin=origin
        )
        if not all_results or not filter_query:
            return all_results

        query_words = filter_query.lower().split()
        filtered = [
            r
            for r in all_results
            if any(word in r.title.lower() for word in query_words)
        ]
        if filtered:
            log.info(
                "1337x trending fallback: %d results matching %r",
                len(filtered),
                filter_query,
            )
        return filtered

    def _parse_results(
        self,
        html: str,
        label: str,
        *,
        origin: str | None = None,
        deadline: float | None = None,
    ) -> tuple[list[IndexerQueryResult], bool]:
        tree = HTMLParser(html)

        rows = tree.css("table.table-list tbody tr")
        if rows:
            active_origin = origin or self.url.rstrip("/")
            return (
                self._parse_rows(
                    rows,
                    label,
                    origin=active_origin,
                    deadline=deadline,
                ),
                False,
            )

        body = tree.body
        page_text = (
            body.text(separator=" ", strip=True) if body else (html or "")
        ).lower()

        if "no results" in page_text:
            log.info("1337x: 0 results (no matches) for: %s", label)
            return [], False
        if "bad request" in page_text:
            log.warning("1337x: bad request for: %s", label)
            return [], True

        title_el = tree.css_first("title")
        page_title = title_el.text(strip=True) if title_el else "N/A"
        snippet = (html or "")[:400].replace("\n", " ").strip()
        log.info(
            "1337x returned no result table for: %s (page_title=%r, html_bytes=%d, snippet=%s)",
            label,
            page_title,
            len(html or ""),
            snippet,
        )
        return [], True

    def _parse_rows(
        self,
        rows: list[Node],
        label: str,
        *,
        origin: str,
        deadline: float | None = None,
    ) -> list[IndexerQueryResult]:
        parsed_rows = []
        for row in rows[:10]:
            try:
                meta = self._parse_row_metadata(row)
                if meta:
                    parsed_rows.append(meta)
            except Exception:
                log.debug("Failed to parse 1337x row", exc_info=True)

        results: list[IndexerQueryResult] = []
        for meta in parsed_rows:
            try:
                magnet, age = self._fetch_magnet(
                    meta,
                    origin=origin,
                    deadline=deadline,
                )
                if magnet:
                    results.append(
                        IndexerQueryResult(
                            title=meta["title"],
                            download_url=magnet,
                            seeders=meta["seeders"],
                            flags=[],
                            size=meta["size"],
                            usenet=False,
                            age=age,
                            indexer="1337x",
                        )
                    )
            except Exception:
                log.debug("Failed to fetch magnet for %s", meta["title"], exc_info=True)

        log.info("1337x returned %d results for: %s", len(results), label)
        return results

    @staticmethod
    def _parse_row_metadata(row: Node) -> dict | None:
        link = row.css_first('a[href*="/torrent/"]')
        if not link:
            return None

        title = link.text(strip=True)
        detail_path = link.attributes.get("href") or ""
        if not detail_path:
            return None

        seeders = 0
        seed_cell = row.css_first("td.coll-2")
        if seed_cell:
            try:
                seeders = int(seed_cell.text(strip=True))
            except ValueError:
                pass

        size_bytes = 0
        size_cell = row.css_first("td.coll-4")
        if size_cell:
            size_bytes = X1337Site._parse_size(size_cell.text(strip=True))

        return {
            "title": title,
            "detail_path": detail_path,
            "seeders": seeders,
            "size": size_bytes,
        }

    def _fetch_magnet(
        self,
        meta: dict,
        *,
        origin: str,
        deadline: float | None = None,
    ) -> tuple[str | None, int]:
        detail_url = f"{origin}{meta['detail_path']}"
        try:
            response = self._plain_get(detail_url, deadline=deadline)
            if response.status_code >= 400:
                log.warning(
                    "Failed to fetch 1337x detail page %s: HTTP %s",
                    detail_url,
                    response.status_code,
                )
                return None, UNKNOWN_AGE_DAYS
            detail_html = response.text
        except Exception as exc:
            log.warning("Failed to fetch 1337x detail page %s: %s", detail_url, exc)
            return None, UNKNOWN_AGE_DAYS

        detail_tree = HTMLParser(detail_html)
        magnet_link = detail_tree.css_first("a[href^='magnet:']")
        if not magnet_link:
            log.warning(
                "No magnet link on 1337x detail page %s (parser may need updating)",
                detail_url,
            )
            return None, UNKNOWN_AGE_DAYS

        href = magnet_link.attributes.get("href") or ""
        age = parse_upload_age_days(detail_html)
        return (href or None), age

    @staticmethod
    def _parse_size(size_str: str) -> int:
        match = re.match(r"([\d.]+)\s*(GB|MB|KB|TB)", size_str, re.IGNORECASE)
        if not match:
            return 0
        value = float(match.group(1))
        unit = match.group(2).upper()
        multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(value * multipliers.get(unit, 1))
