"""Regression: /recommended handlers release the DB session before provider fan-out."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from miramedia.recommended_discovery_cache import (
    _RECOMMENDED_MOVIES_CACHE,
    _RECOMMENDED_SHOWS_CACHE,
)


@pytest.fixture
def recommended_client(
    override_dependency: Callable[[Callable, object], None],
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[..., tuple[TestClient, list[str]]]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_service
    from miramedia.shows.dependencies import get_show_service

    def make(*, kind: str) -> tuple[TestClient, list[str], str]:
        order: list[str] = []
        db = MagicMock()

        async def _release(_session: object) -> None:
            order.append("release")

        if kind == "movie":
            service = MagicMock()
            service.movie_repository.db = db
            service.discover_movies = AsyncMock(
                side_effect=lambda **_kwargs: order.append("discover") or []
            )
            service.annotate_search_results = AsyncMock(
                side_effect=lambda results: results
            )
            get_service = get_movie_service
            monkeypatch.setattr(
                "miramedia.movies.router.release_session_before_external_io",
                _release,
            )
            _RECOMMENDED_MOVIES_CACHE._cache.clear()
            path = "/api/v1/movies/recommended"
        else:
            service = MagicMock()
            service.show_repository.db = db
            service.discover_shows = AsyncMock(
                side_effect=lambda **_kwargs: order.append("discover") or []
            )
            service.annotate_search_results = AsyncMock(
                side_effect=lambda results: results
            )
            get_service = get_show_service
            monkeypatch.setattr(
                "miramedia.shows.router.release_session_before_external_io",
                _release,
            )
            _RECOMMENDED_SHOWS_CACHE._cache.clear()
            path = "/api/v1/shows/recommended"

        async def _stub_session() -> None:
            yield db

        async def _active_user() -> MagicMock:
            return MagicMock()

        def _service() -> MagicMock:
            return service

        override_dependency(get_session, _stub_session)
        override_dependency(current_active_user, _active_user)
        override_dependency(get_service, _service)

        return TestClient(app, raise_server_exceptions=False), order, path

    return make


def test_popular_movies_releases_session_before_cache_discover(
    recommended_client,
) -> None:
    client, order, path = recommended_client(kind="movie")
    response = client.get(path)
    assert response.status_code == 200
    assert order.index("release") < order.index("discover")


def test_recommended_shows_releases_session_before_cache_discover(
    recommended_client,
) -> None:
    client, order, path = recommended_client(kind="show")
    response = client.get(path)
    assert response.status_code == 200
    assert order.index("release") < order.index("discover")
