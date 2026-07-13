"""DB-free route test for GET /api/v1/movies/{movie_id}/torrents."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException
from fastapi.testclient import TestClient

from miramedia.torrents.schemas import Quality, RichTorrent, TorrentStatus
from tests.fakes.repositories import make_movie

PREFIX = "/api/v1/movies"


@contextmanager
def movie_torrents_client(
    *,
    torrents: list[RichTorrent] | None = None,
    anonymous: bool = False,
) -> Generator[TestClient]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_by_id, get_movie_service

    movie = make_movie(name="Inception")
    rich = (
        torrents
        if torrents is not None
        else [
            RichTorrent(
                id=uuid.uuid4(),
                status=TorrentStatus.downloading,
                progress=0.42,
                num_peers=3,
                num_seeds=12,
                download_speed=1024,
                title="Inception.2010.1080p",
                quality=Quality.fullhd,
                hash="b" * 40,
            )
        ]
    )

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        if anonymous:
            raise HTTPException(status_code=401, detail="Unauthorized")
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = False
        return user

    async def _movie_dep() -> Any:
        return movie

    service = MagicMock()
    service.get_torrents_for_movie = AsyncMock(return_value=rich)

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[get_movie_by_id] = _movie_dep
    app.dependency_overrides[get_movie_service] = lambda: service
    try:
        client = TestClient(app, raise_server_exceptions=False)
        yield client
    finally:
        app.dependency_overrides.clear()


def test_get_movie_torrents_returns_200_list() -> None:
    with movie_torrents_client() as client:
        response = client.get(f"{PREFIX}/{uuid.uuid4()}/torrents")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["title"] == "Inception.2010.1080p"
    assert body[0]["progress"] == 0.42
    assert "status" in body[0]
    assert "hash" in body[0]


def test_get_movie_torrents_empty_list() -> None:
    with movie_torrents_client(torrents=[]) as client:
        response = client.get(f"{PREFIX}/{uuid.uuid4()}/torrents")
    assert response.status_code == 200, response.text
    assert response.json() == []
