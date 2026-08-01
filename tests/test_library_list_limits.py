"""DB-free contract tests for bounded SQL pagination on library list routes."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from miramedia.movies.schemas import PublicMovie
from miramedia.shows.schemas import PublicShow
from tests.fakes.repositories import make_movie, make_show

SHOWS_PREFIX = "/api/v1/shows"
MOVIES_PREFIX = "/api/v1/movies"


def _public_show() -> PublicShow:
    return PublicShow.model_validate(make_show(name="Listed Show"))


def _public_movie() -> PublicMovie:
    return PublicMovie.model_validate(make_movie(name="Listed Movie"))


@contextmanager
def _library_list_client(
    *,
    service_dep: Any,
    paginated_method: str,
    full_library_method: str,
    sample_item: Callable[[], PublicShow | PublicMovie],
) -> Generator[tuple[TestClient, MagicMock]]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = False
        return user

    service = MagicMock()
    page_item = sample_item()
    setattr(
        service,
        paginated_method,
        AsyncMock(return_value=([page_item], 42)),
    )
    setattr(
        service,
        full_library_method,
        AsyncMock(return_value=[page_item]),
    )

    prior_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[service_dep] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, service
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)


@contextmanager
def shows_list_client() -> Generator[tuple[TestClient, MagicMock]]:
    from miramedia.shows.dependencies import get_show_service

    with _library_list_client(
        service_dep=get_show_service,
        paginated_method="get_paginated_public_shows",
        full_library_method="get_all_public_shows",
        sample_item=_public_show,
    ) as pair:
        yield pair


@contextmanager
def movies_list_client() -> Generator[tuple[TestClient, MagicMock]]:
    from miramedia.movies.dependencies import get_movie_service

    with _library_list_client(
        service_dep=get_movie_service,
        paginated_method="get_paginated_public_movies",
        full_library_method="get_all_public_movies",
        sample_item=_public_movie,
    ) as pair:
        yield pair


@pytest.mark.parametrize(
    ("client_factory", "prefix", "paginated_method", "full_library_method"),
    [
        (
            shows_list_client,
            SHOWS_PREFIX,
            "get_paginated_public_shows",
            "get_all_public_shows",
        ),
        (
            movies_list_client,
            MOVIES_PREFIX,
            "get_paginated_public_movies",
            "get_all_public_movies",
        ),
    ],
)
def test_omitted_limit_uses_default_pagination(
    client_factory: Callable[[], Any],
    prefix: str,
    paginated_method: str,
    full_library_method: str,
) -> None:
    with client_factory() as (client, service):
        response = client.get(prefix)

    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "42"
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1

    paginated = getattr(service, paginated_method)
    paginated.assert_awaited_once_with(
        offset=0,
        limit=100,
        query=None,
        sort=None,
        libraries=None,
        excluded_libraries=None,
        genres=None,
        excluded_genres=None,
        decades=None,
        excluded_decades=None,
        statuses=None,
        excluded_statuses=None,
        **({"airing": None, "excluded_airing": None} if prefix == SHOWS_PREFIX else {}),
    )
    getattr(service, full_library_method).assert_not_called()


@pytest.mark.parametrize(
    ("client_factory", "prefix", "paginated_method", "full_library_method"),
    [
        (
            shows_list_client,
            SHOWS_PREFIX,
            "get_paginated_public_shows",
            "get_all_public_shows",
        ),
        (
            movies_list_client,
            MOVIES_PREFIX,
            "get_paginated_public_movies",
            "get_all_public_movies",
        ),
    ],
)
def test_limit_above_max_returns_422_without_service_call(
    client_factory: Callable[[], Any],
    prefix: str,
    paginated_method: str,
    full_library_method: str,
) -> None:
    with client_factory() as (client, service):
        response = client.get(prefix, params={"limit": 501})

    assert response.status_code == 422, response.text
    getattr(service, paginated_method).assert_not_called()
    getattr(service, full_library_method).assert_not_called()


@pytest.mark.parametrize(
    ("client_factory", "prefix", "paginated_method", "full_library_method"),
    [
        (
            shows_list_client,
            SHOWS_PREFIX,
            "get_paginated_public_shows",
            "get_all_public_shows",
        ),
        (
            movies_list_client,
            MOVIES_PREFIX,
            "get_paginated_public_movies",
            "get_all_public_movies",
        ),
    ],
)
def test_explicit_limit_offset_and_filters_forwarded(
    client_factory: Callable[[], Any],
    prefix: str,
    paginated_method: str,
    full_library_method: str,
) -> None:
    params: dict[str, Any] = {
        "limit": 50,
        "offset": 20,
        "q": "alpha",
        "sort": "name",
        "library": ["Main"],
        "exclude_library": ["Kids"],
        "genre": ["Drama"],
        "exclude_genre": ["Horror"],
        "decade": [1990, 2000],
        "exclude_decade": [1980],
        "status": ["wanted"],
        "exclude_status": ["skipped"],
    }
    if prefix == SHOWS_PREFIX:
        params["airing"] = ["ended"]
        params["exclude_airing"] = ["airing"]

    with client_factory() as (client, service):
        response = client.get(prefix, params=params)

    assert response.status_code == 200, response.text
    assert response.headers["X-Total-Count"] == "42"

    expected: dict[str, Any] = {
        "offset": 20,
        "limit": 50,
        "query": "alpha",
        "sort": "name",
        "libraries": ["Main"],
        "excluded_libraries": ["Kids"],
        "genres": ["Drama"],
        "excluded_genres": ["Horror"],
        "decades": [1990, 2000],
        "excluded_decades": [1980],
        "statuses": ["wanted"],
        "excluded_statuses": ["skipped"],
    }
    if prefix == SHOWS_PREFIX:
        expected["airing"] = ["ended"]
        expected["excluded_airing"] = ["airing"]

    getattr(service, paginated_method).assert_awaited_once_with(**expected)
    getattr(service, full_library_method).assert_not_called()


@pytest.mark.parametrize(
    "openapi_path",
    [
        "/api/v1/shows",
        "/api/v1/movies",
    ],
)
def test_openapi_limit_parameter_is_optional_bounded_integer(
    openapi_path: str,
) -> None:
    from miramedia.main import app

    schema = app.openapi()
    get_op = schema["paths"][openapi_path]["get"]
    limit_param = next(p for p in get_op["parameters"] if p["name"] == "limit")

    assert limit_param["required"] is False
    limit_schema = limit_param["schema"]
    assert limit_schema.get("type") == "integer"
    assert limit_schema.get("default") == 100
    assert limit_schema.get("maximum") == 500
    assert limit_schema.get("exclusiveMinimum") == 0
    assert "anyOf" not in limit_schema
    assert "nullable" not in limit_schema
    assert limit_schema.get("type") != "null"
