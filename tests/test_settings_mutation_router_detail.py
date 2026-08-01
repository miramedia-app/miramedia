"""Router regression tests for operator-safe settings mutation errors."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from miramedia.settings.mutation import (
    SETTINGS_MUTATION_FAILED_DETAIL,
    SETTINGS_MUTATION_POSTCOMMIT_DETAIL,
    SettingsMutationError,
)

SETTINGS_PREFIX = "/api/v1/system/settings"


@contextmanager
def settings_client() -> Generator[TestClient]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.settings.dependencies import get_settings_repository
    from tests.fakes.repositories import FakeSettingsRepository

    fake_repo = FakeSettingsRepository()

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    def _repo_dep() -> FakeSettingsRepository:
        return fake_repo

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_settings_repository] = _repo_dep
    try:
        with patch(
            "miramedia.settings.router.refresh_dynamic_schedules",
            new_callable=AsyncMock,
            create=True,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            try:
                yield client
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_settings_put_hides_arbitrary_mutation_error_message(
    monkeypatch: Any,
) -> None:
    leaked = "postgres://admin:secret@db.internal:5432/miramedia"

    async def _boom(**_kwargs: object) -> dict:
        raise SettingsMutationError(leaked)

    monkeypatch.setattr(
        "miramedia.settings.router.execute_settings_mutation",
        _boom,
    )

    with settings_client() as client:
        response = client.put(SETTINGS_PREFIX, json={"misc": {"development": True}})

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == SETTINGS_MUTATION_FAILED_DETAIL
    assert leaked not in response.text


def test_settings_put_returns_safe_postcommit_detail(
    monkeypatch: Any,
) -> None:
    async def _boom(**_kwargs: object) -> dict:
        raise SettingsMutationError(SETTINGS_MUTATION_POSTCOMMIT_DETAIL)

    monkeypatch.setattr(
        "miramedia.settings.router.execute_settings_mutation",
        _boom,
    )

    with settings_client() as client:
        response = client.put(SETTINGS_PREFIX, json={"misc": {"development": True}})

    assert response.status_code == 500
    assert response.json()["detail"] == SETTINGS_MUTATION_POSTCOMMIT_DETAIL
