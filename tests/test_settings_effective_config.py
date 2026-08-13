"""Pure settings read coherence tests."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from miramedia.config import MiraMediaConfig
from miramedia.settings.schemas import SystemSettingsUpdate
from miramedia.settings.service import (
    SETTINGS_SECTIONS,
    apply_live_config_from_overrides,
    build_isolated_config,
    get_effective_config,
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
        with patch(
            "miramedia.settings.router.refresh_dynamic_schedules",
            create=True,
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


def test_get_effective_config_ignores_stale_live_singleton() -> None:
    """TOML A + stale live B + DB overrides {} must return A, not B."""
    toml_a = _toml_development()
    stale_b = not toml_a
    apply_live_config_from_overrides({"misc": {"development": stale_b}})
    assert MiraMediaConfig().misc.development is stale_b

    effective = get_effective_config({})
    assert effective["misc"]["development"] is toml_a


def test_get_effective_config_reflects_authoritative_overrides() -> None:
    """DB override C wins over stale live singleton."""
    toml_a = _toml_development()
    override_c = not toml_a
    apply_live_config_from_overrides({"misc": {"development": toml_a}})

    effective = get_effective_config({"misc": {"development": override_c}})
    assert effective["misc"]["development"] is override_c
    assert build_isolated_config({"misc": {"development": override_c}}).misc.development


def test_get_settings_returns_toml_not_stale_live_when_overrides_empty() -> None:
    toml_a = _toml_development()
    stale_b = not toml_a
    apply_live_config_from_overrides({"misc": {"development": stale_b}})

    repo = FakeSettingsRepository(overrides={})
    with settings_client(repo=repo) as (client, _fake_repo):
        response = client.get(SETTINGS_PREFIX)

    assert response.status_code == 200
    body = response.json()
    assert body["overrides"] == {}
    assert body["misc"]["development"] is toml_a
    assert body["misc"]["development"] is not stale_b


def test_get_settings_returns_override_not_stale_live() -> None:
    toml_a = _toml_development()
    override_c = not toml_a
    apply_live_config_from_overrides({"misc": {"development": toml_a}})

    repo = FakeSettingsRepository(overrides={"misc": {"development": override_c}})
    with settings_client(repo=repo) as (client, _fake_repo):
        response = client.get(SETTINGS_PREFIX)

    assert response.status_code == 200
    body = response.json()
    assert body["overrides"]["misc"]["development"] is override_c
    assert body["misc"]["development"] is override_c


def test_effective_config_is_a_valid_settings_update() -> None:
    """GET must not include keys PUT forbids, or the settings editor 422s on save."""
    effective = get_effective_config({})
    payload = {section: effective[section] for section in SETTINGS_SECTIONS}
    SystemSettingsUpdate.model_validate(payload)
    assert "admin_emails" not in effective["auth"]
    assert "token_secret" not in effective["auth"]
