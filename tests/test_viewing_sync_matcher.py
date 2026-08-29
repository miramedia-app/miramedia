"""DB-free matcher tests for Jellyfin viewing-state dry-run."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from miramedia.viewing_sync.matcher import (
    build_movie_catalog_lookup,
    build_show_catalog_lookup,
    match_episode,
    match_movie,
    match_show,
    media_catalog_from_sequences,
    normalize_imdb_id,
    quarantine_from_match,
)
from miramedia.viewing_sync.schemas import (
    ExternalViewingEvent,
    MatchConfidence,
    MediaKind,
    QuarantineReason,
)


@dataclass
class _Movie:
    id: UUID
    imdb_id: str | None
    external_id: str
    metadata_provider: str


@dataclass
class _Show:
    id: UUID
    imdb_id: str | None
    external_id: str
    metadata_provider: str


@dataclass
class _Episode:
    id: UUID
    season_id: UUID
    number: int


def _movie(imdb: str) -> _Movie:
    return _Movie(
        id=uuid4(),
        imdb_id=imdb,
        external_id=imdb,
        metadata_provider="native",
    )


def _movie_catalog(*movies: _Movie):
    return build_movie_catalog_lookup(movies)


def _show_catalog(*shows: _Show):
    return build_show_catalog_lookup(shows)


def test_normalize_imdb_id_adds_tt_prefix() -> None:
    assert normalize_imdb_id("0111161") == "tt0111161"


def test_movie_unique_imdb_match() -> None:
    movie = _movie("tt123")
    result = match_movie(_movie_catalog(movie), {"Imdb": "tt123"})
    assert result.confidence == MatchConfidence.unique
    assert result.media_id == movie.id


def test_movie_ambiguous_imdb_match() -> None:
    first = _movie("tt123")
    second = _movie("tt123")
    result = match_movie(_movie_catalog(first, second), {"imdb": "tt123"})
    assert result.confidence == MatchConfidence.ambiguous
    assert len(result.candidate_ids) == 2


def test_movie_ambiguous_tmdb_match() -> None:
    first = _Movie(
        id=uuid4(),
        imdb_id=None,
        external_id="42",
        metadata_provider="tmdb",
    )
    second = _Movie(
        id=uuid4(),
        imdb_id=None,
        external_id="42",
        metadata_provider="tmdb",
    )
    result = match_movie(_movie_catalog(first, second), {"tmdb": "42"})
    assert result.confidence == MatchConfidence.ambiguous
    assert set(result.candidate_ids) == {first.id, second.id}


def test_movie_ambiguous_tvdb_match() -> None:
    first = _Movie(
        id=uuid4(),
        imdb_id=None,
        external_id="99",
        metadata_provider="tvdb",
    )
    second = _Movie(
        id=uuid4(),
        imdb_id=None,
        external_id="99",
        metadata_provider="tvdb",
    )
    result = match_movie(_movie_catalog(first, second), {"tvdb": "99"})
    assert result.confidence == MatchConfidence.ambiguous


def test_movie_native_imdb_alias_match() -> None:
    movie = _Movie(
        id=uuid4(),
        imdb_id=None,
        external_id="tt555",
        metadata_provider="native",
    )
    result = match_movie(_movie_catalog(movie), {"imdb": "tt555"})
    assert result.confidence == MatchConfidence.unique
    assert result.media_id == movie.id


def test_movie_imdb_precedence_over_tmdb() -> None:
    imdb_movie = _movie("tt777")
    tmdb_movie = _Movie(
        id=uuid4(),
        imdb_id=None,
        external_id="777",
        metadata_provider="tmdb",
    )
    result = match_movie(
        _movie_catalog(imdb_movie, tmdb_movie),
        {"imdb": "tt777", "tmdb": "777"},
    )
    assert result.confidence == MatchConfidence.unique
    assert result.media_id == imdb_movie.id


def test_movie_unmatched_provider_ids() -> None:
    movie = _movie("tt123")
    result = match_movie(_movie_catalog(movie), {"tmdb": "missing"})
    assert result.confidence == MatchConfidence.unmatched
    assert result.reason == "zero_matches"


def test_show_ambiguous_tmdb_match() -> None:
    first = _Show(
        id=uuid4(),
        imdb_id=None,
        external_id="100",
        metadata_provider="tmdb",
    )
    second = _Show(
        id=uuid4(),
        imdb_id=None,
        external_id="100",
        metadata_provider="tmdb",
    )
    result = match_show(_show_catalog(first, second), {"tmdb": "100"})
    assert result.confidence == MatchConfidence.ambiguous


def test_episode_unique_show_and_number_match() -> None:
    show = _Show(
        id=uuid4(),
        imdb_id="tt999",
        external_id="tt999",
        metadata_provider="native",
    )
    episode = _Episode(id=uuid4(), season_id=uuid4(), number=5)
    catalog = media_catalog_from_sequences(
        [],
        [show],
        {(show.id, 2): [episode]},
    )
    result = match_episode(
        catalog=catalog,
        provider_ids={"Imdb": "tt999"},
        season_number=2,
        episode_number=5,
        episode_number_end=None,
    )
    assert result.confidence == MatchConfidence.unique
    assert result.media_id == episode.id


def test_episode_unmatched_coordinates() -> None:
    show = _Show(
        id=uuid4(),
        imdb_id="tt999",
        external_id="tt999",
        metadata_provider="native",
    )
    catalog = media_catalog_from_sequences([], [show], {})
    result = match_episode(
        catalog=catalog,
        provider_ids={"Imdb": "tt999"},
        season_number=1,
        episode_number=1,
        episode_number_end=None,
    )
    assert result.confidence == MatchConfidence.unmatched
    assert result.reason == "zero_matches"


def test_episode_multi_episode_quarantine() -> None:
    show = _Show(
        id=uuid4(),
        imdb_id="tt999",
        external_id="tt999",
        metadata_provider="native",
    )
    catalog = media_catalog_from_sequences([], [show], {})
    result = match_episode(
        catalog=catalog,
        provider_ids={"Imdb": "tt999"},
        season_number=1,
        episode_number=1,
        episode_number_end=2,
    )
    assert result.reason == "multi_episode"


def test_missing_provider_ids_quarantine() -> None:
    movie = _movie("tt123")
    result = match_movie(_movie_catalog(movie), {})
    assert result.reason == "missing_provider_ids"
    event = ExternalViewingEvent(
        connector="jellyfin",
        connector_user_id="user-1",
        connector_item_id="item-1",
        media_kind=MediaKind.movie,
        provider_ids={},
        season_number=None,
        episode_number=None,
        episode_number_end=None,
        position_ms=0,
        duration_ms=0,
        remote_played=False,
        remote_at=None,
        payload_digest="abc",
    )
    record = quarantine_from_match(event, result)
    assert record is not None
    assert record.reason == QuarantineReason.missing_provider_ids
