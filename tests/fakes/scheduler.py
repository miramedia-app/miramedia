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


class FakeMoviePathService:
    def __init__(self, *, path_by_row_id: dict[UUID, Any] | None = None) -> None:
        self.path_by_row_id = path_by_row_id or {}

    async def resolve_movie_file_path(self, row: Any) -> Any:
        if row.id in self.path_by_row_id:
            return self.path_by_row_id[row.id]
        return getattr(row, "_resolved_path", None)


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
    _resolved_path: Any = None
