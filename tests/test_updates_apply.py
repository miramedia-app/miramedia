"""Tests for disabled in-app Docker apply (fail closed)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from miramedia.updates.schemas import UpdateInfo
from miramedia.updates.service import UpdateService

UPDATES_PREFIX = "/api/v1/system/updates"


def _update_info() -> UpdateInfo:
    return UpdateInfo(
        enabled=True,
        current_version="1.0.0",
        latest_version="1.1.0",
        update_available=True,
        release_url="https://example.com/release",
        release_notes="notes",
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
        mock_svc.get_update_info.return_value = _update_info()

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


def test_is_apply_supported_always_false() -> None:
    assert UpdateService.is_apply_supported() is False


def test_get_update_info_reports_apply_unsupported() -> None:
    svc = UpdateService()
    with patch.object(svc, "_fetch_latest_release", return_value=None):
        info = svc.get_update_info()
    assert info.apply_supported is False


def test_trigger_apply_rejects_without_background_work() -> None:
    svc = UpdateService()
    with patch("threading.Thread") as mock_thread:
        accepted, detail = svc.trigger_apply(target_tag="latest")
    assert accepted is False
    assert detail is not None
    assert "disabled" in detail
    mock_thread.assert_not_called()


def test_post_updates_apply_returns_400_when_disabled() -> None:
    svc = MagicMock()
    svc.trigger_apply.return_value = (
        False,
        "in-app Docker apply is disabled; run "
        "`docker compose pull && docker compose up -d` on the host",
    )
    svc.get_apply_state.return_value = MagicMock()

    with updates_client(svc=svc) as (client, _):
        response = client.post(
            f"{UPDATES_PREFIX}/apply",
            json={"confirm": True},
        )

    assert response.status_code == 400
    assert "disabled" in response.json()["detail"]


def test_legacy_allow_in_app_apply_config_warns_but_loads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from miramedia.updates.config import UpdateConfig

    with caplog.at_level(logging.WARNING):
        cfg = UpdateConfig(allow_in_app_apply=True)
    assert cfg.allow_in_app_apply is True
    assert any("allow_in_app_apply" in r.message for r in caplog.records)
