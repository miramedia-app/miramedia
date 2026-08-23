"""Bounded TTL cache with single-flight coalescing for /recommended discovery."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import TypeVar

from cachetools import TTLCache

from miramedia.metadata.schemas import MetaDataProviderSearchResult

T = TypeVar("T", bound=Hashable)

_RECOMMENDED_TTL_SECONDS = 3600.0  # 1h — trending shifts daily, not per page refresh.
# Browse infinite scroll uses skip offsets 0, 10, 20, … (PAGE_SIZE=10); cap distinct pages.
_RECOMMENDED_MAX_PAGES = 8


class RecommendedDiscoveryCache:
    """Process-local cache for provider discovery payloads on /recommended."""

    def __init__(
        self,
        maxsize: int,
        ttl: float,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl, timer=timer)
        self._lock = threading.Lock()
        self._in_flight: dict[
            Hashable, asyncio.Task[list[MetaDataProviderSearchResult]]
        ] = {}
        self._flight_guard = asyncio.Lock()

    async def get(
        self,
        key: Hashable,
        discover: Callable[[], Awaitable[list[MetaDataProviderSearchResult]]],
        annotate: Callable[
            [list[MetaDataProviderSearchResult]],
            Awaitable[list[MetaDataProviderSearchResult]],
        ],
    ) -> list[MetaDataProviderSearchResult]:
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return await annotate(cached)

        async with self._flight_guard:
            task = self._in_flight.get(key)
            if task is None:
                task = asyncio.create_task(self._fetch_and_store(key, discover))
                self._in_flight[key] = task

        results = await asyncio.shield(task)
        return await annotate(results)

    async def _fetch_and_store(
        self,
        key: Hashable,
        discover: Callable[[], Awaitable[list[MetaDataProviderSearchResult]]],
    ) -> list[MetaDataProviderSearchResult]:
        try:
            results = await discover()
            with self._lock:
                self._cache[key] = results
            return results
        finally:
            async with self._flight_guard:
                self._in_flight.pop(key, None)


_RECOMMENDED_MOVIES_CACHE = RecommendedDiscoveryCache(
    maxsize=_RECOMMENDED_MAX_PAGES, ttl=_RECOMMENDED_TTL_SECONDS
)
_RECOMMENDED_SHOWS_CACHE = RecommendedDiscoveryCache(
    maxsize=_RECOMMENDED_MAX_PAGES, ttl=_RECOMMENDED_TTL_SECONDS
)

__all__ = [
    "_RECOMMENDED_MAX_PAGES",
    "_RECOMMENDED_MOVIES_CACHE",
    "_RECOMMENDED_SHOWS_CACHE",
    "_RECOMMENDED_TTL_SECONDS",
    "RecommendedDiscoveryCache",
]
