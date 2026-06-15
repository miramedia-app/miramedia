"""YTS/YIFY — Public movie torrent site with a free JSON API."""

import logging
from typing import ClassVar

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite, build_magnet
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class YtsSite(BaseSite):
    name = "yts"
    url = "https://yts.bz"
    available_urls: ClassVar[list[str]] = ["https://yts.bz", "https://yts.am"]
    supports_tv = False
    supports_movies = True
    cloudflare_protected = False
    # Probe the JSON API, not the (CF-frontable) website root.
    test_path = "/api/v2/list_movies.json?limit=1"

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
            data = self._fetch_json(
                f"{self.url}/api/v2/list_movies.json", params=params
            )
        except Exception:
            log.exception("YTS search failed")
            return []

        if data.get("status") != "ok":
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

                magnet = build_magnet(info_hash, title)
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
