"""Fakes and helpers for scheduler task-body characterization tests."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

from miramedia.requests.schemas import (
    MediaRequest,
    MediaRequestId,
    MediaType,
    RequestStatus,
)
from miramedia.shows.schemas import Episode, Season, Show
from tests.fakes.db import RecordingSession


@dataclass
class TrackingRequestService:
    approved: list[MediaRequest] = field(default_factory=list)
    mark_downloading_ids: list[MediaRequestId] = field(default_factory=list)
    mark_downloaded_ids: list[MediaRequestId] = field(default_factory=list)
    heal_calls: int = 0
    heal_result: MediaRequest | None = None

    async def get_approved_not_downloaded(self) -> list[MediaRequest]:
        return list(self.approved)

    async def mark_downloading(self, request_id: MediaRequestId) -> MediaRequest:
        self.mark_downloading_ids.append(request_id)
        return _find_request(self.approved, request_id)

    async def mark_downloaded(self, request_id: MediaRequestId) -> MediaRequest:
        self.mark_downloaded_ids.append(request_id)
        return _find_request(self.approved, request_id)

    async def heal_missing_imdb_id(self, request: MediaRequest) -> MediaRequest:
        self.heal_calls += 1
        if self.heal_result is not None:
            return self.heal_result
        return request


def _find_request(
    approved: list[MediaRequest], request_id: MediaRequestId
) -> MediaRequest:
    for request in approved:
        if request.id == request_id:
            return request
    msg = f"request {request_id} not found"
    raise KeyError(msg)


def make_request(
    *,
    media_type: MediaType = MediaType.movie,
    status: RequestStatus = RequestStatus.approved,
    external_id: str = "tt1234567",
    imdb_id: str | None = None,
    metadata_provider: str = "native",
    title: str = "Test Title",
) -> MediaRequest:
    return MediaRequest(
        id=MediaRequestId(uuid.uuid4()),
        media_type=media_type,
        title=title,
        external_id=external_id,
        imdb_id=imdb_id,
        metadata_provider=metadata_provider,
        status=status,
    )


def native_provider() -> SimpleNamespace:
    return SimpleNamespace(name="native")


class FakeMovieService:
    def __init__(
        self,
        movie: Any,
        *,
        downloaded: bool = False,
        add_raises: Exception | None = None,
    ) -> None:
        self.movie = movie
        self.downloaded = downloaded
        self.add_raises = add_raises
        self.add_movie_calls: list[tuple[str, Any]] = []

    async def add_movie(self, *, external_id: str, metadata_provider: Any) -> Any:
        self.add_movie_calls.append((external_id, metadata_provider))
        if self.add_raises is not None:
            raise self.add_raises
        return self.movie

    async def is_movie_downloaded(self, *, movie: Any) -> bool:  # noqa: ARG002
        return self.downloaded


class FakeShowService:
    def __init__(
        self,
        show: Show,
        *,
        downloaded_episodes: set[UUID] | None = None,
        path_by_row_id: dict[UUID, Any] | None = None,
    ) -> None:
        self.show = show
        self.downloaded_episodes = downloaded_episodes or set()
        self.path_by_row_id = path_by_row_id or {}
        self.add_show_calls: list[tuple[str, Any]] = []

    async def add_show(self, *, external_id: str, metadata_provider: Any) -> Show:
        self.add_show_calls.append((external_id, metadata_provider))
        return self.show

    async def is_episode_downloaded(
        self,
        *,
        episode: Episode,
        season: Season,  # noqa: ARG002
        show: Show,  # noqa: ARG002
    ) -> bool:
        return episode.id in self.downloaded_episodes

    async def resolve_episode_file_path(self, row: Any) -> Any:
        if row.id in self.path_by_row_id:
            return self.path_by_row_id[row.id]
        return getattr(row, "_resolved_path", None)

    async def batch_resolve_episode_file_paths(
        self,
        rows: list[Any],
        episode_context: dict[Any, Any],
        shows: dict[Any, Any],
    ) -> dict[UUID, Any]:
        del episode_context, shows
        paths: dict[UUID, Any] = {}
        for row in rows:
            paths[row.id] = await self.resolve_episode_file_path(row)
        return paths


class FakeMoviePathService:
    def __init__(self, *, path_by_row_id: dict[UUID, Any] | None = None) -> None:
        self.path_by_row_id = path_by_row_id or {}

    async def resolve_movie_file_path(self, row: Any) -> Any:
        if row.id in self.path_by_row_id:
            return self.path_by_row_id[row.id]
        return getattr(row, "_resolved_path", None)

    async def batch_resolve_movie_file_paths(
        self,
        rows: list[Any],
        movies: dict[Any, Any],
    ) -> dict[UUID, Any]:
        del movies
        paths: dict[UUID, Any] = {}
        for row in rows:
            paths[row.id] = await self.resolve_movie_file_path(row)
        return paths


def bg_request_service_factory(
    request_service: TrackingRequestService,
) -> Any:
    request_repository = MagicMock()

    @asynccontextmanager
    async def _bg_request_service():
        yield (request_service, request_repository)

    return _bg_request_service


def bg_movie_service_factory(movie_service: FakeMovieService) -> Any:
    @asynccontextmanager
    async def _bg_movie_service():
        yield movie_service

    return _bg_movie_service


def bg_show_service_factory(show_service: FakeShowService) -> Any:
    @asynccontextmanager
    async def _bg_show_service():
        yield show_service

    return _bg_show_service


def bg_movie_path_service_factory(movie_service: FakeMoviePathService) -> Any:
    @asynccontextmanager
    async def _bg_movie_service():
        yield movie_service

    return _bg_movie_service


def patch_batch_resolve_paths(monkeypatch: Any, path_by_id: dict[UUID, Any]) -> None:
    """Route scheduler/API path resolution without bg_show/bg_movie services."""

    async def _episode_paths(rows, episode_context, shows, layout):  # noqa: ARG001
        return {row.id: path_by_id.get(row.id) for row in rows}

    async def _movie_paths(rows, movies, layout):  # noqa: ARG001
        return {row.id: path_by_id.get(row.id) for row in rows}

    monkeypatch.setattr(
        "miramedia.torrents.integrity.batch_resolve_episode_paths_async",
        _episode_paths,
    )
    monkeypatch.setattr(
        "miramedia.torrents.integrity.batch_resolve_movie_paths_async",
        _movie_paths,
    )


def patch_audit_repository_lookups(monkeypatch: Any) -> None:
    """Stub show/movie context loads used by the audit task snapshot phase."""
    from miramedia.movies.repository import MovieRepository
    from miramedia.shows.repository import ShowRepository
    from miramedia.shows.schemas import EpisodeId, EpisodeIntegrityContext, ShowId

    async def _episode_context(self, episode_ids):  # noqa: ARG001
        return {
            EpisodeId(eid): EpisodeIntegrityContext(
                episode_number=1,
                season_number=1,
                show_id=ShowId(uuid.uuid4()),
                show_name="Test Show",
            )
            for eid in episode_ids
        }

    async def _shows_by_ids(self, show_ids):  # noqa: ARG001
        return {}

    async def _movies_by_ids(self, movie_ids):  # noqa: ARG001
        return {}

    monkeypatch.setattr(ShowRepository, "batch_episodes_with_context", _episode_context)
    monkeypatch.setattr(ShowRepository, "get_shows_by_ids", _shows_by_ids)
    monkeypatch.setattr(MovieRepository, "get_movies_by_ids", _movies_by_ids)


def background_session_factory(
    *,
    episode_rows: list[Any] | None = None,
    movie_rows: list[Any] | None = None,
) -> tuple[Any, list[RecordingSession]]:
    sessions: list[RecordingSession] = []

    @asynccontextmanager
    async def _background_session():
        session = RecordingSession(
            episode_rows=list(episode_rows or []),
            movie_rows=list(movie_rows or []),
        )
        sessions.append(session)
        yield session

    return _background_session, sessions


@dataclass
class FakeFileRow:
    id: UUID
    sha1: str | None = None
    import_error: str | None = None
    episode_id: UUID | None = field(default_factory=uuid.uuid4)
    movie_id: UUID | None = None
    torrent_id: UUID | None = None
    quality: Any = None
    variant: str = ""
    import_status: Any = None
    _resolved_path: Any = None

    def __post_init__(self) -> None:
        from miramedia.file_status import ImportOutcome
        from miramedia.torrents.schemas import Quality

        if self.quality is None:
            self.quality = Quality.hd
        if self.import_status is None:
            self.import_status = ImportOutcome.imported
