"""Route-level auth tests for GET /api/v1/requests list ownership."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

PREFIX = "/api/v1/requests"


@contextmanager
def list_requests_client(
    *,
    is_superuser: bool,
    mine: bool | None = None,
    user_id: uuid.UUID | None = None,
) -> Generator[tuple[TestClient, uuid.UUID, AsyncMock]]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.requests.dependencies import (
        get_request_service,
        require_requests_enabled,
    )

    user_id = user_id or uuid.uuid4()

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = user_id
        user.is_superuser = is_superuser
        return user

    list_requests = AsyncMock(return_value=[])

    async def _request_service() -> Any:
        service = MagicMock()
        service.list_requests = list_requests
        yield service

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[require_requests_enabled] = lambda: None
    app.dependency_overrides[get_request_service] = _request_service
    try:
        client = TestClient(app, raise_server_exceptions=False)
        params: dict[str, bool] = {}
        if mine is not None:
            params["mine"] = mine
        response = client.get(PREFIX, params=params)
        assert response.status_code == 200, response.text
        yield client, user_id, list_requests
    finally:
        app.dependency_overrides.clear()


def test_list_requests_ordinary_user_without_mine_filters_to_self() -> None:
    with list_requests_client(is_superuser=False, mine=None) as (
        _client,
        user_id,
        list_requests,
    ):
        list_requests.assert_awaited_once_with(
            status=None,
            media_type=None,
            requested_by_id=user_id,
        )


def test_list_requests_ordinary_user_with_mine_false_filters_to_self() -> None:
    with list_requests_client(is_superuser=False, mine=False) as (
        _client,
        user_id,
        list_requests,
    ):
        list_requests.assert_awaited_once_with(
            status=None,
            media_type=None,
            requested_by_id=user_id,
        )


def test_list_requests_ordinary_user_with_mine_true_filters_to_self() -> None:
    with list_requests_client(is_superuser=False, mine=True) as (
        _client,
        user_id,
        list_requests,
    ):
        list_requests.assert_awaited_once_with(
            status=None,
            media_type=None,
            requested_by_id=user_id,
        )


def test_list_requests_superuser_without_mine_lists_all() -> None:
    with list_requests_client(is_superuser=True, mine=None) as (
        _client,
        _user_id,
        list_requests,
    ):
        list_requests.assert_awaited_once_with(
            status=None,
            media_type=None,
            requested_by_id=None,
        )


def test_list_requests_superuser_with_mine_false_lists_all() -> None:
    with list_requests_client(is_superuser=True, mine=False) as (
        _client,
        _user_id,
        list_requests,
    ):
        list_requests.assert_awaited_once_with(
            status=None,
            media_type=None,
            requested_by_id=None,
        )


def test_list_requests_superuser_with_mine_true_filters_to_self() -> None:
    with list_requests_client(is_superuser=True, mine=True) as (
        _client,
        user_id,
        list_requests,
    ):
        list_requests.assert_awaited_once_with(
            status=None,
            media_type=None,
            requested_by_id=user_id,
        )


def test_list_requests_two_ordinary_users_each_filter_to_self() -> None:
    """Each ordinary caller must pass only their own ID, never None."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    for user_id in (user_a, user_b):
        with list_requests_client(
            is_superuser=False,
            mine=None,
            user_id=user_id,
        ) as (
            _client,
            observed_id,
            list_requests,
        ):
            assert observed_id == user_id
            list_requests.assert_awaited_once_with(
                status=None,
                media_type=None,
                requested_by_id=user_id,
            )
