"""YTS/YIFY — Public movie torrent site with a free JSON API."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlparse

import httpx

from miramedia.indexers.http_retry import indexer_fanout_deadline, indexer_get
from miramedia.indexers.mirrors import MirrorPreference, is_allowed_mirror_origin
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite, _get_http_client, build_magnet
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)

_LIST_MOVIES_PATH = "/api/v2/list_movies.json"


@dataclass(frozen=True)
class _YtsApiAttempt:
    data: dict | None
    origin: str | None
    valid: bool = False


class YtsSite(BaseSite):
    name = "yts"
    url = "https://yts.bz"
    available_urls: ClassVar[list[str]] = [
        "https://yts.bz",
        "https://yts.am",
        "https://yts.gg",
    ]
    supports_tv = False
    supports_movies = True
    cloudflare_protected = False
    # Probe the JSON API, not the (CF-frontable) website root.
    test_path = "/api/v2/list_movies.json?limit=1"
    _mirror_pref: MirrorPreference | None = None

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

    @staticmethod
    def _origin_from_response(response: httpx.Response, fallback: str) -> str:
        parsed = urlparse(str(response.url))
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        return fallback.rstrip("/")

    def _is_allowed_origin(self, origin: str) -> bool:
        return is_allowed_mirror_origin(origin, self._mirror_list())

    def _yts_get(
        self,
        url: str,
        params: dict | None,
        *,
        deadline: float | None = None,
    ) -> httpx.Response:
        client = _get_http_client()
        return indexer_get(
            client,
            url,
            params=params,
            timeout=self.timeout,
            deadline=deadline,
        )

    def _try_mirror(
        self,
        origin: str,
        path: str,
        params: dict[str, str | int],
        *,
        deadline: float | None = None,
    ) -> _YtsApiAttempt:
        url = f"{origin.rstrip('/')}{path}"
        try:
            response = self._yts_get(url, params, deadline=deadline)
        except Exception as exc:
            log.warning("YTS request failed for %s: %s", url, exc)
            return _YtsApiAttempt(data=None, origin=None)

        if response.status_code >= 400:
            return _YtsApiAttempt(data=None, origin=None)

        final_origin = self._origin_from_response(response, origin)
        if not self._is_allowed_origin(final_origin):
            log.warning("YTS redirect landed on untrusted origin %s", final_origin)
            return _YtsApiAttempt(data=None, origin=None)

        try:
            data = response.json()
        except Exception:
            log.warning("YTS response was not valid JSON from %s", url)
            return _YtsApiAttempt(data=None, origin=None)

        if not isinstance(data, dict) or data.get("status") != "ok":
            return _YtsApiAttempt(data=None, origin=None)

        return _YtsApiAttempt(data=data, origin=final_origin, valid=True)

    def _fetch_yts_json(self, params: dict[str, str | int]) -> dict | None:
        deadline = indexer_fanout_deadline()
        mirrors = self._get_mirror_pref().ordered()
        for origin in mirrors:
            attempt = self._try_mirror(
                origin,
                _LIST_MOVIES_PATH,
                params,
                deadline=deadline,
            )
            if attempt.valid and attempt.data is not None:
                self._get_mirror_pref().mark_success(attempt.origin or origin)
                return attempt.data
        return None

    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        return self._search_yts(query)

    def search_show(
        self,
        query: str,
        show: Show,
        season_number: int,
    ) -> list[IndexerQueryResult]:
        return []  # YTS is movies only

    def search_movie(self, query: str, movie: Movie) -> list[IndexerQueryResult]:
        params: dict[str, str | int] = {"query_term": query, "limit": 50}
        if movie.imdb_id:
            params["query_term"] = movie.imdb_id
        return self._search_yts(query, params)

    def _search_yts(
        self, query: str, params: dict | None = None
    ) -> list[IndexerQueryResult]:
        if params is None:
            params = {"query_term": query, "limit": 50}

        try:
            data = self._fetch_yts_json(params)
        except Exception:
            log.exception("YTS search failed")
            return []

        if data is None:
            return []

        movies = data.get("data", {}).get("movies") or []
        results: list[IndexerQueryResult] = []

        for movie_data in movies:
            for torrent in movie_data.get("torrents", []):
                title_parts = [
                    movie_data.get("title_long", movie_data.get("title", "")),
                    torrent.get("quality", ""),
                    torrent.get("type", ""),
                ]
                title = " ".join(p for p in title_parts if p)

                info_hash = torrent.get("hash", "")
                if not info_hash:
                    continue

                try:
                    magnet = build_magnet(info_hash, title)
                except ValueError:
                    log.warning(
                        "%s: dropping row with invalid info hash (len=%d)",
                        "yts",
                        len(info_hash.strip()),
                    )
                    continue
                size_bytes = torrent.get("size_bytes", 0)
                seeders = torrent.get("seeds", 0)

                results.append(
                    IndexerQueryResult(
                        title=title,
                        download_url=magnet,
                        seeders=seeders,
                        flags=[],
                        size=size_bytes,
                        usenet=False,
                        age=0,
                        indexer="yts",
                    )
                )

        log.info(f"YTS returned {len(results)} results for: {query}")
        return results
