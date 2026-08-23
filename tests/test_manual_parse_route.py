"""DB-free route test for POST /api/v1/torrents/manual/parse."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from miramedia.movies.schemas import MovieId
from miramedia.shows.schemas import ShowId

MAGNET = (
    "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    "&dn=Inception.2010.1080p.BluRay.x264"
)
PREFIX = "/api/v1/torrents/manual/parse"


@dataclass(frozen=True, slots=True)
class _SlimShow:
    id: ShowId
    name: str
    year: int | None


@dataclass(frozen=True, slots=True)
class _SlimMovie:
    id: MovieId
    name: str
    year: int | None


@contextmanager
def manual_parse_client(
    *,
    shows: list[_SlimShow] | None = None,
    movies: list[_SlimMovie] | None = None,
) -> Generator[tuple[TestClient, MagicMock, MagicMock, AsyncMock]]:
    from miramedia.auth.users import current_active_user, current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_service
    from miramedia.shows.dependencies import get_show_service
    from miramedia.torrents.dependencies import get_torrent_repository

    inception_id = MovieId(uuid.uuid4())
    matrix_id = MovieId(uuid.uuid4())
    default_movies = [
        _SlimMovie(id=inception_id, name="Inception", year=2010),
        _SlimMovie(id=matrix_id, name="The Matrix", year=1999),
    ]

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        user.is_active = True
        user.is_verified = True
        return user

    async def _superuser() -> Any:
        return await _active_user()

    show_service = MagicMock()
    show_service.get_show_match_candidates = AsyncMock(return_value=shows or [])
    show_service.get_all_shows = AsyncMock()

    movie_service = MagicMock()
    movie_service.get_movie_match_candidates = AsyncMock(
        return_value=movies if movies is not None else default_movies
    )
    movie_service.get_all_movies = AsyncMock()

    torrent_repo = MagicMock()
    torrent_repo.save_manual_parse_token = AsyncMock()

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_show_service] = lambda: show_service
    app.dependency_overrides[get_movie_service] = lambda: movie_service
    app.dependency_overrides[get_torrent_repository] = lambda: torrent_repo
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, show_service, movie_service, torrent_repo.save_manual_parse_token
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_manual_parse_uses_slim_candidates_and_ranks_matches() -> None:
    with manual_parse_client() as (client, show_service, movie_service, save_token):
        response = client.post(PREFIX, data={"magnet_link": MAGNET})

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Inception.2010.1080p.BluRay.x264"
    assert body["download_token"]
    assert save_token.await_count == 1

    show_service.get_show_match_candidates.assert_awaited_once()
    movie_service.get_movie_match_candidates.assert_awaited_once()
    show_service.get_all_shows.assert_not_awaited()
    movie_service.get_all_movies.assert_not_awaited()

    candidates = body["candidates"]
    assert candidates
    assert candidates[0]["media_type"] == "movie"
    assert candidates[0]["media_name"] == "Inception"
    assert candidates[0]["media_year"] == 2010
    assert candidates[0]["confidence"] > 0.3


def test_manual_parse_requires_magnet_or_file() -> None:
    with manual_parse_client() as (client, *_):
        response = client.post(PREFIX, data={})

    assert response.status_code == 400
