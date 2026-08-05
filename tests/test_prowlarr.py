"""Tests for Prowlarr backend concurrency, caching, and search limits."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from requests import Response

from miramedia.indexers.backends.prowlarr import (
    _CAPABILITY_CACHE_TTL_SECONDS,
    _NEWZNAB_RESULT_LIMIT,
    _SEARCH_MAX_WORKERS,
    IndexerInfo,
    Prowlarr,
)

_MINIMAL_TORZNAB_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>{title}</title>
      <size>1048576</size>
      <enclosure url="http://example.com/{slug}.torrent"
                 type="application/x-bittorrent"
                 length="1048576"/>
      <torznab:attr name="seeders" value="10"/>
    </item>
  </channel>
</rss>
"""

_NETWORK_DOWN = "network down"
_INDEXER_FAILED = "indexer failed"


def _indexer_payload(
    *,
    indexer_id: int,
    name: str,
    tv: bool = True,
    movie: bool = False,
) -> dict:
    capabilities: dict = {}
    if tv:
        capabilities["tvSearchParams"] = ["q", "season"]
    if movie:
        capabilities["movieSearchParams"] = ["q"]
    return {
        "id": indexer_id,
        "name": name,
        "capabilities": capabilities,
    }


def _response_for(content: str | bytes, *, json_data: object | None = None) -> Response:
    response = Response()
    response.status_code = 200
    if json_data is not None:
        response._content = b"{}"
        response.json = MagicMock(return_value=json_data)
    else:
        response._content = content.encode() if isinstance(content, str) else content
    return response


def _make_indexer(
    *,
    indexer_id: int,
    name: str,
    tv: bool = True,
) -> IndexerInfo:
    return IndexerInfo(
        id=indexer_id,
        name=name,
        supports_tv_search=tv,
        supports_tv_search_tmdb=False,
        supports_tv_search_imdb=False,
        supports_tv_search_tvdb=False,
        supports_tv_search_season=True,
        supports_movie_search=False,
        supports_movie_search_tmdb=False,
        supports_movie_search_imdb=False,
        supports_movie_search_tvdb=False,
    )


def test_newznab_search_copies_params_and_sets_limit() -> None:
    backend = Prowlarr()
    original = {"q": "test", "t": "tvsearch"}
    captured: dict | None = None

    def _fake_api(path: str, parameters: dict | None = None) -> Response:
        nonlocal captured
        captured = parameters
        del path
        return _response_for(_MINIMAL_TORZNAB_XML.format(title="A", slug="a"))

    backend._call_prowlarr_api = _fake_api
    indexer = _make_indexer(indexer_id=1, name="A")

    backend._newznab_search(indexer=indexer, parameters=original)

    assert "limit" not in original
    assert captured is not None
    assert captured["limit"] == _NEWZNAB_RESULT_LIMIT
    assert captured["q"] == "test"


def test_search_preserves_indexer_order() -> None:
    backend = Prowlarr()

    def _fake_api(path: str, parameters: dict | None = None) -> Response:
        del parameters
        if path == "/indexer":
            return _response_for(
                "",
                json_data=[
                    _indexer_payload(indexer_id=1, name="Alpha"),
                    _indexer_payload(indexer_id=2, name="Beta"),
                ],
            )
        if path.endswith("/newznab"):
            slug = "alpha" if "/1/" in path else "beta"
            title = "Alpha release" if slug == "alpha" else "Beta release"
            return _response_for(_MINIMAL_TORZNAB_XML.format(title=title, slug=slug))
        raise AssertionError(path)

    backend._call_prowlarr_api = _fake_api
    backend._invalidate_indexer_cache()

    results = backend.search("query", is_tv=True)

    assert [r.title for r in results] == ["Alpha release", "Beta release"]


def test_capability_cache_hit_and_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = Prowlarr()
    calls = 0
    monotonic_values = [100.0, 100.0, 100.0, 200.0, 200.0]

    def _fake_monotonic() -> float:
        return monotonic_values.pop(0)

    monkeypatch.setattr(time, "monotonic", _fake_monotonic)

    def _fake_api(path: str, parameters: dict | None = None) -> Response:
        del parameters
        nonlocal calls
        if path == "/indexer":
            calls += 1
            return _response_for(
                "",
                json_data=[_indexer_payload(indexer_id=1, name="Alpha")],
            )
        raise AssertionError(path)

    backend._call_prowlarr_api = _fake_api
    backend._invalidate_indexer_cache()

    first = backend._get_indexers()
    second = backend._get_indexers()
    third = backend._get_indexers()

    assert len(first) == 1
    assert len(second) == 1
    assert len(third) == 1
    assert calls == 2


def test_failed_capability_fetch_does_not_poison_cache() -> None:
    backend = Prowlarr()
    calls = 0

    def _fake_api(path: str, parameters: dict | None = None) -> Response:
        del parameters
        nonlocal calls
        if path == "/indexer":
            calls += 1
            if calls == 1:
                raise RuntimeError(_NETWORK_DOWN)
            return _response_for(
                "",
                json_data=[_indexer_payload(indexer_id=1, name="Alpha")],
            )
        raise AssertionError(path)

    backend._call_prowlarr_api = _fake_api
    backend._invalidate_indexer_cache()

    with pytest.raises(RuntimeError, match=_NETWORK_DOWN):
        backend._get_indexers()

    indexers = backend._get_indexers()
    assert len(indexers) == 1
    assert calls == 2


def test_search_bounded_concurrency_with_overlap() -> None:
    backend = Prowlarr()
    hold = threading.Event()
    condition = threading.Condition()
    in_flight = 0
    peak = 0

    def _fake_api(path: str, parameters: dict | None = None) -> Response:
        del parameters
        if path == "/indexer":
            return _response_for(
                "",
                json_data=[
                    _indexer_payload(indexer_id=i, name=f"Idx{i}") for i in range(1, 7)
                ],
            )
        if path.endswith("/newznab"):
            with condition:
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                if in_flight >= 2:
                    condition.notify_all()
            hold.wait(timeout=5)
            with condition:
                in_flight -= 1
            slug = path.split("/")[2]
            return _response_for(
                _MINIMAL_TORZNAB_XML.format(title=f"title-{slug}", slug=slug)
            )
        raise AssertionError(path)

    backend._call_prowlarr_api = _fake_api
    backend._invalidate_indexer_cache()

    def _run_search() -> None:
        backend.search("query", is_tv=True)

    thread = threading.Thread(target=_run_search)
    with condition:
        thread.start()
        condition.wait(timeout=5)
    hold.set()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert peak <= _SEARCH_MAX_WORKERS
    assert peak >= 2


def test_search_propagates_indexer_failure() -> None:
    backend = Prowlarr()

    def _fake_api(path: str, parameters: dict | None = None) -> Response:
        del parameters
        if path == "/indexer":
            return _response_for(
                "",
                json_data=[
                    _indexer_payload(indexer_id=1, name="Alpha"),
                    _indexer_payload(indexer_id=2, name="Beta"),
                ],
            )
        if path.endswith("/newznab") and "/2/" in path:
            raise RuntimeError(_INDEXER_FAILED)
        if path.endswith("/newznab"):
            return _response_for(_MINIMAL_TORZNAB_XML.format(title="ok", slug="ok"))
        raise AssertionError(path)

    backend._call_prowlarr_api = _fake_api
    backend._invalidate_indexer_cache()

    with pytest.raises(RuntimeError, match=_INDEXER_FAILED):
        backend.search("query", is_tv=True)


def test_cache_ttl_constant_is_short() -> None:
    assert _CAPABILITY_CACHE_TTL_SECONDS <= 60
