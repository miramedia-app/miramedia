"""
Native indexer — replaces Prowlarr/Jackett with direct site queries.

Preloaded with popular public indexer sites and supports custom Torznab
endpoints. Optionally integrates an in-process Cloudflare bypass.
"""

import asyncio
import concurrent.futures
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from miramedia.cloudflare import get_cloudflare_bypass
from miramedia.config import MiraMediaConfig
from miramedia.indexers.backends.generic import GenericIndexer
from miramedia.indexers.config import TorznabSiteConfig
from miramedia.indexers.schemas import IndexerQueryResult, IndexerSiteRead
from miramedia.indexers.sites import get_preloaded_sites
from miramedia.indexers.sites.base import BaseSite
from miramedia.indexers.sites.torznab_site import TorznabSite
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)

# Callback fired with ``(site_name, results)`` as each site completes.
OnSiteResult = Callable[[str, list[IndexerQueryResult]], None]

_cached_indexer: "NativeIndexer | None" = None


def get_native_indexer(db_sites: list[IndexerSiteRead]) -> "NativeIndexer":
    """Return the cached NativeIndexer singleton, creating it if needed."""
    global _cached_indexer
    if _cached_indexer is None:
        _cached_indexer = NativeIndexer(db_sites=db_sites)
    try:
        _cached_indexer._loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    return _cached_indexer


def invalidate_native_indexer() -> None:
    """Clear the cached NativeIndexer so it's rebuilt on next use."""
    global _cached_indexer
    _cached_indexer = None


class NativeIndexer(GenericIndexer):
    """
    A native indexer that queries torrent sites directly, without needing
    external tools like Prowlarr or Jackett.
    """

    def __init__(self, db_sites: list[IndexerSiteRead]) -> None:
        super().__init__(name="native")
        self._loop: asyncio.AbstractEventLoop | None = None
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        indexers_cfg = MiraMediaConfig().indexers
        config = indexers_cfg.native
        timeout_seconds = indexers_cfg.timeout_seconds

        bypass = get_cloudflare_bypass()

        self.max_workers = config.max_concurrent_searches
        # Backstop for the parallel fan-out: a single wedged site worker (e.g. a
        # CF solve that never returns) must not pin the whole search forever.
        # Generous by default so a legitimately-slow CF solve isn't dropped —
        # this only catches a true wedge. Env-tunable for slow NAS boxes.
        self._fanout_timeout = float(
            os.getenv("MIRAMEDIA_INDEXER_FANOUT_TIMEOUT", "300")
        )
        self.sites: list[BaseSite] = []

        # Registry of preloaded site classes
        preloaded_registry = get_preloaded_sites()

        for site_record in db_sites:
            if not site_record.enabled:
                log.debug(
                    "Native indexer: skipping disabled site '%s'",
                    site_record.name,
                )
                continue

            if site_record.site_type == "native":
                site_cls = preloaded_registry.get(site_record.name)
                if site_cls:
                    instance = site_cls(bypass=bypass, timeout=timeout_seconds)
                    # Override the class-level URL with the DB's active URL
                    instance.url = site_record.url
                    # Attach the DB id so search hooks can stamp last_success_at
                    instance.site_id = site_record.id  # type: ignore[attr-defined]
                    self.sites.append(instance)
                    log.debug(
                        "Native indexer: loaded site '%s' (%s)",
                        site_record.name,
                        site_record.url,
                    )
                else:
                    log.warning(
                        "Native indexer: unknown preloaded site '%s', skipping",
                        site_record.name,
                    )
            elif site_record.site_type == "torznab":
                site = TorznabSite(
                    config=TorznabSiteConfig(
                        name=site_record.name,
                        url=site_record.url,
                        api_key=site_record.api_key,
                        supports_tv=site_record.supports_tv,
                        supports_movies=site_record.supports_movies,
                        categories_tv=site_record.categories_tv,
                        categories_movies=site_record.categories_movies,
                        cloudflare_protected=site_record.cloudflare_protected,
                    ),
                    bypass=bypass,
                    timeout=timeout_seconds,
                )
                site.site_id = site_record.id  # type: ignore[attr-defined]
                self.sites.append(site)
                log.debug(
                    "Native indexer: loaded custom Torznab site '%s'",
                    site_record.name,
                )

        site_names = [s.name for s in self.sites]
        log.info(
            "Native indexer initialized with %s sites: %s",
            len(self.sites),
            site_names,
        )

    def _search_parallel(
        self,
        sites: list[BaseSite],
        search_fn: str,
        *args: object,
        on_site_result: OnSiteResult | None = None,
    ) -> list[IndexerQueryResult]:
        """
        Run a search method across multiple sites in parallel.

        :param sites: Sites to search.
        :param search_fn: Method name to call on each site.
        :param args: Arguments to pass to the method.
        :param on_site_result: Optional callback ``(site_name, results)`` fired
            as each site completes — used by the SSE streaming endpoint so the
            UI can render partial results without waiting for the slowest site.
        """
        results: list[IndexerQueryResult] = []
        futures = {}

        # Not a `with` block: the context manager's __exit__ calls
        # shutdown(wait=True), which would block on a wedged worker even after
        # the as_completed timeout fires. Manage it manually so a hung site
        # can't pin the whole search — return partial results and let the stray
        # thread finish in the background.
        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        site_ids_with_results: list = []
        try:
            for site in sites:
                method = getattr(site, search_fn)
                future = executor.submit(method, *args)
                futures[future] = (site.name, getattr(site, "site_id", None))

            try:
                completed = concurrent.futures.as_completed(
                    futures, timeout=self._fanout_timeout
                )
                for future in completed:
                    site_name, site_id = futures[future]
                    try:
                        site_results = future.result()
                        if site_results:
                            results.extend(site_results)
                            if site_id is not None:
                                site_ids_with_results.append(site_id)
                        if on_site_result is not None:
                            try:
                                on_site_result(site_name, site_results or [])
                            except Exception:
                                log.exception(
                                    "on_site_result callback failed for %s", site_name
                                )
                    except Exception:
                        log.exception(
                            "Native indexer: search failed for site '%s'",
                            site_name,
                        )
                        if on_site_result is not None:
                            try:
                                on_site_result(site_name, [])
                            except Exception:
                                log.exception(
                                    "on_site_result callback failed for %s", site_name
                                )
            except concurrent.futures.TimeoutError:
                # One or more sites exceeded the backstop. Report them as empty
                # so the SSE stream completes, and drop their results.
                for f, (site_name, _sid) in futures.items():
                    if not f.done():
                        log.warning(
                            "Native indexer: site '%s' exceeded %.0fs fan-out "
                            "budget; dropping",
                            site_name,
                            self._fanout_timeout,
                        )
                        if on_site_result is not None:
                            try:
                                on_site_result(site_name, [])
                            except Exception:
                                log.exception(
                                    "on_site_result callback failed for %s", site_name
                                )
        finally:
            # wait=False so a wedged worker can't block; cancel_futures kills any
            # that never started.
            executor.shutdown(wait=False, cancel_futures=True)

        # Stamp ``last_success_at`` for sites that returned results. Done via
        # a sync-friendly helper that schedules an async DB write on the
        # running loop — the search itself runs inside a ThreadPoolExecutor
        # so we can't ``await`` directly here.
        if site_ids_with_results:
            self._record_successes_threadsafe(site_ids_with_results)
        return results

    def _record_successes_threadsafe(self, site_ids: list) -> None:
        """Best-effort: stamp success timestamps on a background loop.

        Search runs in sync worker threads; the project's DB session is async.
        Schedule the write back on the main event loop so it doesn't hold up
        the search response. Silently no-ops if no loop is running (CLI use).
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            log.debug("No event loop captured; skipping site success stamps")
            return

        async def _do_record() -> None:
            try:
                from miramedia.database import SessionLocalBackground
                from miramedia.indexers.repository import IndexerRepository

                if SessionLocalBackground is None:
                    return
                async with SessionLocalBackground() as db:
                    repo = IndexerRepository(db)
                    for sid in set(site_ids):
                        await repo.record_site_success(sid)
            except Exception:
                log.exception("Failed to record site success timestamps")

        try:
            asyncio.run_coroutine_threadsafe(_do_record(), loop)
        except Exception:
            log.exception("Failed to schedule site success recording")

    def search(
        self, query: str, is_tv: bool, on_site_result: OnSiteResult | None = None
    ) -> list[IndexerQueryResult]:
        category = "tv" if is_tv else "movies"
        sites = [
            s
            for s in self.sites
            if (is_tv and s.supports_tv) or (not is_tv and s.supports_movies)
        ]
        return self._search_parallel(
            sites, "search", query, category, on_site_result=on_site_result
        )

    def search_season(
        self,
        query: str,
        show: Show,
        season_number: int,
        on_site_result: OnSiteResult | None = None,
    ) -> list[IndexerQueryResult]:
        sites = [s for s in self.sites if s.supports_tv]
        return self._search_parallel(
            sites,
            "search_show",
            query,
            show,
            season_number,
            on_site_result=on_site_result,
        )

    def search_movie(
        self, query: str, movie: Movie, on_site_result: OnSiteResult | None = None
    ) -> list[IndexerQueryResult]:
        sites = [s for s in self.sites if s.supports_movies]
        return self._search_parallel(
            sites, "search_movie", query, movie, on_site_result=on_site_result
        )
