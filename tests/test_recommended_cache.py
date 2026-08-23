"""Tests for bounded /recommended discovery cache + single-flight coalescing."""

from __future__ import annotations

import asyncio

import pytest

from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.recommended_discovery_cache import RecommendedDiscoveryCache


def _result(name: str = "title") -> MetaDataProviderSearchResult:
    return MetaDataProviderSearchResult(
        poster_path=None,
        overview=None,
        name=name,
        external_id=f"ext-{name}",
        year=2020,
        metadata_provider="tmdb",
        added=False,
    )


@pytest.mark.anyio
async def test_max_size_eviction() -> None:
    cache = RecommendedDiscoveryCache(maxsize=3, ttl=60.0)
    calls: list[int] = []

    async def discover(skip: int) -> list[MetaDataProviderSearchResult]:
        calls.append(skip)
        return [_result(f"page-{skip}")]

    async def annotate(
        results: list[MetaDataProviderSearchResult],
    ) -> list[MetaDataProviderSearchResult]:
        return [r.model_copy() for r in results]

    for skip in (0, 10, 20):
        await cache.get(skip, lambda s=skip: discover(s), annotate)

    assert len(cache._cache) == 3
    await cache.get(30, lambda: discover(30), annotate)
    assert 30 in calls
    assert 0 not in cache._cache

    await cache.get(0, lambda: discover(0), annotate)
    assert calls.count(0) == 2


@pytest.mark.anyio
async def test_expired_entry_removed() -> None:
    clock = {"t": 0.0}

    def timer() -> float:
        return clock["t"]

    cache = RecommendedDiscoveryCache(maxsize=4, ttl=0.05, timer=timer)
    calls = 0

    async def discover() -> list[MetaDataProviderSearchResult]:
        nonlocal calls
        calls += 1
        return [_result("fresh")]

    async def annotate(
        results: list[MetaDataProviderSearchResult],
    ) -> list[MetaDataProviderSearchResult]:
        return results

    await cache.get("key", discover, annotate)
    clock["t"] += 0.06
    await cache.get("key", discover, annotate)
    assert calls == 2


@pytest.mark.anyio
async def test_single_flight_same_key() -> None:
    cache = RecommendedDiscoveryCache(maxsize=4, ttl=60.0)
    calls = 0
    entered = asyncio.Event()

    async def discover() -> list[MetaDataProviderSearchResult]:
        nonlocal calls
        calls += 1
        entered.set()
        await asyncio.sleep(0.05)
        return [_result("coalesced")]

    async def annotate(
        results: list[MetaDataProviderSearchResult],
    ) -> list[MetaDataProviderSearchResult]:
        return results

    first = asyncio.create_task(cache.get("same", discover, annotate))
    await entered.wait()
    second = asyncio.create_task(cache.get("same", discover, annotate))
    results = await asyncio.gather(first, second)

    assert calls == 1
    assert results[0][0].name == "coalesced"
    assert results[1][0].name == "coalesced"


@pytest.mark.anyio
async def test_waiter_cancellation_does_not_kill_flight() -> None:
    cache = RecommendedDiscoveryCache(maxsize=4, ttl=60.0)
    calls = 0
    entered = asyncio.Event()
    gate = asyncio.Event()

    async def discover() -> list[MetaDataProviderSearchResult]:
        nonlocal calls
        calls += 1
        entered.set()
        await gate.wait()
        return [_result("shielded")]

    async def annotate(
        results: list[MetaDataProviderSearchResult],
    ) -> list[MetaDataProviderSearchResult]:
        return results

    first = asyncio.create_task(cache.get("shield", discover, annotate))
    await entered.wait()
    second = asyncio.create_task(cache.get("shield", discover, annotate))

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    gate.set()
    results = await second

    assert calls == 1
    assert results[0].name == "shielded"
    assert "shield" in cache._cache


@pytest.mark.anyio
async def test_independent_keys_fetch_separately() -> None:
    cache = RecommendedDiscoveryCache(maxsize=4, ttl=60.0)
    calls: list[str] = []

    async def discover(key: str) -> list[MetaDataProviderSearchResult]:
        calls.append(key)
        return [_result(key)]

    async def annotate(
        results: list[MetaDataProviderSearchResult],
    ) -> list[MetaDataProviderSearchResult]:
        return results

    await asyncio.gather(
        cache.get("a", lambda: discover("a"), annotate),
        cache.get("b", lambda: discover("b"), annotate),
    )

    assert sorted(calls) == ["a", "b"]


@pytest.mark.anyio
async def test_failed_calls_not_cached_and_retry() -> None:
    cache = RecommendedDiscoveryCache(maxsize=4, ttl=60.0)
    calls = 0

    async def discover() -> list[MetaDataProviderSearchResult]:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "provider down"
            raise RuntimeError(msg)
        return [_result("ok")]

    async def annotate(
        results: list[MetaDataProviderSearchResult],
    ) -> list[MetaDataProviderSearchResult]:
        return results

    with pytest.raises(RuntimeError, match="provider down"):
        await cache.get("fail", discover, annotate)

    assert "fail" not in cache._cache
    assert len(cache._in_flight) == 0

    out = await cache.get("fail", discover, annotate)
    assert calls == 2
    assert out[0].name == "ok"


@pytest.mark.anyio
async def test_annotate_runs_on_cache_hit() -> None:
    cache = RecommendedDiscoveryCache(maxsize=4, ttl=60.0)
    discover_calls = 0
    annotate_calls = 0

    async def discover() -> list[MetaDataProviderSearchResult]:
        nonlocal discover_calls
        discover_calls += 1
        return [_result("cached")]

    async def annotate(
        results: list[MetaDataProviderSearchResult],
    ) -> list[MetaDataProviderSearchResult]:
        nonlocal annotate_calls
        annotate_calls += 1
        return [r.model_copy(update={"added": True}) for r in results]

    key = "hit"
    first = await cache.get(key, discover, annotate)
    second = await cache.get(key, discover, annotate)

    assert discover_calls == 1
    assert annotate_calls == 2
    assert first[0].added is True
    assert second[0].added is True


@pytest.mark.anyio
async def test_cache_payload_untouched_by_annotation() -> None:
    cache = RecommendedDiscoveryCache(maxsize=4, ttl=60.0)

    async def discover() -> list[MetaDataProviderSearchResult]:
        return [_result("pristine")]

    async def annotate(
        results: list[MetaDataProviderSearchResult],
    ) -> list[MetaDataProviderSearchResult]:
        return [r.model_copy(update={"added": True}) for r in results]

    key = "isolate"
    out = await cache.get(key, discover, annotate)
    assert out[0].added is True
    assert cache._cache[key][0].added is False

    out2 = await cache.get(key, discover, annotate)
    assert out2[0].added is True
    assert cache._cache[key][0].added is False


@pytest.mark.anyio
async def test_no_leaked_in_flight_after_failure() -> None:
    cache = RecommendedDiscoveryCache(maxsize=4, ttl=60.0)
    entered = asyncio.Event()

    async def discover() -> list[MetaDataProviderSearchResult]:
        entered.set()
        await asyncio.sleep(0.02)
        msg = "boom"
        raise ValueError(msg)

    async def annotate(
        results: list[MetaDataProviderSearchResult],
    ) -> list[MetaDataProviderSearchResult]:
        return results

    task = asyncio.create_task(cache.get("leak", discover, annotate))
    await entered.wait()
    follower = asyncio.create_task(cache.get("leak", discover, annotate))

    results = await asyncio.gather(task, follower, return_exceptions=True)
    assert all(isinstance(r, ValueError) for r in results)
    assert len(cache._in_flight) == 0
