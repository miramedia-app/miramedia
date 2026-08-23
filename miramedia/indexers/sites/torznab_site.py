"""Torznab site — For private trackers and custom Torznab-compatible endpoints."""

import logging

from miramedia.indexers.backends.torznab_mixin import TorznabMixin
from miramedia.indexers.config import TorznabSiteConfig
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.sites.base import BaseSite
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class TorznabSite(BaseSite, TorznabMixin):
    """
    A site that speaks the Torznab protocol directly.
    Used for private trackers and any custom Torznab-compatible endpoint.
    """

    supports_tv = True
    supports_movies = True

    def __init__(self, config: TorznabSiteConfig, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.name = config.name or "torznab"
        self.url = config.url
        self.api_key = config.api_key
        self.categories_tv = config.categories_tv
        self.categories_movies = config.categories_movies
        self.supports_tv = config.supports_tv
        self.supports_movies = config.supports_movies
        self.cloudflare_protected = config.cloudflare_protected

    def _torznab_search(self, params: dict) -> list[IndexerQueryResult]:
        """Execute a Torznab search and parse the XML results."""
        if self.api_key:
            params["apikey"] = self.api_key

        try:
            xml = self._fetch(self.url, params=params)
        except Exception as exc:
            log.error(  # noqa: TRY400 — exception text embeds apikey URL
                "Torznab search failed for %s (%s)",
                self.name,
                type(exc).__name__,
            )
            return []

        results = self.process_search_result(xml=xml)
        # Override indexer name
        for r in results:
            r.indexer = self.name

        log.info("Torznab site %s returned %s results", self.name, len(results))
        return results

    def search(self, query: str, category: str) -> list[IndexerQueryResult]:
        cat = self.categories_tv if category == "tv" else self.categories_movies
        return self._torznab_search(
            {
                "t": "search",
                "q": query,
                "cat": cat,
            }
        )

    def search_show(
        self, query: str, show: Show, season_number: int
    ) -> list[IndexerQueryResult]:
        params: dict[str, str | int] = {
            "t": "tvsearch",
            "q": query,
            "cat": self.categories_tv,
            "season": season_number,
        }
        if show.imdb_id:
            params["imdbid"] = show.imdb_id
        if show.metadata_provider == "tmdb":
            params["tmdbid"] = show.external_id
        elif show.metadata_provider == "tvdb":
            params["tvdbid"] = show.external_id

        return self._torznab_search(params)

    def search_movie(self, query: str, movie: Movie) -> list[IndexerQueryResult]:
        params: dict[str, str | int] = {
            "t": "movie",
            "q": query,
            "cat": self.categories_movies,
        }
        if movie.imdb_id:
            params["imdbid"] = movie.imdb_id
        if movie.metadata_provider == "tmdb":
            params["tmdbid"] = movie.external_id
        elif movie.metadata_provider == "tvdb":
            params["tvdbid"] = movie.external_id

        return self._torznab_search(params)
