"""API boundary tests for restart-only token_secret settings."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miramedia.config import MiraMediaConfig
from tests.fakes.repositories import FakeSettingsRepository
from tests.oauth_test_helpers import ENDPOINT_DEFAULT, build_openid_client_mock

SETTINGS_PREFIX = "/api/v1/system/settings"


@contextmanager
def settings_client(
    *,
    repo: FakeSettingsRepository | None = None,
) -> Generator[tuple[TestClient, FakeSettingsRepository]]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.settings.dependencies import get_settings_repository

    fake_repo = repo or FakeSettingsRepository()

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    def _repo_dep() -> FakeSettingsRepository:
        return fake_repo

    prior_overrides = dict(app.dependency_overrides)
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
                yield client, fake_repo
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)


def test_put_rejects_token_secret_in_auth_section() -> None:
    with settings_client() as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"auth": {"token_secret": "a" * 64}},
        )
    assert response.status_code == 422
    assert not fake_repo.save_calls


def test_import_rejects_token_secret() -> None:
    with settings_client() as (client, fake_repo):
        response = client.post(
            f"{SETTINGS_PREFIX}/import",
            json={
                "mode": "merge",
                "overrides": {"auth": {"token_secret": "a" * 64}},
            },
        )
    assert response.status_code == 400
    assert "cannot be changed at runtime" in response.json()["detail"]
    assert not fake_repo.save_calls


def test_clear_rejects_token_secret_path() -> None:
    with settings_client() as (client, fake_repo):
        response = client.post(
            f"{SETTINGS_PREFIX}/override/clear",
            json={"path": ["auth", "token_secret"]},
        )
    assert response.status_code == 400
    assert "cannot be changed at runtime" in response.json()["detail"]
    assert not fake_repo.save_calls


def test_reset_preserves_live_token_secret() -> None:
    live_secret = MiraMediaConfig().auth.token_secret
    repo = FakeSettingsRepository(overrides={"misc": {"development": True}})
    with settings_client(repo=repo) as (client, _fake_repo):
        response = client.delete(SETTINGS_PREFIX)
    assert response.status_code == 204
    assert MiraMediaConfig().auth.token_secret == live_secret


def test_export_omits_token_secret() -> None:
    repo = FakeSettingsRepository(
        overrides={"auth": {"token_secret": "b" * 64, "email_password_resets": True}}
    )
    with settings_client(repo=repo) as (client, _fake_repo):
        response = client.get(f"{SETTINGS_PREFIX}/export")
    assert response.status_code == 200
    exported = response.json()["overrides"]
    assert "token_secret" not in exported.get("auth", {})
    assert exported["auth"]["email_password_resets"] is True


def test_put_cas_increments_revision(fake_openid: list[MagicMock]) -> None:  # noqa: ARG001
    repo = FakeSettingsRepository()
    with settings_client(repo=repo) as (client, fake_repo):
        before_revision = fake_repo.revision
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"development": True}},
        )
    assert response.status_code == 200
    assert fake_repo.revision == before_revision + 1
    assert fake_repo.cas_calls[-1][1] == before_revision


@pytest.fixture
def fake_openid(monkeypatch: pytest.MonkeyPatch) -> list[MagicMock]:
    created: list[MagicMock] = []

    def _factory(**kwargs: object) -> MagicMock:
        endpoint = str(kwargs.get("openid_configuration_endpoint", ENDPOINT_DEFAULT))
        client = build_openid_client_mock(
            endpoint=endpoint,
            name=str(kwargs.get("name", "Provider")),
            client_id=str(kwargs.get("client_id", "client-a")),
            client_secret=str(kwargs.get("client_secret", "secret")),
        )
        created.append(client)
        return client

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _factory)
    return created
