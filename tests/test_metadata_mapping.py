"""Golden-payload mapping tests for TMDB / TVDB / native metadata backends.

Fixtures under ``tests/fixtures/metadata/`` are hand-crafted minimal payloads
shaped like each provider's API — never fetched live.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from miramedia.config import MiraMediaConfig
from miramedia.metadata.backends.native import (
    NativeMetadataProvider,
    _parse_cinemeta_cast,
)
from miramedia.metadata.backends.tmdb import (
    TMDB_DISCOVERY_POSTER_SIZE,
    TMDB_IMAGE_BASE,
    TmdbMetadataProvider,
    _discovery_poster_url,
    _extract_movie_content_rating,
    _extract_show_content_rating,
)
from miramedia.metadata.backends.tvdb import TvdbMetadataProvider
from miramedia.metadata.cache import invalidate_all

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "metadata"


def _load_json(name: str) -> Any:
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture(autouse=True)
def _clear_metadata_cache() -> None:
    invalidate_all()


@pytest.fixture(autouse=True)
def _pin_utc_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    # Provider air dates/times derive from UTC datetimes (Cinemeta 'released',
    # TVMaze 'airstamp') converted to the configured zone. Pin UTC so these
    # mapping assertions are deterministic regardless of the host's timezone.
    monkeypatch.setattr(MiraMediaConfig().misc, "timezone", "UTC")


# ---------------------------------------------------------------------------
# TMDB pure helpers
# ---------------------------------------------------------------------------


def test_extract_show_content_rating_prefers_us() -> None:
    data = {
        "results": [
            {"iso_3166_1": "GB", "rating": "15"},
            {"iso_3166_1": "US", "rating": "TV-MA"},
        ]
    }
    assert _extract_show_content_rating(data) == "TV-MA"


def test_extract_show_content_rating_falls_back_to_first() -> None:
    data = {"results": [{"iso_3166_1": "GB", "rating": "15"}]}
    assert _extract_show_content_rating(data) == "15"


def test_extract_show_content_rating_empty() -> None:
    assert _extract_show_content_rating({}) is None
    assert _extract_show_content_rating({"results": []}) is None


def test_extract_show_content_rating_empty_rating_string() -> None:
    data = {"results": [{"iso_3166_1": "US", "rating": ""}]}
    assert _extract_show_content_rating(data) is None


def test_extract_movie_content_rating_us_cert() -> None:
    data = {
        "results": [
            {
                "iso_3166_1": "US",
                "release_dates": [
                    {"certification": ""},
                    {"certification": "R"},
                ],
            }
        ]
    }
    assert _extract_movie_content_rating(data) == "R"


def test_extract_movie_content_rating_absent_us() -> None:
    data = {
        "results": [
            {
                "iso_3166_1": "GB",
                "release_dates": [{"certification": "15"}],
            }
        ]
    }
    assert _extract_movie_content_rating(data) is None


def test_extract_movie_content_rating_empty() -> None:
    assert _extract_movie_content_rating({}) is None


# ---------------------------------------------------------------------------
# TMDB mapping
# ---------------------------------------------------------------------------


def _tmdb_provider() -> TmdbMetadataProvider:
    provider = TmdbMetadataProvider.__new__(TmdbMetadataProvider)
    provider.primary_languages = []
    provider.default_language = "en"
    return provider


def _tmdb_tv_search_result(*, poster_path: str | None = "/poster.jpg") -> dict:
    return {
        "id": 1396,
        "name": "Breaking Bad",
        "original_name": "Breaking Bad",
        "overview": "Chemistry teacher.",
        "poster_path": poster_path,
        "first_air_date": "2008-01-20",
        "vote_average": 8.9,
        "original_language": "en",
    }


def _tmdb_movie_search_result(*, poster_path: str | None = "/poster.jpg") -> dict:
    return {
        "id": 550,
        "title": "Fight Club",
        "original_title": "Fight Club",
        "overview": "An insomniac office worker.",
        "poster_path": poster_path,
        "release_date": "1999-10-15",
        "vote_average": 8.4,
        "original_language": "en",
    }


def test_discovery_poster_url_uses_configured_size() -> None:
    assert _discovery_poster_url("/poster.jpg") == (
        f"{TMDB_IMAGE_BASE}/{TMDB_DISCOVERY_POSTER_SIZE}/poster.jpg"
    )
    assert TMDB_DISCOVERY_POSTER_SIZE == "w500"


def test_tmdb_get_show_metadata_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    show_payload = _load_json("tmdb_show.json")
    season1 = _load_json("tmdb_season.json")
    season0 = {"season_number": 0, "episodes": []}
    provider = _tmdb_provider()

    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_show_metadata",
        lambda *_a, **_k: show_payload,
    )
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_show_external_ids",
        lambda *_a, **_k: {"imdb_id": "tt0903747"},
    )
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_show_credits",
        lambda *_a, **_k: ["Bryan Cranston", "Aaron Paul"],
    )
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_show_content_ratings",
        lambda *_a, **_k: {
            "results": [{"iso_3166_1": "US", "rating": "TV-MA"}],
        },
    )

    def fake_season(*_a: object, **kwargs: object) -> dict:
        season_number = kwargs.get("season_number")
        return season0 if season_number == 0 else season1

    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_season_metadata",
        fake_season,
    )

    show = provider.get_show_metadata("1396", language="en")

    assert show.external_id == "1396"
    assert show.name == "Breaking Bad"
    assert show.year == 2008
    assert show.ended is True
    assert show.original_language == "en"
    assert show.imdb_id == "tt0903747"
    assert show.content_rating == "TV-MA"
    assert show.genres == ["Drama", "Crime"]
    assert show.cast == ["Bryan Cranston", "Aaron Paul"]
    assert show.metadata_provider == "tmdb"
    assert [s.number for s in show.seasons] == [0, 1]
    season1_eps = next(s for s in show.seasons if s.number == 1).episodes
    assert season1_eps[0].number == 1
    assert season1_eps[0].title == "Pilot"
    assert season1_eps[0].air_date == date(2008, 1, 20)
    assert season1_eps[1].air_date is None  # null air_date fallback


def test_tmdb_get_show_metadata_missing_imdb_and_empty_cast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    show_payload = _load_json("tmdb_show.json")
    show_payload = {**show_payload, "first_air_date": None, "genres": [], "seasons": []}
    provider = _tmdb_provider()

    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_show_metadata",
        lambda *_a, **_k: show_payload,
    )
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_show_external_ids",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_show_credits",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_show_content_ratings",
        lambda *_a, **_k: {},
    )

    show = provider.get_show_metadata("1396", language="en")
    assert show.year is None
    assert show.imdb_id is None
    assert show.genres == []
    assert show.cast == []
    assert show.content_rating is None
    assert show.seasons == []


def test_tmdb_get_movie_metadata_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    movie_payload = _load_json("tmdb_movie.json")
    provider = _tmdb_provider()

    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_movie_metadata",
        lambda *_a, **_k: movie_payload,
    )
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_movie_external_ids",
        lambda *_a, **_k: {"imdb_id": "tt0137523"},
    )
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_movie_credits",
        lambda *_a, **_k: ["Brad Pitt", "Edward Norton"],
    )
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_movie_release_dates",
        lambda *_a, **_k: {
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [{"certification": "R"}],
                }
            ]
        },
    )

    movie = provider.get_movie_metadata("550", language="en")
    assert movie.external_id == "550"
    assert movie.name == "Fight Club"
    assert movie.year == 1999
    assert movie.release_date == date(1999, 10, 15)
    assert movie.imdb_id == "tt0137523"
    assert movie.content_rating == "R"
    assert movie.runtime == 139
    assert movie.genres == ["Drama"]
    assert movie.cast == ["Brad Pitt", "Edward Norton"]
    assert movie.metadata_provider == "tmdb"
    assert movie.original_language == "en"


def test_tmdb_search_show_stringifies_id(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _tmdb_provider()
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__search_tv",
        lambda *_a, **_k: {"results": [_tmdb_tv_search_result()]},
    )
    results = provider.search_show("breaking", max_pages=1)
    assert len(results) == 1
    assert results[0].external_id == "1396"
    assert results[0].year == 2008
    assert results[0].metadata_provider == "tmdb"
    assert results[0].poster_path == _discovery_poster_url("/poster.jpg")
    assert "/original" not in (results[0].poster_path or "")


def test_tmdb_search_movie_uses_discovery_poster_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _tmdb_provider()
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__search_movie",
        lambda *_a, **_k: {"results": [_tmdb_movie_search_result()]},
    )
    results = provider.search_movie("fight", max_pages=1)
    assert len(results) == 1
    assert results[0].poster_path == _discovery_poster_url("/poster.jpg")
    assert "/original" not in (results[0].poster_path or "")


def test_tmdb_trending_show_uses_discovery_poster_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _tmdb_provider()
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_trending_tv",
        lambda *_a, **_k: {"results": [_tmdb_tv_search_result()]},
    )
    results = provider.search_show()
    assert len(results) == 1
    assert results[0].poster_path == _discovery_poster_url("/poster.jpg")


def test_tmdb_trending_movie_uses_discovery_poster_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _tmdb_provider()
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_trending_movies",
        lambda *_a, **_k: {"results": [_tmdb_movie_search_result()]},
    )
    results = provider.search_movie()
    assert len(results) == 1
    assert results[0].poster_path == _discovery_poster_url("/poster.jpg")


def test_tmdb_search_show_null_poster(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _tmdb_provider()
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__search_tv",
        lambda *_a, **_k: {"results": [_tmdb_tv_search_result(poster_path=None)]},
    )
    results = provider.search_show("breaking", max_pages=1)
    assert len(results) == 1
    assert results[0].poster_path is None


def test_tmdb_search_movie_null_poster(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _tmdb_provider()
    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__search_movie",
        lambda *_a, **_k: {"results": [_tmdb_movie_search_result(poster_path=None)]},
    )
    results = provider.search_movie("fight", max_pages=1)
    assert len(results) == 1
    assert results[0].poster_path is None


def test_tmdb_download_show_poster_still_uses_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from miramedia.shows.schemas import Show

    provider = _tmdb_provider()
    provider.storage_path = tmp_path
    captured_url: list[str] = []

    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_show_metadata",
        lambda *_a, **_k: {"poster_path": "/poster.jpg", "name": "Breaking Bad"},
    )
    monkeypatch.setattr(
        "miramedia.metadata.backends.tmdb.miramedia.metadata.utils.download_poster_image",
        lambda **kwargs: captured_url.append(kwargs["poster_url"]) or True,
    )

    show = Show(
        external_id="1396",
        name="Breaking Bad",
        overview="",
        year=2008,
        metadata_provider="tmdb",
        original_language="en",
    )
    assert provider.download_show_poster_image(show) is True
    assert captured_url == [f"{TMDB_IMAGE_BASE}/original/poster.jpg"]


def test_tmdb_download_movie_poster_still_uses_original(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from miramedia.movies.schemas import Movie

    provider = _tmdb_provider()
    provider.storage_path = tmp_path
    captured_url: list[str] = []

    monkeypatch.setattr(
        provider,
        "_TmdbMetadataProvider__get_movie_metadata",
        lambda *_a, **_k: {"poster_path": "/poster.jpg", "title": "Fight Club"},
    )
    monkeypatch.setattr(
        "miramedia.metadata.backends.tmdb.miramedia.metadata.utils.download_poster_image",
        lambda **kwargs: captured_url.append(kwargs["poster_url"]) or True,
    )

    movie = Movie(
        external_id="550",
        name="Fight Club",
        overview="",
        year=1999,
        metadata_provider="tmdb",
        original_language="en",
    )
    assert provider.download_movie_poster_image(movie) is True
    assert captured_url == [f"{TMDB_IMAGE_BASE}/original/poster.jpg"]


# ---------------------------------------------------------------------------
# TVDB mapping
# ---------------------------------------------------------------------------


def _tvdb_provider() -> TvdbMetadataProvider:
    return TvdbMetadataProvider.__new__(TvdbMetadataProvider)


def test_tvdb_get_show_metadata_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    show_payload = _load_json("tvdb_show.json")
    season_aired = _load_json("tvdb_season_aired.json")
    season_dvd = _load_json("tvdb_season_dvd.json")
    provider = _tvdb_provider()

    seasons = {1001: season_aired, 1002: season_dvd}
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__get_show",
        lambda *_a, **_k: show_payload,
    )
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__get_season",
        lambda *a, **k: seasons[k.get("season_id", a[0] if a else None)],
    )

    show = provider.get_show_metadata("81189")
    assert show.external_id == "81189"
    assert show.name == "Breaking Bad"
    # TVDB returns year as a string; Show.year is int | None — pydantic coerces.
    assert show.year == 2008
    assert show.imdb_id == "tt0903747"
    assert show.genres == ["Drama", "Crime"]
    assert show.cast == ["Bryan Cranston", "Aaron Paul"]
    assert show.metadata_provider == "tvdb"
    # DVD-order season filtered out — only aired-order season kept
    assert len(show.seasons) == 1
    assert show.seasons[0].number == 1
    assert show.seasons[0].episodes[0].title == "Pilot"
    assert show.seasons[0].episodes[0].number == 1
    assert show.seasons[0].episodes[0].air_date == date(2008, 1, 20)


def test_tvdb_get_movie_metadata_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    movie_payload = _load_json("tvdb_movie.json")
    provider = _tvdb_provider()
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__get_movie",
        lambda *_a, **_k: movie_payload,
    )

    movie = provider.get_movie_metadata("123")
    assert movie.external_id == "123"
    assert movie.name == "Inception"
    assert movie.year == 2010
    assert movie.imdb_id == "tt1375666"
    assert movie.genres == ["Science Fiction"]
    assert movie.cast == ["Leonardo DiCaprio"]
    assert movie.release_date == date(2010, 7, 16)
    assert movie.overview == "Overviews are not supported with TVDB"
    assert movie.metadata_provider == "tvdb"


def test_tvdb_search_show_filters_series(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _tvdb_provider()
    monkeypatch.setattr(
        provider,
        "_TvdbMetadataProvider__search",
        lambda *_a, **_k: [
            {
                "type": "series",
                "name": "Breaking Bad",
                "tvdb_id": 81189,
                "year": "2008",
                "overview": "Chemistry.",
                "image_url": "https://art.example/bb.jpg",
            },
            {
                "type": "movie",
                "name": "Breaking Bad Movie",
                "tvdb_id": 999,
                "year": "2020",
            },
        ],
    )
    results = provider.search_show("breaking")
    assert len(results) == 1
    assert results[0].external_id == "81189"
    assert results[0].year == 2008
    assert results[0].metadata_provider == "tvdb"


# ---------------------------------------------------------------------------
# Native / Cinemeta mapping
# ---------------------------------------------------------------------------


def _native_provider(
    *, cinemeta: bool = True, tvmaze: bool = False
) -> NativeMetadataProvider:
    provider = NativeMetadataProvider.__new__(NativeMetadataProvider)
    provider._desired_languages = ["en"]
    provider._tvmaze_enabled = tvmaze
    provider._cinemeta_enabled = cinemeta
    return provider


def test_parse_cinemeta_cast_unescapes_html_entities() -> None:
    assert _parse_cinemeta_cast(["Matt Smith", "Emma D&apos;Arcy", "Olivia Cooke"]) == [
        "Matt Smith",
        "Emma D'Arcy",
        "Olivia Cooke",
    ]


def test_native_get_show_metadata_cinemeta_maps_imdb_and_specials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = _load_json("cinemeta_series.json")
    provider = _native_provider()
    monkeypatch.setattr(
        provider,
        "_cinemeta_get",
        lambda path: series if path.endswith("tt0903747.json") else {},
    )

    show = provider.get_show_metadata("tt0903747")
    assert show.external_id == "tt0903747"
    assert show.imdb_id == "tt0903747"
    assert show.name == "Breaking Bad"
    assert show.year == 2008
    assert show.ended is True
    assert show.genres == ["Crime", "Drama", "Thriller"]
    assert show.cast == ["Bryan Cranston", "Aaron Paul"]
    assert show.vote_average == 9.5
    assert show.metadata_provider == "native"

    season_numbers = sorted(s.number for s in show.seasons)
    assert season_numbers == [0, 1]
    specials = next(s for s in show.seasons if s.number == 0)
    assert specials.episodes[0].title == "Behind the Scenes"
    assert specials.episodes[0].number == 1
    assert specials.episodes[0].air_date == date(2009, 2, 17)


def test_native_get_movie_metadata_maps_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    movie = _load_json("cinemeta_movie.json")
    provider = _native_provider()
    monkeypatch.setattr(provider, "_cinemeta_get", lambda _path: movie)

    result = provider.get_movie_metadata("tt1375666")
    assert result.external_id == "tt1375666"
    assert result.imdb_id == "tt1375666"
    assert result.name == "Inception"
    assert result.year == 2010
    assert result.release_date == date(2010, 7, 16)
    assert result.runtime == 148  # 2h 28min
    assert result.vote_average == 8.8
    assert result.genres == ["Action", "Adventure", "Sci-Fi"]
    assert result.cast == ["Leonardo DiCaprio", "Joseph Gordon-Levitt"]
    assert result.metadata_provider == "native"
    assert result.content_rating is None
