"""In-process TTL cache for metadata provider lookups.

Library scans + scheduled metadata refreshes can hammer external APIs
with the same `(provider, provider_id)` repeatedly. This module exposes
a small set of TTLCaches keyed by call shape, plus a `cached` decorator
factory so providers can opt in per-method without changing call sites.

Cache is process-local; accessed from the event-loop thread and
``asyncio.to_thread`` worker threads. All TTLCache container access goes
through ``_lock``. Eviction is LRU + TTL via cachetools.
"""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, TypeVar

from cachetools import TTLCache

T = TypeVar("T")

_DEFAULT_TTL = int(
    os.getenv("MIRAMEDIA_METADATA_CACHE_TTL_SECONDS", str(60 * 60 * 24))
)  # 24h
# Conservative default — a single cached Show/Movie payload is ~500KB once
# season/episode embeds are materialised, so 1024 entries ≈ ~500MB worst
# case. On a NAS deployment that's the limit we want to budget before
# eviction kicks in. Operators can raise via env if they have RAM to spare.
_DEFAULT_MAXSIZE = int(os.getenv("MIRAMEDIA_METADATA_CACHE_MAXSIZE", "1024"))

# Separate caches per call shape so a `get_show` and `search_show("foo")`
# don't collide. Keyed by a tuple of (method_name, *args).
_caches: dict[str, TTLCache] = {}
_lock = threading.Lock()


@dataclass
class CacheStats:
    """Per-cache hit/miss/set counters.

    Integer ``+= 1`` is GIL-atomic under CPython, so we deliberately skip
    locking — being off by a handful under extreme concurrency is fine for
    observability and avoids contention in a hot path.
    """

    hits: int = 0
    misses: int = 0
    sets: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


_stats: dict[str, CacheStats] = {}


def _get_stats(name: str) -> CacheStats:
    # ``setdefault`` is GIL-atomic on a dict, so two concurrent callers can't
    # both win — the loser discards its fresh ``CacheStats`` and reads the
    # winner's row.
    stats = _stats.get(name)
    if stats is None:
        stats = _stats.setdefault(name, CacheStats())
    return stats


def _get_cache(name: str, ttl: int | None = None) -> TTLCache:
    # See ``_get_stats`` — same setdefault race fix. The discarded fresh
    # TTLCache is cheap (empty) so the cost of losing the race is negligible.
    cache = _caches.get(name)
    if cache is None:
        new = TTLCache(
            maxsize=_DEFAULT_MAXSIZE,
            ttl=ttl if ttl is not None else _DEFAULT_TTL,
        )
        cache = _caches.setdefault(name, new)
    # Ensure a stats row exists alongside every cache so get_all_cache_stats
    # reports zero-traffic caches too (handy for spotting unused decorators).
    _get_stats(name)
    return cache


def cached(
    name: str, ttl: int | None = None
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate a sync or async method on a metadata provider.

    Key = (method args tuple). Self is excluded so multiple instances of
    the same provider share results.

    Negative results (None / [] / raised exceptions) are NOT cached so a
    transient outage doesn't poison the entry.
    """
    cache = _get_cache(name, ttl=ttl)
    stats = _get_stats(name)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        is_coro = asyncio.iscoroutinefunction(fn)

        if is_coro:

            @wraps(fn)
            async def awrapper(self: object, *args: object, **kwargs: object) -> Any:  # noqa: ANN401 — wraps arbitrary provider methods with dynamic return types
                key = (args, tuple(sorted(kwargs.items())))
                with _lock:
                    hit = cache.get(key)
                if hit is not None:
                    stats.hits += 1
                    return hit
                stats.misses += 1
                result = await fn(self, *args, **kwargs)
                if result:
                    with _lock:
                        cache[key] = result
                    stats.sets += 1
                return result

            return awrapper

        @wraps(fn)
        def swrapper(self: object, *args: object, **kwargs: object) -> Any:  # noqa: ANN401 — wraps arbitrary provider methods with dynamic return types
            key = (args, tuple(sorted(kwargs.items())))
            with _lock:
                hit = cache.get(key)
            if hit is not None:
                stats.hits += 1
                return hit
            stats.misses += 1
            result = fn(self, *args, **kwargs)
            if result:
                with _lock:
                    cache[key] = result
                stats.sets += 1
            return result

        return swrapper

    return decorator


def get_all_cache_stats() -> dict[str, dict[str, int | float]]:
    """Snapshot of every cache's hit/miss counters. Used by /health detail."""
    result: dict[str, dict[str, int | float]] = {}
    for name, s in _stats.items():
        with _lock:
            size = len(_caches.get(name, ()))
        result[name] = {
            "hits": s.hits,
            "misses": s.misses,
            "sets": s.sets,
            "hit_rate": round(s.hit_rate, 4),
            "size": size,
        }
    return result


def invalidate_all() -> None:
    """Drop every cache entry. Use after a user-triggered force-refresh."""
    with _lock:
        for c in _caches.values():
            c.clear()


def invalidate(name: str) -> None:
    with _lock:
        if name in _caches:
            _caches[name].clear()


__all__ = [
    "CacheStats",
    "cached",
    "get_all_cache_stats",
    "invalidate",
    "invalidate_all",
]
