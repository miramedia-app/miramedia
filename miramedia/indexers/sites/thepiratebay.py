"""The Pirate Bay — Public torrent site via ApiBay JSON API."""

import logging

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite, build_magnet
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class ThePirateBaySite(BaseSite):
    name = "thepiratebay"
    url = "https://apibay.org"
    supports_tv = True
    supports_movies = True
    # apibay.org/ 403s behind Cloudflare; the JSON API endpoint serves fine.
    test_path = "/q.php?q=test"

    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        return self._search_tpb(query)

    def search_show(
        self,
        query: str,
        show: Show,
        season_number: int,
    ) -> list[IndexerQueryResult]:
        return self._search_tpb(query)

    def search_movie(
        self,
        query: str,
        movie: Movie,
    ) -> list[IndexerQueryResult]:
        return self._search_tpb(query)

    def _search_tpb(self, query: str) -> list[IndexerQueryResult]:
        # ApiBay's ``cat=205`` (TV shows) excludes HD-TV (207) + UHD-TV (208)
        # buckets, so single-episode releases were getting filtered out
        # server-side. Send the bare query and let downstream scoring decide
        # what counts as a TV vs movie match. Matches the behaviour of the
        # browser-facing /search.php?q=... endpoint.
        params = {"q": query}
        try:
            data = self._fetch_json(f"{self.url}/q.php", params=params)
        except Exception:
            log.exception("TPB search failed")
            return []

        if not isinstance(data, list):
            return []

        results: list[IndexerQueryResult] = []
        for item in data:
            name = item.get("name", "")
            info_hash = item.get("info_hash", "")

            # ApiBay returns a single entry with id=0 when no results
            if not name or not info_hash or item.get("id") == "0":
                continue

            magnet = build_magnet(info_hash, name)

            try:
                size = int(item.get("size", 0))
            except (ValueError, TypeError):
                size = 0

            try:
                seeders = int(item.get("seeders", 0))
            except (ValueError, TypeError):
                seeders = 0

            results.append(
                IndexerQueryResult(
                    title=name,
                    download_url=magnet,
                    seeders=seeders,
                    flags=[],
                    size=size,
                    usenet=False,
                    age=0,
                    indexer="thepiratebay",
                )
            )

        log.info(f"TPB returned {len(results)} results for: {query}")
        return results
