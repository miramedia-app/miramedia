"""Route-level tests for settings mutation endpoints.

Covers ``PUT /settings``, ``DELETE /settings``, ``POST /settings/import``, and
``POST /settings/override/clear`` on the system router, including auth gates and
``_cleanup_stale_media_preferences`` side effects.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.fakes.repositories import FakeSettingsRepository

SETTINGS_PREFIX = "/api/v1/system/settings"


@contextmanager
def settings_client(
    *,
    superuser: bool = True,
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
        if not superuser:
            raise HTTPException(status_code=403, detail="Forbidden")
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


def test_get_settings_returns_200() -> None:
    with settings_client() as (client, _repo):
        response = client.get(SETTINGS_PREFIX)
    assert response.status_code == 200
    body = response.json()
    assert "misc" in body
    assert "overrides" in body


def test_put_settings_round_trip() -> None:
    repo = FakeSettingsRepository()
    payload = {"misc": {"development": True}}
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(SETTINGS_PREFIX, json=payload)
    assert response.status_code == 200
    assert fake_repo.save_calls, "expected save_overrides to be called"
    body = response.json()
    assert body["misc"]["development"] is True
    assert body["overrides"].get("misc", {}).get("development") is True


def test_put_settings_invalid_payload_returns_422() -> None:
    with settings_client() as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"development": "not-a-boolean"}},
        )
    assert response.status_code == 422
    assert not fake_repo.save_calls


def test_delete_settings_invokes_reset() -> None:
    repo = FakeSettingsRepository(overrides={"misc": {"development": True}})
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.delete(SETTINGS_PREFIX)
    assert response.status_code == 204
    assert fake_repo.reset_called


def test_post_settings_import_valid_persists() -> None:
    repo = FakeSettingsRepository()
    import_body = {
        "overrides": {"misc": {"continuous_download": False}},
        "mode": "merge",
    }
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.post(f"{SETTINGS_PREFIX}/import", json=import_body)
    assert response.status_code == 200
    assert fake_repo.save_calls
    body = response.json()
    assert body["overrides"]["misc"]["continuous_download"] is False


def test_post_settings_import_unknown_section_rejected() -> None:
    repo = FakeSettingsRepository(overrides={"misc": {"development": True}})
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.post(
            f"{SETTINGS_PREFIX}/import",
            json={"overrides": {"not_a_real_section": {"x": 1}}, "mode": "merge"},
        )
    assert response.status_code == 400
    assert len(fake_repo.save_calls) == 0
    assert repo.overrides == {"misc": {"development": True}}


def test_post_settings_import_malformed_json_returns_422() -> None:
    repo = FakeSettingsRepository(overrides={"misc": {"development": True}})
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.post(
            f"{SETTINGS_PREFIX}/import",
            content=b'{"overrides": {',
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 422
    assert len(fake_repo.save_calls) == 0


def test_post_settings_override_clear() -> None:
    repo = FakeSettingsRepository(overrides={"misc": {"development": True}})
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.post(
            f"{SETTINGS_PREFIX}/override/clear",
            json={"path": ["misc", "development"]},
        )
    assert response.status_code == 200
    assert fake_repo.save_calls
    assert "development" not in fake_repo.overrides.get("misc", {})


@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [
        ("put", SETTINGS_PREFIX, {"json": {"misc": {"development": True}}}),
        ("delete", SETTINGS_PREFIX, {}),
        (
            "post",
            f"{SETTINGS_PREFIX}/import",
            {"json": {"overrides": {"misc": {"development": True}}, "mode": "merge"}},
        ),
        (
            "post",
            f"{SETTINGS_PREFIX}/override/clear",
            {"json": {"path": ["misc", "development"]}},
        ),
    ],
)
def test_cleanup_runs_on_mutating_paths(
    method: str, url: str, kwargs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def _counting_cleanup(_db: Any) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(
        "miramedia.settings.router._cleanup_stale_media_preferences",
        _counting_cleanup,
    )
    with settings_client() as (client, _repo):
        response = getattr(client, method)(url, **kwargs)
    assert response.status_code in (200, 204)
    assert calls >= 1


@pytest.mark.parametrize(
    ("method", "url", "kwargs"),
    [
        ("put", SETTINGS_PREFIX, {"json": {"misc": {"development": True}}}),
        ("delete", SETTINGS_PREFIX, {}),
        (
            "post",
            f"{SETTINGS_PREFIX}/import",
            {"json": {"overrides": {"misc": {"development": True}}, "mode": "merge"}},
        ),
        (
            "post",
            f"{SETTINGS_PREFIX}/override/clear",
            {"json": {"path": ["misc", "development"]}},
        ),
    ],
)
def test_mutating_routes_reject_non_superuser(
    method: str, url: str, kwargs: dict[str, Any]
) -> None:
    with settings_client(superuser=False) as (client, _repo):
        response = getattr(client, method)(url, **kwargs)
    assert response.status_code == 403


def test_mutating_routes_reject_anonymous() -> None:
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    app.dependency_overrides[get_session] = _stub_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.put(SETTINGS_PREFIX, json={"misc": {"development": True}})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401
