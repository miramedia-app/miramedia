import asyncio
import logging
import os
import time
from collections.abc import Callable

from miramedia.config import MiraMediaConfig
from miramedia.indexers.backends.generic import GenericIndexer
from miramedia.indexers.backends.jackett import Jackett
from miramedia.indexers.backends.native import get_native_indexer
from miramedia.indexers.backends.prowlarr import Prowlarr
from miramedia.indexers.repository import IndexerRepository
from miramedia.indexers.schemas import IndexerQueryResult, IndexerQueryResultId
from miramedia.indexers.utils import sanitize_search_query, search_name_variants
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)

# Callback fired with ``(source_name, results)`` as each backend completes —
# used by the SSE streaming endpoint to surface partial results.
OnPartial = Callable[[str, list[IndexerQueryResult]], None]

_BREAKER_FAILURES: dict[str, tuple[int, float]] = {}
_BREAKER_THRESHOLD = max(1, int(os.getenv("MIRAMEDIA_INDEXER_CIRCUIT_FAILURES", "3")))
_BREAKER_COOLDOWN = max(
    1, int(os.getenv("MIRAMEDIA_INDEXER_CIRCUIT_COOLDOWN_SECONDS", "300"))
)
_INDEXER_FANOUT_LIMIT = max(1, int(os.getenv("MIRAMEDIA_INDEXER_FANOUT_LIMIT", "8")))


def _query_variants(name: str, suffix: str = "") -> list[str]:
    """Sanitized search queries for a media name.

    Full title plus its pre-colon main title — release groups usually drop
    metadata subtitles, so a subtitle-only query returns nothing on most
    trackers.
    """
    queries: list[str] = []
    for variant in search_name_variants(name):
        query = sanitize_search_query(f"{variant}{suffix}")
        if query and query not in queries:
            queries.append(query)
    return queries


def _dedupe_results(results: list[IndexerQueryResult]) -> list[IndexerQueryResult]:
    """Drop duplicate torrents surfaced by more than one query variant."""
    seen: set[str] = set()
    unique: list[IndexerQueryResult] = []
    for result in results:
        if result.download_url in seen:
            continue
        seen.add(result.download_url)
        unique.append(result)
    return unique


class IndexerService:
    def __init__(self, indexer_repository: IndexerRepository) -> None:
        self.repository = indexer_repository
        self.indexers: list[GenericIndexer] = []
        self._initialized = False

    async def _ensure_initialized(self) -> None:
        """Lazy backend init — `get_all_sites()` is async so we can't do it in `__init__`."""
        if self._initialized:
            return
        config = MiraMediaConfig()
        if config.indexers.prowlarr.enabled:
            self.indexers.append(Prowlarr())
        if config.indexers.jackett.enabled:
            self.indexers.append(Jackett())
        if config.indexers.native.enabled:
            db_sites = await self.repository.get_all_sites()
            self.indexers.append(get_native_indexer(db_sites=db_sites))
        self._initialized = True

    async def _release_before_fanout(self, on_partial: OnPartial | None) -> None:
        """Return the session's connection to the pool before the slow fan-out.

        ``_ensure_initialized()`` runs ``get_all_sites()`` which leaves the
        asyncpg connection ``idle in transaction`` (implicit ``BEGIN`` on the
        read, no following commit). Held across the multi-minute indexer
        fan-out (cloudflare bypass + parallel HTTP) it gets reaped by Postgres
        ``idle_in_transaction_session_timeout``, and the first post-fan-out
        ``save_result`` then fails with ``InterfaceError: connection is
        closed`` — because the session reuses the dead held connection
        verbatim (no fresh checkout, so ``pool_pre_ping`` never runs).
        Releasing here forces the persistence loop to re-check out a fresh,
        pre-pinged connection.

        Note: a caller releasing *before* invoking ``search_*`` is not enough
        — ``_ensure_initialized`` re-acquires a connection after that release.
        This must run after init and immediately before the fan-out.

        Streaming searches (``on_partial`` set) persist each chunk *during*
        the fan-out, so the connection must stay live — skip the release.
        """
        if on_partial is not None:
            return
        from miramedia.database import release_session_before_external_io

        await release_session_before_external_io(self.repository.db)

    async def get_result(self, result_id: IndexerQueryResultId) -> IndexerQueryResult:
        return await self.repository.get_result(result_id=result_id)

    @staticmethod
    def _invoke_indexer_search(
        indexer: GenericIndexer, query: str, is_tv: bool, on_partial: OnPartial | None
    ) -> list[IndexerQueryResult]:
        """Run a single indexer's sync ``search`` call. Wrapped so the
        outer async method can dispatch it to a worker thread and the
        event loop stays free for other requests during long HTTP/CF work.
        """
        try:
            return indexer.search(query, is_tv=is_tv, on_site_result=on_partial)
        except TypeError:
            results = indexer.search(query, is_tv=is_tv)
            if on_partial is not None:
                on_partial(indexer.__class__.__name__, results)
            return results

    @staticmethod
    def _indexer_name(indexer: GenericIndexer) -> str:
        return getattr(indexer, "name", None) or indexer.__class__.__name__

    @staticmethod
    def _circuit_allows(name: str) -> bool:
        failures, opened_at = _BREAKER_FAILURES.get(name, (0, 0.0))
        if failures < _BREAKER_THRESHOLD:
            return True
        if time.monotonic() - opened_at >= _BREAKER_COOLDOWN:
            _BREAKER_FAILURES.pop(name, None)
            return True
        return False

    @staticmethod
    def _record_indexer_success(name: str) -> None:
        _BREAKER_FAILURES.pop(name, None)

    @staticmethod
    def _record_indexer_failure(name: str) -> None:
        failures, _opened_at = _BREAKER_FAILURES.get(name, (0, 0.0))
        failures += 1
        opened_at = time.monotonic() if failures >= _BREAKER_THRESHOLD else 0.0
        _BREAKER_FAILURES[name] = (failures, opened_at)
        if failures == _BREAKER_THRESHOLD:
            log.warning(
                "Indexer circuit opened for %s after %d failures; cooling down for %ss",
                name,
                failures,
                _BREAKER_COOLDOWN,
            )

    @staticmethod
    def _invoke_indexer_kwargs(
        fn: Callable[..., list[IndexerQueryResult]],
        kwargs: dict,
        extra: dict,
        on_partial: OnPartial | None,
        source_name: str,
    ) -> list[IndexerQueryResult]:
        """Sync helper for typed search methods (search_show/search_season/
        search_movie). Handles backends that don't accept on_site_result."""
        try:
            return fn(**kwargs, **extra)
        except TypeError:
            results = fn(**kwargs)
            if on_partial is not None:
                on_partial(source_name, results or [])
            return results

    async def search(
        self, query: str, is_tv: bool, on_partial: OnPartial | None = None
    ) -> list[IndexerQueryResult]:
        """Search indexers in parallel; each backend runs in its own thread."""
        await self._ensure_initialized()
        log.debug("Searching for: %s", query)
        sem = asyncio.Semaphore(_INDEXER_FANOUT_LIMIT)

        async def _run_one(indexer: GenericIndexer) -> list[IndexerQueryResult]:
            name = self._indexer_name(indexer)
            if not self._circuit_allows(name):
                log.info("Skipping %s: circuit breaker open", name)
                return []
            try:
                async with sem:
                    indexer_results = await asyncio.to_thread(
                        self._invoke_indexer_search, indexer, query, is_tv, on_partial
                    )
                self._record_indexer_success(name)
                log.debug(
                    "Indexer %s returned %d results for query: %s",
                    indexer.__class__.__name__,
                    len(indexer_results or []),
                    query,
                )
            except Exception:
                self._record_indexer_failure(name)
                log.exception(
                    f"Indexer {indexer.__class__.__name__} failed for query '{query}'"
                )
                return []
            else:
                return indexer_results or []

        await self._release_before_fanout(on_partial)
        gathered = await asyncio.gather(*(_run_one(i) for i in self.indexers))
        results: list[IndexerQueryResult] = []
        for r in gathered:
            results.extend(r or [])

        if results:
            await self.repository.save_results(results)

        return results

    async def search_movie(
        self, movie: Movie, on_partial: OnPartial | None = None
    ) -> list[IndexerQueryResult]:
        await self._ensure_initialized()
        queries = _query_variants(movie.name)

        extra = {"on_site_result": on_partial} if on_partial is not None else {}
        sem = asyncio.Semaphore(_INDEXER_FANOUT_LIMIT)

        async def _run_one(
            indexer: GenericIndexer, query: str
        ) -> list[IndexerQueryResult]:
            name = self._indexer_name(indexer)
            if not self._circuit_allows(name):
                log.info("Skipping %s: circuit breaker open", name)
                return []
            try:
                async with sem:
                    result = await asyncio.to_thread(
                        self._invoke_indexer_kwargs,
                        indexer.search_movie,
                        {"query": query, "movie": movie},
                        extra,
                        on_partial,
                        name,
                    )
                self._record_indexer_success(name)
            except Exception:
                self._record_indexer_failure(name)
                log.exception(
                    f"Indexer {indexer.__class__.__name__} failed for movie search '{query}'"
                )
                return []
            else:
                return result

        await self._release_before_fanout(on_partial)
        gathered = await asyncio.gather(
            *(_run_one(i, q) for q in queries for i in self.indexers)
        )
        results: list[IndexerQueryResult] = []
        for r in gathered:
            if r:
                results.extend(r)
        results = _dedupe_results(results)

        if results:
            await self.repository.save_results(results)

        return results

    async def search_season(
        self, show: Show, season_number: int, on_partial: OnPartial | None = None
    ) -> list[IndexerQueryResult]:
        await self._ensure_initialized()
        queries = _query_variants(show.name, f" S{season_number:02d}")

        extra = {"on_site_result": on_partial} if on_partial is not None else {}
        sem = asyncio.Semaphore(_INDEXER_FANOUT_LIMIT)

        async def _run_one(
            indexer: GenericIndexer, query: str
        ) -> list[IndexerQueryResult]:
            name = self._indexer_name(indexer)
            if not self._circuit_allows(name):
                log.info("Skipping %s: circuit breaker open", name)
                return []
            try:
                async with sem:
                    result = await asyncio.to_thread(
                        self._invoke_indexer_kwargs,
                        indexer.search_season,
                        {"query": query, "show": show, "season_number": season_number},
                        extra,
                        on_partial,
                        name,
                    )
                self._record_indexer_success(name)
            except Exception:
                self._record_indexer_failure(name)
                log.exception(
                    f"Indexer {indexer.__class__.__name__} failed for season search '{query}'"
                )
                return []
            else:
                return result

        await self._release_before_fanout(on_partial)
        gathered = await asyncio.gather(
            *(_run_one(i, q) for q in queries for i in self.indexers)
        )
        results: list[IndexerQueryResult] = []
        for r in gathered:
            if r:
                results.extend(r)
        results = _dedupe_results(results)

        if results:
            await self.repository.save_results(results)

        return results

    async def search_episode(
        self,
        show: Show,
        season_number: int,
        episode_number: int,
        on_partial: OnPartial | None = None,
    ) -> list[IndexerQueryResult]:
        await self._ensure_initialized()
        queries = _query_variants(
            show.name, f" S{season_number:02d}E{episode_number:02d}"
        )

        extra = {"on_site_result": on_partial} if on_partial is not None else {}
        sem = asyncio.Semaphore(_INDEXER_FANOUT_LIMIT)

        async def _run_one(
            indexer: GenericIndexer, query: str
        ) -> list[IndexerQueryResult]:
            name = self._indexer_name(indexer)
            if not self._circuit_allows(name):
                log.info("Skipping %s: circuit breaker open", name)
                return []
            try:
                async with sem:
                    result = await asyncio.to_thread(
                        self._invoke_indexer_kwargs,
                        indexer.search_season,
                        {"query": query, "show": show, "season_number": season_number},
                        extra,
                        on_partial,
                        name,
                    )
                self._record_indexer_success(name)
            except Exception:
                self._record_indexer_failure(name)
                log.exception(
                    f"Indexer {indexer.__class__.__name__} failed for episode search '{query}'"
                )
                return []
            else:
                return result

        await self._release_before_fanout(on_partial)
        gathered = await asyncio.gather(
            *(_run_one(i, q) for q in queries for i in self.indexers)
        )
        results: list[IndexerQueryResult] = []
        for r in gathered:
            if r:
                results.extend(r)
        results = _dedupe_results(results)

        if results:
            await self.repository.save_results(results)

        return results
