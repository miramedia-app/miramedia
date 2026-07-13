"""Route tests for manual update checks offloaded from the event loop."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from miramedia.updates.schemas import UpdateInfo

UPDATES_PREFIX = "/api/v1/system/updates"


def _update_info(
    *,
    update_available: bool,
    latest_version: str | None,
) -> UpdateInfo:
    return UpdateInfo(
        enabled=True,
        current_version="1.0.0",
        latest_version=latest_version,
        update_available=update_available,
        release_url="https://example.com/release" if update_available else None,
        release_notes="notes" if update_available else None,
        published_at=None,
        last_checked_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        repo="org/repo",
        apply_supported=False,
    )


@contextmanager
def updates_client(
    *,
    superuser: bool = True,
    svc: MagicMock | None = None,
) -> Generator[tuple[TestClient, MagicMock]]:
    from miramedia.auth.users import current_superuser
    from miramedia.main import app
    from miramedia.updates.dependencies import get_update_service

    mock_svc = svc or MagicMock()
    if svc is None:
        mock_svc.get_update_info.return_value = _update_info(
            update_available=True,
            latest_version="1.1.0",
        )

    async def _superuser() -> Any:
        if not superuser:
            raise HTTPException(status_code=403, detail="Forbidden")
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    def _svc_dep() -> MagicMock:
        return mock_svc

    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_update_service] = _svc_dep
    try:
        client = TestClient(app, raise_server_exceptions=False)
        yield client, mock_svc
    finally:
        app.dependency_overrides.clear()


def test_get_updates_offloads_sync_service_to_thread() -> None:
    with (
        updates_client() as (client, svc),
        patch(
            "miramedia.updates.router.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread,
    ):
        response = client.get(UPDATES_PREFIX)

    assert response.status_code == 200
    assert response.json()["update_available"] is True
    mock_to_thread.assert_called_once()
    assert mock_to_thread.call_args.args[0] is svc.get_update_info
    assert mock_to_thread.call_args.args[1] is False


def test_get_updates_returns_cached_no_update_response_off_thread() -> None:
    svc = MagicMock()
    svc.get_update_info.return_value = _update_info(
        update_available=False,
        latest_version="1.0.0",
    )

    with (
        updates_client(svc=svc) as (client, _),
        patch(
            "miramedia.updates.router.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread,
    ):
        response = client.get(UPDATES_PREFIX)

    assert response.status_code == 200
    body = response.json()
    assert body["update_available"] is False
    assert body["latest_version"] == "1.0.0"
    assert body["last_checked_at"] is not None
    mock_to_thread.assert_called_once()
    assert mock_to_thread.call_args.args[1] is False


def test_get_updates_force_query_offloads_with_force_true() -> None:
    with (
        updates_client() as (client, svc),
        patch(
            "miramedia.updates.router.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread,
    ):
        response = client.get(f"{UPDATES_PREFIX}?force=true")

    assert response.status_code == 200
    mock_to_thread.assert_called_once()
    assert mock_to_thread.call_args.args[0] is svc.get_update_info
    assert mock_to_thread.call_args.args[1] is True


def test_post_updates_check_invalidates_cache_and_offloads() -> None:
    svc = MagicMock()
    svc.get_update_info.return_value = _update_info(
        update_available=False,
        latest_version="1.0.0",
    )

    with (
        updates_client(svc=svc) as (client, mock_svc),
        patch(
            "miramedia.updates.router.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ) as mock_to_thread,
    ):
        response = client.post(f"{UPDATES_PREFIX}/check")

    assert response.status_code == 200
    assert response.json()["update_available"] is False
    mock_svc.invalidate_cache.assert_called_once()
    mock_to_thread.assert_called_once()
    assert mock_to_thread.call_args.args[0] is mock_svc.get_update_info
    assert mock_to_thread.call_args.args[1] is True


def test_get_updates_maps_service_errors_to_500() -> None:
    svc = MagicMock()
    svc.get_update_info.side_effect = RuntimeError("network down")

    with (
        updates_client(svc=svc) as (client, _),
        patch(
            "miramedia.updates.router.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ),
    ):
        response = client.get(UPDATES_PREFIX)

    assert response.status_code == 500
    assert "RuntimeError" in response.json()["detail"]


def test_post_updates_check_maps_service_errors_to_500() -> None:
    svc = MagicMock()
    svc.get_update_info.side_effect = ValueError("bad payload")

    with (
        updates_client(svc=svc) as (client, _),
        patch(
            "miramedia.updates.router.asyncio.to_thread",
            wraps=asyncio.to_thread,
        ),
    ):
        response = client.post(f"{UPDATES_PREFIX}/check")

    assert response.status_code == 500
    assert "ValueError" in response.json()["detail"]


def test_get_updates_requires_superuser() -> None:
    with updates_client(superuser=False) as (client, svc):
        response = client.get(UPDATES_PREFIX)
    assert response.status_code == 403
    svc.get_update_info.assert_not_called()


def test_post_updates_check_requires_superuser() -> None:
    with updates_client(superuser=False) as (client, svc):
        response = client.post(f"{UPDATES_PREFIX}/check")
    assert response.status_code == 403
    svc.invalidate_cache.assert_not_called()
    svc.get_update_info.assert_not_called()
