import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from requests import Response, Session

from miramedia.config import MiraMediaConfig
from miramedia.indexers.backends.generic import GenericIndexer
from miramedia.indexers.backends.torznab_mixin import TorznabMixin
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)

# Torznab providers often default to ~100 rows; cap parsing work per indexer call.
_NEWZNAB_RESULT_LIMIT = 500
_SEARCH_MAX_WORKERS = 4
_CAPABILITY_CACHE_TTL_SECONDS = 30.0


@dataclass
class IndexerInfo:
    id: int
    name: str

    supports_tv_search: bool
    supports_tv_search_tmdb: bool
    supports_tv_search_imdb: bool
    supports_tv_search_tvdb: bool
    supports_tv_search_season: bool

    supports_movie_search: bool
    supports_movie_search_tmdb: bool
    supports_movie_search_imdb: bool
    supports_movie_search_tvdb: bool


class Prowlarr(GenericIndexer, TorznabMixin):
    def __init__(self) -> None:
        """
        A subclass of GenericIndexer for interacting with the Prowlarr API.
        """
        super().__init__(name="prowlarr")
        self.config = MiraMediaConfig().indexers.prowlarr
        self._indexer_cache_lock = threading.Lock()
        self._indexer_cache_entry: tuple[float, list[IndexerInfo]] | None = None

    def _call_prowlarr_api(self, path: str, parameters: dict | None = None) -> Response:
        url = f"{self.config.url}/api/v1{path}"
        headers = {"X-Api-Key": self.config.api_key}
        timeout = MiraMediaConfig().indexers.timeout_seconds
        with Session() as session:
            return session.get(
                url=url,
                params=parameters,
                timeout=timeout,
                headers=headers,
            )

    def _invalidate_indexer_cache(self) -> None:
        with self._indexer_cache_lock:
            self._indexer_cache_entry = None

    def _newznab_search(
        self, indexer: IndexerInfo, parameters: dict | None = None
    ) -> list[IndexerQueryResult]:
        params = dict(parameters or {})
        params["limit"] = _NEWZNAB_RESULT_LIMIT
        results = self._call_prowlarr_api(
            path=f"/indexer/{indexer.id}/newznab", parameters=params
        )
        parsed = self.process_search_result(xml=results.content)
        log.info(
            "Indexer %s returned %s results for search: %s",
            indexer.name,
            len(parsed),
            params,
        )
        return parsed

    def _parse_indexer_list(self, indexers: list[dict]) -> list[IndexerInfo]:
        indexer_info_list: list[IndexerInfo] = []
        for indexer in indexers:
            supports_tv_search = False
            supports_movie_search = False
            tv_search_params = []
            movie_search_params = []

            if not indexer["capabilities"].get("tvSearchParams"):
                supports_tv_search = False
            else:
                supports_tv_search = True
                tv_search_params = indexer["capabilities"]["tvSearchParams"]

            if not indexer["capabilities"].get("movieSearchParams"):
                supports_movie_search = False
            else:
                supports_movie_search = True
                movie_search_params = indexer["capabilities"]["movieSearchParams"]

            indexer_info = IndexerInfo(
                id=indexer["id"],
                name=indexer.get("name", "unknown"),
                supports_tv_search=supports_tv_search,
                supports_tv_search_tmdb="tmdbId" in tv_search_params,
                supports_tv_search_imdb="imdbId" in tv_search_params,
                supports_tv_search_tvdb="tvdbId" in tv_search_params,
                supports_tv_search_season="season" in tv_search_params,
                supports_movie_search=supports_movie_search,
                supports_movie_search_tmdb="tmdbId" in movie_search_params,
                supports_movie_search_imdb="imdbId" in movie_search_params,
                supports_movie_search_tvdb="tvdbId" in movie_search_params,
            )
            indexer_info_list.append(indexer_info)
        return indexer_info_list

    def _fetch_indexers_from_api(self) -> list[IndexerInfo]:
        indexers = self._call_prowlarr_api(path="/indexer")
        return self._parse_indexer_list(indexers.json())

    def _get_indexers(self) -> list[IndexerInfo]:
        now = time.monotonic()
        with self._indexer_cache_lock:
            if self._indexer_cache_entry is not None:
                cached_at, cached = self._indexer_cache_entry
                if now - cached_at < _CAPABILITY_CACHE_TTL_SECONDS:
                    return list(cached)

        indexers = self._fetch_indexers_from_api()
        with self._indexer_cache_lock:
            self._indexer_cache_entry = (time.monotonic(), list(indexers))
        return indexers

    def _get_tv_indexers(self) -> list[IndexerInfo]:
        return [x for x in self._get_indexers() if x.supports_tv_search]

    def _get_movie_indexers(self) -> list[IndexerInfo]:
        return [x for x in self._get_indexers() if x.supports_movie_search]

    def _fan_out_newznab_searches(
        self,
        searches: list[tuple[IndexerInfo, dict]],
    ) -> list[IndexerQueryResult]:
        if not searches:
            return []

        max_workers = min(len(searches), _SEARCH_MAX_WORKERS)
        raw_results: list[IndexerQueryResult] = []

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="prowlarr-search"
        ) as executor:
            futures = [
                executor.submit(
                    self._newznab_search, indexer=indexer, parameters=parameters
                )
                for indexer, parameters in searches
            ]
            for future in futures:
                raw_results.extend(future.result())

        return raw_results

    def search(self, query: str, is_tv: bool) -> list[IndexerQueryResult]:
        log.info("Searching for: %s", query)
        params = {
            "q": query,
            "t": "tvsearch" if is_tv else "movie",
        }
        indexers = self._get_tv_indexers() if is_tv else self._get_movie_indexers()
        return self._fan_out_newznab_searches(
            [(indexer, params) for indexer in indexers]
        )

    def search_season(
        self, query: str, show: Show, season_number: int
    ) -> list[IndexerQueryResult]:
        indexers = self._get_tv_indexers()
        searches: list[tuple[IndexerInfo, dict]] = []

        for indexer in indexers:
            log.debug("Preparing search for indexer: %s", indexer.name)
            search_params = {
                "cat": "5000",
                "q": query,
                "t": "tvsearch",
            }

            if indexer.supports_tv_search_tmdb and show.metadata_provider == "tmdb":
                search_params["tmdbid"] = show.external_id
            if indexer.supports_tv_search_tvdb and show.metadata_provider == "tvdb":
                search_params["tvdbid"] = show.external_id
            if indexer.supports_tv_search_imdb:
                search_params["imdbid"] = show.imdb_id
            if indexer.supports_tv_search_season:
                search_params["season"] = season_number

            searches.append((indexer, search_params))

        return self._fan_out_newznab_searches(searches)

    def search_movie(self, query: str, movie: Movie) -> list[IndexerQueryResult]:
        indexers = self._get_movie_indexers()
        searches: list[tuple[IndexerInfo, dict]] = []

        for indexer in indexers:
            log.debug("Preparing search for indexer: %s", indexer.name)

            search_params = {
                "cat": "2000",
                "q": query,
                "t": "movie",
            }

            if indexer.supports_movie_search_tmdb and movie.metadata_provider == "tmdb":
                search_params["tmdbid"] = movie.external_id
            if indexer.supports_movie_search_tvdb and movie.metadata_provider == "tvdb":
                search_params["tvdbid"] = movie.external_id
            if indexer.supports_movie_search_imdb:
                search_params["imdbid"] = movie.imdb_id

            searches.append((indexer, search_params))

        return self._fan_out_newznab_searches(searches)
