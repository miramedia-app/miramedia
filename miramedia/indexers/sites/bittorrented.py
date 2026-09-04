"""BitTorrented — JSON API indexer for video torrents."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import cast

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite, build_magnet
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)

_MIN_QUERY_LEN = 3
_INFO_HASH_RE = re.compile(r"^[a-f0-9]{40}$")
_API_PATH = "/api/search/torrents"


class BitTorrentedSite(BaseSite):
    name = "bittorrented"
    url = "https://bittorrented.com"
    supports_tv = True
    supports_movies = True
    cloudflare_protected = False
    default_enabled = True
    test_path = (
        f"{_API_PATH}?q=test+video&type=video&limit=1&sortBy=seeders&sortOrder=desc"
    )

    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        return self._search_api(query)

    def search_show(
        self,
        query: str,
        show: Show,
        season_number: int,
    ) -> list[IndexerQueryResult]:
        return self._search_api(query)

    def search_movie(self, query: str, movie: Movie) -> list[IndexerQueryResult]:
        return self._search_api(query)

    def _search_api(self, query: str) -> list[IndexerQueryResult]:
        trimmed = query.strip()
        if len(trimmed) < _MIN_QUERY_LEN:
            return []

        params = {
            "q": trimmed,
            "type": "video",
            "limit": 50,
            "sortBy": "seeders",
            "sortOrder": "desc",
        }
        try:
            payload = self._fetch_over_mirrors(
                _API_PATH, params=params, fetch=self._fetch_json
            )
        except Exception:
            log.exception("BitTorrented search failed")
            return []

        if not isinstance(payload, dict):
            log.warning("BitTorrented returned non-object envelope")
            return []

        results = payload.get("results")
        if not isinstance(results, list):
            log.warning("BitTorrented envelope missing list-valued results")
            return []

        return self._map_results(results)

    def _map_results(self, rows: list[object]) -> list[IndexerQueryResult]:
        mapped: list[IndexerQueryResult] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            result = self._map_row(cast(dict[str, object], row))
            if result is not None:
                mapped.append(result)
        log.info("BitTorrented returned %d results", len(mapped))
        return mapped

    def _map_row(self, row: dict[str, object]) -> IndexerQueryResult | None:
        raw_hash = row.get("torrent_infohash")
        if not isinstance(raw_hash, str):
            return None
        info_hash = raw_hash.strip().lower()
        if not _INFO_HASH_RE.match(info_hash):
            log.warning(
                "bittorrented: dropping row with invalid info hash (len=%d)",
                len(info_hash),
            )
            return None

        title = row.get("torrent_name")
        if not isinstance(title, str) or not title.strip():
            title = info_hash

        try:
            magnet = build_magnet(info_hash, title)
        except ValueError:
            return None

        size = self._non_negative_int(row.get("torrent_total_size"))
        seeders = self._nullable_int(row.get("torrent_seeders"))
        age = self._age_days(row.get("torrent_created_at"))

        return IndexerQueryResult(
            title=title,
            download_url=magnet,
            seeders=seeders,
            flags=[],
            size=size,
            usenet=False,
            age=age,
            indexer="bittorrented",
        )

    @staticmethod
    def _non_negative_int(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        if isinstance(value, str):
            try:
                return max(0, int(value))
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _nullable_int(value: object) -> int | None:
        if value is None:
            return 0
        return BitTorrentedSite._non_negative_int(value)

    @staticmethod
    def _age_days(value: object) -> int:
        if not isinstance(value, str) or not value.strip():
            return 0
        try:
            created = datetime.fromisoformat(value)
        except ValueError:
            return 0
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return max(0, (now.date() - created.astimezone(UTC).date()).days)
