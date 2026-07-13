"""Authoritative settings PUT derivation tests."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miramedia.config import MiraMediaConfig
from miramedia.settings.service import (
    apply_live_config_from_overrides,
    compute_mutation_overrides,
    get_toml_defaults,
)
from tests.fakes.repositories import FakeSettingsRepository

SETTINGS_PREFIX = "/api/v1/system/settings"


@contextmanager
def settings_client(
    *,
    repo: FakeSettingsRepository,
) -> Generator[tuple[TestClient, FakeSettingsRepository]]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.settings.dependencies import get_settings_repository

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    def _repo_dep() -> FakeSettingsRepository:
        return repo

    prior_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_settings_repository] = _repo_dep
    try:
        with (
            patch(
                "miramedia.scheduler.refresh_dynamic_schedules",
                new_callable=AsyncMock,
            ),
            patch(
                "miramedia.settings.router._cleanup_stale_media_preferences",
                new_callable=AsyncMock,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            try:
                yield client, repo
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)


def _toml_development() -> bool:
    return bool(MiraMediaConfig.load_isolated().misc.development)


def test_compute_mutation_overrides_removes_true_override_when_returning_to_toml() -> (
    None
):
    toml_dev = _toml_development()
    result = compute_mutation_overrides(
        {"misc": {"development": not toml_dev}},
        {"misc": {"development": toml_dev}},
    )
    assert "misc" not in result or "development" not in result.get("misc", {})


def test_stale_worker_put_false_clears_authoritative_true_override() -> None:
    toml_dev = _toml_development()
    repo = FakeSettingsRepository(overrides={"misc": {"development": not toml_dev}})
    apply_live_config_from_overrides({"misc": {"development": toml_dev}})

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"development": toml_dev}},
        )

    assert response.status_code == 200
    assert fake_repo.overrides == {}
    body = response.json()
    assert body["misc"]["development"] is toml_dev
    assert body["overrides"] == {}


def test_partial_patch_preserves_omitted_authoritative_fields() -> None:
    toml_dev = _toml_development()
    repo = FakeSettingsRepository(
        overrides={
            "misc": {
                "development": not toml_dev,
                "frontend_url": "https://override.example/",
            }
        }
    )

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"development": toml_dev}},
        )

    assert response.status_code == 200
    assert fake_repo.overrides == {
        "misc": {"frontend_url": "https://override.example/"}
    }
    assert response.json()["misc"]["development"] is toml_dev


def test_nested_key_removal_deletes_override_branch() -> None:
    defaults = get_toml_defaults()
    default_url = defaults["misc"]["frontend_url"]
    repo = FakeSettingsRepository(
        overrides={"misc": {"frontend_url": "https://override.example/"}}
    )

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"frontend_url": default_url}},
        )

    assert response.status_code == 200
    assert fake_repo.overrides == {}
    assert response.json()["overrides"] == {}


def test_put_revision_conflict_returns_409() -> None:
    repo = FakeSettingsRepository(revision=2)

    async def _conflict(*_args: object, **_kwargs: object) -> tuple[dict, int]:
        from miramedia.settings.repository import SettingsRevisionConflictError

        raise SettingsRevisionConflictError(2, 3)

    with settings_client(repo=repo) as (client, fake_repo):
        fake_repo.save_overrides_cas = _conflict  # type: ignore[method-assign]
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"development": True}},
        )

    assert response.status_code == 409


def test_failed_put_rollback_preserves_prior_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import miramedia.settings.mutation as mutation_mod

    repo = FakeSettingsRepository(overrides={"misc": {"development": True}})

    def _boom(_overrides: dict, _prospective: object) -> None:
        msg = "apply failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(
        mutation_mod,
        "_apply_live_mutation_critical_section",
        _boom,
    )

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"development": False}},
        )

    assert response.status_code == 500
    assert fake_repo.overrides == {"misc": {"development": True}}


def test_explicit_nullable_removes_authoritative_override() -> None:
    repo = FakeSettingsRepository(overrides={"auth": {"cookie_secure": True}})

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"auth": {"cookie_secure": None}},
        )

    assert response.status_code == 200
    assert fake_repo.overrides == {}
    assert response.json()["auth"]["cookie_secure"] is None


def test_omitted_nullable_preserves_authoritative_override() -> None:
    repo = FakeSettingsRepository(overrides={"auth": {"cookie_secure": True}})

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"development": True}},
        )

    assert response.status_code == 200
    assert fake_repo.overrides["auth"]["cookie_secure"] is True


def test_compute_mutation_overrides_removes_nullable_override_explicitly() -> None:
    result = compute_mutation_overrides(
        {"auth": {"cookie_secure": True}},
        {"auth": {"cookie_secure": None}},
    )
    assert result == {}


def test_compute_mutation_overrides_null_leaf_resets_non_null_default_to_toml() -> None:
    toml_dev = _toml_development()
    result = compute_mutation_overrides(
        {"misc": {"development": not toml_dev}},
        {"misc": {"development": None}},
    )
    assert "misc" not in result or "development" not in result.get("misc", {})


def test_explicit_null_leaf_resets_non_null_default_with_stale_live() -> None:
    toml_dev = _toml_development()
    repo = FakeSettingsRepository(overrides={"misc": {"development": not toml_dev}})
    apply_live_config_from_overrides({"misc": {"development": not toml_dev}})

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"development": None}},
        )

    assert response.status_code == 200
    assert fake_repo.overrides == {}
    body = response.json()
    assert body["misc"]["development"] is toml_dev
    assert body["overrides"] == {}
    assert MiraMediaConfig().misc.development is toml_dev


def test_explicit_null_section_resets_whole_misc_with_stale_live() -> None:
    toml_dev = _toml_development()
    defaults = get_toml_defaults()
    default_url = defaults["misc"]["frontend_url"]
    repo = FakeSettingsRepository(
        overrides={
            "misc": {
                "development": not toml_dev,
                "frontend_url": "https://override.example/",
            }
        }
    )
    apply_live_config_from_overrides(
        {
            "misc": {
                "development": not toml_dev,
                "frontend_url": "https://stale-live.example/",
            }
        }
    )

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": None},
        )

    assert response.status_code == 200
    assert fake_repo.overrides == {}
    body = response.json()
    assert body["misc"]["development"] is toml_dev
    assert body["misc"]["frontend_url"] == default_url
    assert body["overrides"] == {}
    assert MiraMediaConfig().misc.development is toml_dev
    assert str(MiraMediaConfig().misc.frontend_url) == str(default_url)


def test_invalid_effective_settings_returns_400_without_mutation() -> None:
    repo = FakeSettingsRepository(overrides={"misc": {"development": True}})
    prior_overrides = dict(repo.overrides)
    prior_live_dev = MiraMediaConfig().misc.development

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"frontend_url": "not-a-valid-url"}},
        )

    assert response.status_code == 400
    assert "frontend_url" in response.json()["detail"].lower()
    assert fake_repo.overrides == prior_overrides
    assert MiraMediaConfig().misc.development is prior_live_dev
