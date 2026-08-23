"""TVDB detail fan-out: request counts, ordering, and partial failures."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pytest

from miramedia.metadata.backends.tvdb import TvdbMetadataProvider
from miramedia.metadata.cache import invalidate_all


@pytest.fixture(autouse=True)
def _clear_metadata_cache() -> None:
    invalidate_all()
    yield
    invalidate_all()


def _tvdb_provider() -> TvdbMetadataProvider:
    return TvdbMetadataProvider.__new__(TvdbMetadataProvider)


def _movie_search_hit(
    *,
    tvdb_id: int,
    name: str,
    year: str = "2010",
    overview: str = "A test movie.",
    image_url: str = "https://artworks.thetvdb.com/banners/test.jpg",
) -> dict[str, Any]:
    return {
        "type": "movie",
        "name": name,
        "tvdb_id": tvdb_id,
        "year": year,
        "overview": overview,
        "image_url": image_url,
    }


def _trending_movie_hit(
    *,
    movie_id: int,
    name: str,
    year: str = "2010",
    image: str = "/banners/test.jpg",
) -> dict[str, Any]:
    return {
        "id": movie_id,
        "name": name,
        "year": year,
        "image": image,
    }


class _CountingDelayedClient:
    """Fake TVDB client that sleeps per call and records request names."""

    def __init__(self, delay_seconds: float = 0.01) -> None:
        self.delay_seconds = delay_seconds
        self.calls: list[str] = []

    def _sleep(self, name: str) -> None:
        self.calls.append(name)
        time.sleep(self.delay_seconds)

    def search(self, query: str, **_kwargs: object) -> list[dict[str, Any]]:
        self._sleep("search")
        count = int(query.removeprefix("movies:"))
        return [
            _movie_search_hit(tvdb_id=1000 + i, name=f"Movie {i}") for i in range(count)
        ]

    def get_movie_extended(self, movie_id: int, **_kwargs: object) -> dict[str, Any]:
        self._sleep(f"get_movie_extended:{movie_id}")
        return {
            "id": movie_id,
            "name": f"Movie {movie_id}",
            "year": "2010",
            "overview": "Extended overview.",
            "image_url": "https://artworks.thetvdb.com/banners/extended.jpg",
        }

    def get_all_movies(self, **_kwargs: object) -> list[dict[str, Any]]:
        self._sleep("get_all_movies")
        return [
            _trending_movie_hit(movie_id=2000 + i, name=f"Trending {i}")
            for i in range(20)
        ]

    def get_series_extended(self, show_id: int, **_kwargs: object) -> dict[str, Any]:
        self._sleep(f"get_series_extended:{show_id}")
        season_count = show_id
        return {
            "id": show_id,
            "name": f"Show {show_id}",
            "overview": "Show overview.",
            "year": "2008",
            "remoteIds": [],
            "genres": [],
            "characters": [],
            "seasons": [{"id": 5000 + i} for i in range(season_count)],
        }

    def get_season_extended(self, season_id: int, **_kwargs: object) -> dict[str, Any]:
        self._sleep(f"get_season_extended:{season_id}")
        return {
            "id": season_id,
            "number": season_id - 5000 + 1,
            "type": {"id": 1},
            "episodes": [
                {
                    "number": 1,
                    "name": "Pilot",
                    "aired": "2008-01-20",
                }
            ],
        }


def _attach_client(
    provider: TvdbMetadataProvider, client: _CountingDelayedClient
) -> None:
    provider.client = client


def _elapsed_for(fn: Callable[[], object]) -> tuple[float, object]:
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


@pytest.mark.parametrize("movie_count", [1, 5, 20])
def test_search_movie_query_avoids_detail_fanout(
    monkeypatch: pytest.MonkeyPatch, movie_count: int
) -> None:
    provider = _tvdb_provider()
    client = _CountingDelayedClient(delay_seconds=0.01)
    _attach_client(provider, client)
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__search",
        lambda **_k: client.search(f"movies:{movie_count}"),
    )

    elapsed, results = _elapsed_for(lambda: provider.search_movie("inception"))

    assert len(results) == movie_count
    assert client.calls == ["search"]
    # Serial fan-out would sleep once for search plus once per movie detail.
    assert elapsed < (movie_count + 1) * client.delay_seconds


def test_search_movie_trending_avoids_detail_fanout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _tvdb_provider()
    client = _CountingDelayedClient(delay_seconds=0.01)
    _attach_client(provider, client)
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__get_trending_movies",
        client.get_all_movies,
    )

    elapsed, results = _elapsed_for(lambda: provider.search_movie(None))

    assert len(results) == 20
    assert client.calls == ["get_all_movies"]
    # Serial fan-out would sleep once for the list plus once per movie detail.
    assert elapsed < 21 * client.delay_seconds


def test_search_movie_query_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _tvdb_provider()
    search_results = [
        _movie_search_hit(tvdb_id=3, name="Third"),
        _movie_search_hit(tvdb_id=1, name="First"),
        _movie_search_hit(tvdb_id=2, name="Second"),
    ]
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__search",
        lambda *_a, **_k: search_results,
    )

    results = provider.search_movie("ordered")

    assert [r.external_id for r in results] == ["3", "1", "2"]
    assert [r.name for r in results] == ["Third", "First", "Second"]


def test_search_movie_query_skips_non_movies_and_bad_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _tvdb_provider()
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__search",
        lambda *_a, **_k: [
            _movie_search_hit(tvdb_id=1, name="Good"),
            {"type": "series", "name": "Skip me", "tvdb_id": 99, "year": "2020"},
            {"type": "movie", "tvdb_id": 2},  # missing name -> skipped
            _movie_search_hit(tvdb_id=3, name="Also good"),
        ],
    )

    results = provider.search_movie("partial")

    assert len(results) == 2
    assert [r.external_id for r in results] == ["1", "3"]


def test_search_movie_query_maps_search_payload_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _tvdb_provider()
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__search",
        lambda *_a, **_k: [
            _movie_search_hit(
                tvdb_id=42,
                name="Inception",
                year="2010",
                overview="Dreams.",
                image_url="https://artworks.thetvdb.com/banners/inception.jpg",
            )
        ],
    )

    results = provider.search_movie("inception")

    assert len(results) == 1
    assert results[0].external_id == "42"
    assert results[0].name == "Inception"
    assert results[0].year == 2010
    assert results[0].overview == "Dreams."
    assert (
        results[0].poster_path == "https://artworks.thetvdb.com/banners/inception.jpg"
    )


def test_search_movie_trending_maps_list_payload_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _tvdb_provider()
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__get_trending_movies",
        lambda: [_trending_movie_hit(movie_id=7, name="Trending", year="1999")],
    )

    results = provider.search_movie(None)

    assert len(results) == 1
    assert results[0].external_id == "7"
    assert results[0].name == "Trending"
    assert results[0].year == 1999
    assert results[0].poster_path == "https://artworks.thetvdb.com/banners/test.jpg"


@pytest.mark.parametrize("season_count", [1, 3, 5])
def test_get_show_metadata_still_fetches_each_season_serially(
    monkeypatch: pytest.MonkeyPatch, season_count: int
) -> None:
    provider = _tvdb_provider()
    client = _CountingDelayedClient(delay_seconds=0.01)
    _attach_client(provider, client)
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__get_show",
        lambda show_id: client.get_series_extended(show_id),
    )
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__get_season",
        lambda season_id: client.get_season_extended(season_id),
    )

    elapsed, show = _elapsed_for(lambda: provider.get_show_metadata(str(season_count)))

    expected_calls = 1 + season_count
    assert len(client.calls) == expected_calls
    assert client.calls[0] == f"get_series_extended:{season_count}"
    assert all(call.startswith("get_season_extended:") for call in client.calls[1:])
    assert len(show.seasons) == season_count
    assert elapsed >= expected_calls * client.delay_seconds * 0.9
