"""Route-level tests for /api/v1/playback endpoints."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from miramedia.playback.schemas import (
    MediaKind,
    PlaybackProgress,
    WatchState,
)
from miramedia.playback.service import PlaybackService
from tests.fakes.repositories import FakePlaybackRepository

PREFIX = "/api/v1/playback"


def _progress(file_id: uuid.UUID | None = None) -> PlaybackProgress:
    return PlaybackProgress(
        file_id=file_id or uuid.uuid4(),
        media_kind=MediaKind.movie,
        position_ms=60_000,
        duration_ms=100_000,
        completed=False,
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def playback_client(
    override_dependency: Callable[[Callable, object], None],
) -> Callable[..., tuple[TestClient, uuid.UUID, Any]]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.playback.dependencies import get_playback_service

    def make(
        *,
        user_id: uuid.UUID | None = None,
        service: Any | None = None,
    ) -> tuple[TestClient, uuid.UUID, Any]:
        user_id = user_id or uuid.uuid4()
        if service is None:
            service = MagicMock()
            service.get_progress = AsyncMock(return_value=None)
            service.upsert_progress = AsyncMock(return_value=_progress())
            service.delete_progress = AsyncMock(return_value=None)
            service.delete_all_progress = AsyncMock(return_value=None)
            service.list_continue = AsyncMock(return_value=[])
            service.get_watched = AsyncMock(return_value=None)
            service.set_watched = AsyncMock(return_value=None)
            service.clear_watched_override = AsyncMock(return_value=None)
            service.set_season_watched = AsyncMock(return_value=None)
            service.set_show_watched = AsyncMock(return_value=None)
            service.delete_all_viewing_state = AsyncMock(return_value=None)
            service.list_up_next = AsyncMock(return_value=[])

        async def _stub_session() -> Any:
            yield None

        async def _active_user() -> Any:
            user = MagicMock()
            user.id = user_id
            return user

        def _playback_service() -> Any:
            return service

        override_dependency(get_session, _stub_session)
        override_dependency(current_active_user, _active_user)
        override_dependency(get_playback_service, _playback_service)
        return TestClient(app, raise_server_exceptions=False), user_id, service

    return make


def test_get_progress_returns_null_when_missing(playback_client) -> None:
    file_id = uuid.uuid4()
    client, user_id, service = playback_client()
    response = client.get(f"{PREFIX}/progress", params={"file_id": str(file_id)})
    assert response.status_code == 200
    assert response.json() is None
    service.get_progress.assert_awaited_once_with(
        user_id=user_id,
        file_id=file_id,
        media_kind=None,
    )


def test_put_progress_validates_bounds(playback_client) -> None:
    file_id = uuid.uuid4()
    client, _user_id, service = playback_client()
    response = client.put(
        f"{PREFIX}/progress",
        json={
            "file_id": str(file_id),
            "media_kind": "movie",
            "position_ms": 200_000,
            "duration_ms": 100_000,
        },
    )
    assert response.status_code == 422
    service.upsert_progress.assert_not_awaited()


def test_put_progress_calls_service_with_owner(playback_client) -> None:
    file_id = uuid.uuid4()
    client, user_id, service = playback_client()
    response = client.put(
        f"{PREFIX}/progress",
        json={
            "file_id": str(file_id),
            "media_kind": "movie",
            "position_ms": 60_000,
            "duration_ms": 100_000,
        },
    )
    assert response.status_code == 200
    service.upsert_progress.assert_awaited_once()
    assert service.upsert_progress.await_args.kwargs["user_id"] == user_id


def test_delete_progress_scoped_to_owner(playback_client) -> None:
    file_id = uuid.uuid4()
    client, user_id, service = playback_client()
    response = client.delete(f"{PREFIX}/progress", params={"file_id": str(file_id)})
    assert response.status_code == 204
    service.delete_progress.assert_awaited_once_with(
        user_id=user_id,
        file_id=file_id,
    )


def test_delete_progress_missing_row_returns_204(playback_client) -> None:
    file_id = uuid.uuid4()
    user_id = uuid.uuid4()
    playback_repo = FakePlaybackRepository()
    service = PlaybackService(
        playback_repo,  # type: ignore[arg-type]
        AsyncMock(),
        AsyncMock(),
    )
    client, _user_id, _service = playback_client(user_id=user_id, service=service)
    response = client.delete(f"{PREFIX}/progress", params={"file_id": str(file_id)})
    assert response.status_code == 204


def test_delete_all_progress_scoped_to_owner(playback_client) -> None:
    client, user_id, service = playback_client()
    response = client.delete(f"{PREFIX}/progress/all")
    assert response.status_code == 204
    service.delete_all_progress.assert_awaited_once_with(user_id=user_id)


def test_list_continue_default_limit(playback_client) -> None:
    client, user_id, service = playback_client()
    response = client.get(f"{PREFIX}/continue")
    assert response.status_code == 200
    assert response.json() == []
    service.list_continue.assert_awaited_once_with(user_id=user_id, limit=20)


def test_list_watch_next_default_limit(playback_client) -> None:
    client, user_id, service = playback_client()
    response = client.get(f"{PREFIX}/watch-next")
    assert response.status_code == 200
    assert response.json() == []
    service.list_up_next.assert_awaited_once_with(
        user_id=user_id,
        limit=20,
        include_specials=False,
    )


def test_list_watch_next_limit_validation(playback_client) -> None:
    client, _user_id, service = playback_client()
    assert client.get(f"{PREFIX}/watch-next", params={"limit": 0}).status_code == 422
    assert client.get(f"{PREFIX}/watch-next", params={"limit": 201}).status_code == 422
    service.list_up_next.assert_not_awaited()
    assert client.get(f"{PREFIX}/watch-next", params={"limit": 200}).status_code == 200


def test_list_watch_next_passes_include_specials(playback_client) -> None:
    client, user_id, service = playback_client()
    response = client.get(
        f"{PREFIX}/watch-next",
        params={"include_specials": "true", "limit": 10},
    )
    assert response.status_code == 200
    service.list_up_next.assert_awaited_once_with(
        user_id=user_id,
        limit=10,
        include_specials=True,
    )


def test_watched_routes_pass_current_user_id(playback_client) -> None:
    media_id = uuid.uuid4()
    watched = WatchState(
        media_kind="movie",
        media_id=media_id,
        watched=True,
        source="manual",
        watched_at=datetime.now(UTC),
    )
    client, user_id, service = playback_client()
    service.get_watched = AsyncMock(return_value=watched)
    service.set_watched = AsyncMock(return_value=watched)
    service.clear_watched_override = AsyncMock(return_value=watched)

    get_response = client.get(
        f"{PREFIX}/watched",
        params={"media_kind": "movie", "media_id": str(media_id)},
    )
    assert get_response.status_code == 200
    service.get_watched.assert_awaited_once_with(
        user_id=user_id,
        media_kind=MediaKind.movie,
        media_id=media_id,
    )

    put_response = client.put(
        f"{PREFIX}/watched",
        json={
            "media_kind": "movie",
            "media_id": str(media_id),
            "watched": True,
        },
    )
    assert put_response.status_code == 200
    service.set_watched.assert_awaited_once()
    assert service.set_watched.await_args.kwargs["user_id"] == user_id

    delete_response = client.delete(
        f"{PREFIX}/watched",
        params={"media_kind": "movie", "media_id": str(media_id)},
    )
    assert delete_response.status_code == 200
    service.clear_watched_override.assert_awaited_once_with(
        user_id=user_id,
        media_kind=MediaKind.movie,
        media_id=media_id,
    )
