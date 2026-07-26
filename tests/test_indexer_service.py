"""DB-free unit tests for indexer circuit breaker, dedupe, and query variants."""

from __future__ import annotations

import pytest

from miramedia.indexers import service as indexer_service
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.indexers.service import IndexerService, _dedupe_results, _query_variants
from miramedia.indexers.utils import sanitize_search_query, search_name_variants


@pytest.fixture(autouse=True)
def _clean_breaker_state():
    indexer_service._BREAKER_FAILURES.clear()
    yield
    indexer_service._BREAKER_FAILURES.clear()


@pytest.fixture
def fake_clock(monkeypatch):
    state = {"now": 1000.0}
    monkeypatch.setattr(indexer_service.time, "monotonic", lambda: state["now"])
    return state


def _result(download_url: str, *, title: str = "release") -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url=download_url,
        seeders=10,
        flags=[],
        size=1_000_000,
        usenet=False,
        age=1,
        indexer="test",
    )


def test_circuit_allows_fresh_indexer() -> None:
    assert IndexerService._circuit_allows("Prowlarr") is True


def test_circuit_allows_below_threshold(monkeypatch) -> None:
    monkeypatch.setattr(indexer_service, "_BREAKER_THRESHOLD", 3)
    IndexerService._record_indexer_failure("Prowlarr")
    IndexerService._record_indexer_failure("Prowlarr")
    assert IndexerService._circuit_allows("Prowlarr") is True


def test_circuit_disallows_at_threshold_within_cooldown(monkeypatch) -> None:
    monkeypatch.setattr(indexer_service, "_BREAKER_THRESHOLD", 3)
    monkeypatch.setattr(indexer_service, "_BREAKER_COOLDOWN", 300)
    for _ in range(3):
        IndexerService._record_indexer_failure("Prowlarr")
    assert IndexerService._circuit_allows("Prowlarr") is False


def test_circuit_allows_after_cooldown_and_clears_state(
    monkeypatch, fake_clock
) -> None:
    monkeypatch.setattr(indexer_service, "_BREAKER_THRESHOLD", 3)
    monkeypatch.setattr(indexer_service, "_BREAKER_COOLDOWN", 300)
    for _ in range(3):
        IndexerService._record_indexer_failure("Prowlarr")
    fake_clock["now"] = 1000.0 + 300.0
    assert IndexerService._circuit_allows("Prowlarr") is True
    assert "Prowlarr" not in indexer_service._BREAKER_FAILURES


def test_success_clears_accumulated_failures(monkeypatch) -> None:
    monkeypatch.setattr(indexer_service, "_BREAKER_THRESHOLD", 3)
    IndexerService._record_indexer_failure("Prowlarr")
    IndexerService._record_indexer_failure("Prowlarr")
    IndexerService._record_indexer_success("Prowlarr")
    IndexerService._record_indexer_failure("Prowlarr")
    IndexerService._record_indexer_failure("Prowlarr")
    assert IndexerService._circuit_allows("Prowlarr") is True


@pytest.mark.usefixtures("fake_clock")
def test_circuit_breakers_are_per_indexer(monkeypatch) -> None:
    monkeypatch.setattr(indexer_service, "_BREAKER_THRESHOLD", 3)
    monkeypatch.setattr(indexer_service, "_BREAKER_COOLDOWN", 300)
    for _ in range(3):
        IndexerService._record_indexer_failure("Prowlarr")
    assert IndexerService._circuit_allows("Prowlarr") is False
    assert IndexerService._circuit_allows("Jackett") is True


@pytest.mark.usefixtures("fake_clock")
def test_failures_past_threshold_keep_circuit_open(monkeypatch) -> None:
    monkeypatch.setattr(indexer_service, "_BREAKER_THRESHOLD", 3)
    monkeypatch.setattr(indexer_service, "_BREAKER_COOLDOWN", 300)
    for _ in range(5):
        IndexerService._record_indexer_failure("Prowlarr")
    assert IndexerService._circuit_allows("Prowlarr") is False


def test_dedupe_results_keeps_first_of_duplicate_urls() -> None:
    first = _result("magnet:?xt=urn:btih:aaa", title="first")
    second = _result("magnet:?xt=urn:btih:aaa", title="second")
    third = _result("magnet:?xt=urn:btih:bbb", title="third")
    deduped = _dedupe_results([first, second, third])
    assert deduped == [first, third]


def test_dedupe_results_preserves_unique_input() -> None:
    results = [
        _result("magnet:?xt=urn:btih:aaa"),
        _result("magnet:?xt=urn:btih:bbb"),
    ]
    assert _dedupe_results(results) == results


def test_dedupe_results_empty_list() -> None:
    assert _dedupe_results([]) == []


def test_query_variants_colon_subtitle() -> None:
    name = "The Agency: Central Intelligence"
    expected = []
    for variant in search_name_variants(name):
        query = sanitize_search_query(variant)
        if query and query not in expected:
            expected.append(query)
    assert _query_variants(name) == expected
    assert expected == ["The Agency Central Intelligence", "The Agency"]


def test_query_variants_suffix_appended_before_sanitization() -> None:
    name = "The Agency: Central Intelligence"
    suffix = " S01E01"
    expected = []
    for variant in search_name_variants(name):
        query = sanitize_search_query(f"{variant}{suffix}")
        if query and query not in expected:
            expected.append(query)
    assert _query_variants(name, suffix=suffix) == expected
    assert expected == [
        "The Agency Central Intelligence S01E01",
        "The Agency S01E01",
    ]


def test_query_variants_excludes_empty_sanitized_queries() -> None:
    assert _query_variants("!!!") == []
