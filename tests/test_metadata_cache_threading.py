"""Thread-safety and behavioral regression tests for metadata TTL caches."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

import miramedia.metadata.cache as cache_mod
from miramedia.metadata.cache import cached, invalidate, invalidate_all


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    invalidate_all()


class _Dummy:
    def __init__(self) -> None:
        self.calls = 0

    @cached("t1")
    def lookup(self, key: str) -> str | None:
        self.calls += 1
        if key == "empty":
            return None
        return f"value:{key}"


def test_cached_sync_hit_miss_and_invalidate() -> None:
    obj = _Dummy()

    assert obj.lookup("a") == "value:a"
    assert obj.calls == 1

    assert obj.lookup("a") == "value:a"
    assert obj.calls == 1

    assert obj.lookup("empty") is None
    assert obj.calls == 2
    assert obj.lookup("empty") is None
    assert obj.calls == 3

    invalidate("t1")
    assert obj.lookup("a") == "value:a"
    assert obj.calls == 4


def test_concurrent_eviction_churn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_mod, "_DEFAULT_MAXSIZE", 8)

    class _Provider:
        @cached("t2", ttl=60)
        def compute(self, key: int) -> str:
            return f"out:{key}"

    provider = _Provider()
    expected = {i: f"out:{i}" for i in range(2000)}

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(provider.compute, i): i for i in range(2000)}
        for fut in as_completed(futures):
            i = futures[fut]
            assert fut.result() == expected[i]
