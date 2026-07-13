"""Regression tests for integrity batch show/movie lookups."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.exc import MissingGreenlet
from sqlalchemy.sql.selectable import Select

from miramedia.file_status import ImportOutcome
from miramedia.media_state import ProgressStatus
from miramedia.movies.repository import MovieRepository
from miramedia.movies.schemas import MovieFile, MovieId
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import EpisodeFile, ShowId
from miramedia.shows.schemas import Show as ShowSchema
from miramedia.torrents.integrity import INTEGRITY_MISMATCH_MAX_LIMIT
from miramedia.torrents.schemas import Quality
from tests.fakes.repositories import make_movie, make_show
from tests.test_integrity_mismatch_api import (
    _IntegrityMovieRepo,
    _IntegrityShowRepo,
    _movie_service,
    _patch_list_paths,
    _run,
    _show_service,
    _torrent_service,
)

_REAL_GET_SHOWS_BY_IDS = ShowRepository.get_shows_by_ids
_REAL_GET_MOVIES_BY_IDS = MovieRepository.get_movies_by_ids


def _show_mapping(show: ShowSchema) -> dict[str, Any]:
    return {
        "id": show.id,
        "name": show.name,
        "overview": show.overview,
        "year": show.year,
        "ended": show.ended,
        "external_id": show.external_id,
        "metadata_provider": show.metadata_provider,
        "continuous_download": show.continuous_download,
        "skipped": show.skipped,
        "library": show.library,
        "original_language": show.original_language,
        "imdb_id": show.imdb_id,
        "vote_average": show.vote_average,
        "content_rating": show.content_rating,
        "genres": show.genres,
        "cast": show.cast,
        "preferred_quality": show.preferred_quality,
        "preferred_codec": show.preferred_codec,
        "subtitle_languages": show.subtitle_languages,
        "last_metadata_check": show.last_metadata_check,
        "metadata_failure_backoff_until": show.metadata_failure_backoff_until,
        "auto_download_backoff_until": show.auto_download_backoff_until,
        "wanted_episode_count": show.wanted_episode_count,
        "downloaded_episode_count": show.downloaded_episode_count,
        "list_progress_status": show.list_progress_status,
    }


def _movie_mapping(movie) -> dict[str, Any]:
    return {
        "id": movie.id,
        "name": movie.name,
        "overview": movie.overview,
        "year": movie.year,
        "release_date": movie.release_date,
        "external_id": movie.external_id,
        "metadata_provider": movie.metadata_provider,
        "continuous_download": movie.continuous_download,
        "skipped": movie.skipped,
        "library": movie.library,
        "original_language": movie.original_language,
        "imdb_id": movie.imdb_id,
        "vote_average": movie.vote_average,
        "content_rating": movie.content_rating,
        "runtime": movie.runtime,
        "genres": movie.genres,
        "cast": movie.cast,
        "preferred_quality": movie.preferred_quality,
        "preferred_codec": movie.preferred_codec,
        "subtitle_languages": movie.subtitle_languages,
        "last_metadata_check": movie.last_metadata_check,
        "metadata_failure_backoff_until": movie.metadata_failure_backoff_until,
        "auto_download_backoff_until": movie.auto_download_backoff_until,
        "downloaded": movie.downloaded,
    }


@dataclass
class _MappingExecuteResult:
    rows: list[dict[str, Any]]

    def mappings(self) -> _MappingExecuteResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self.rows


@dataclass
class _MappingRecordingSession:
    executes: list[Any] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    async def execute(self, stmt: Any) -> _MappingExecuteResult:
        self.executes.append(stmt)
        assert isinstance(stmt, Select)
        return _MappingExecuteResult(self.rows)


class _LazySeasonsORM:
    """ORM-shaped row whose seasons relationship would lazy-load off-session."""

    def __init__(self, **fields: Any) -> None:
        for key, value in fields.items():
            setattr(self, key, value)

    @property
    def seasons(self) -> list[object]:
        msg = "greenlet_spawn has not been called; can't call await_only() here."
        raise MissingGreenlet(msg)


def test_show_schema_model_validate_lazy_orm_raises() -> None:
    orm = _LazySeasonsORM(
        id=uuid.uuid4(),
        name="Severance",
        overview="",
        year=2022,
        ended=False,
        external_id="ext",
        metadata_provider="native",
        continuous_download=None,
        skipped=False,
        library="Default",
        original_language=None,
        imdb_id=None,
        vote_average=None,
        content_rating=None,
        genres=None,
        cast=None,
        preferred_quality=None,
        preferred_codec=None,
        subtitle_languages=None,
        last_metadata_check=None,
        metadata_failure_backoff_until=None,
        auto_download_backoff_until=None,
        wanted_episode_count=0,
        downloaded_episode_count=0,
        list_progress_status=ProgressStatus.none.value,
    )
    with pytest.raises((MissingGreenlet, Exception)):
        ShowSchema.model_validate(orm)


def test_get_shows_by_ids_uses_scalar_mapping_not_orm_entity() -> None:
    show = make_show(name="Severance").model_copy(update={"library": "TV"})
    session = _MappingRecordingSession(rows=[_show_mapping(show)])
    repo = ShowRepository(session)  # type: ignore[arg-type]

    loaded = asyncio.run(repo.get_shows_by_ids([show.id]))

    assert len(session.executes) == 1
    stmt = session.executes[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "season" not in compiled.lower()
    assert loaded[show.id].name == "Severance"
    assert loaded[show.id].library == "TV"
    assert loaded[show.id].seasons == []


def test_get_movies_by_ids_uses_scalar_mapping() -> None:
    movie = make_movie(name="Dune").model_copy(update={"library": "Movies"})
    session = _MappingRecordingSession(rows=[_movie_mapping(movie)])
    repo = MovieRepository(session)  # type: ignore[arg-type]

    loaded = asyncio.run(repo.get_movies_by_ids([movie.id]))

    assert len(session.executes) == 1
    assert loaded[movie.id].name == "Dune"
    assert loaded[movie.id].library == "Movies"


class _MappingShowLookupRepo(_IntegrityShowRepo):
    """Delegate get_shows_by_ids to the real repository + mapping session."""

    def __init__(self) -> None:
        super().__init__()
        self._lookup_session = _MappingRecordingSession()

    async def get_shows_by_ids(
        self, show_ids: list[ShowId]
    ) -> dict[ShowId, ShowSchema]:
        self._lookup_session.rows = [
            _show_mapping(self.shows[sid]) for sid in show_ids if sid in self.shows
        ]
        repo = ShowRepository(self._lookup_session)  # type: ignore[arg-type]
        return await _REAL_GET_SHOWS_BY_IDS(repo, show_ids)


class _MappingMovieLookupRepo(_IntegrityMovieRepo):
    def __init__(self) -> None:
        super().__init__()
        self._lookup_session = _MappingRecordingSession()

    async def get_movies_by_ids(self, movie_ids: list[MovieId]):
        self._lookup_session.rows = [
            _movie_mapping(self.movies[mid]) for mid in movie_ids if mid in self.movies
        ]
        repo = MovieRepository(self._lookup_session)  # type: ignore[arg-type]
        return await _REAL_GET_MOVIES_BY_IDS(repo, movie_ids)


def test_list_integrity_mismatches_real_show_lookup_avoids_lazy_seasons(
    monkeypatch,
) -> None:
    """API batch path must survive real get_shows_by_ids without ORM seasons."""
    show = make_show(name="Severance", season_number=1, episode_number=1)
    show_repo = _MappingShowLookupRepo()
    show_repo.add_show(show)
    episode = show.seasons[0].episodes[0]
    detected = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    fid = UUID(int=1)
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.hd,
        torrent_id=None,
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        last_attempt_at=detected,
        sha1="abc",
    )
    movie = make_movie(name="Dune")
    movie_repo = _MappingMovieLookupRepo()
    movie_repo.add_movie(movie)
    movie_file_id = UUID(int=2)
    movie_repo.movie_files[movie_file_id] = MovieFile(
        id=movie_file_id,
        movie_id=movie.id,
        quality=Quality.uhd,
        variant="",
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        last_attempt_at=detected,
        sha1="def",
    )
    path_by_id = {fid: None}
    _patch_list_paths(
        monkeypatch,
        show_paths=path_by_id,
        movie_paths={movie_file_id: None},
    )
    svc = _torrent_service(show_repo, movie_repo)
    page = _run(
        svc.list_integrity_mismatches(
            offset=0,
            limit=INTEGRITY_MISMATCH_MAX_LIMIT,
            show_service=_show_service(show_repo, path_by_id),
            movie_service=_movie_service(movie_repo, {movie_file_id: None}),
        )
    )

    assert len(page.items) == 2
    assert len(show_repo._lookup_session.executes) == 1
    assert len(movie_repo._lookup_session.executes) == 1
    show_rows = [row for row in page.items if row.media_type == "show"]
    assert show_rows[0].media_title == "Severance"
