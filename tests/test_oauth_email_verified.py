"""OAuth callback email_verified gating tests."""

from __future__ import annotations

import types
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users.router.common import ErrorCode

from tests.oauth_test_helpers import (
    ENDPOINT_DEFAULT,
    build_openid_client_mock,
)

SETTINGS_PREFIX = "/api/v1/system/settings"
OIDC_AUTHORIZE_PATH = "/api/v1/auth/oauth/authorize"
OIDC_CALLBACK_PATH = "/api/v1/auth/oauth/callback"


@pytest.fixture(autouse=True)
def _noop_legacy_oauth_reconcile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miramedia.auth.oauth_router.reconcile_legacy_oauth_account",
        AsyncMock(),
    )


@pytest.fixture(autouse=True)
def _reset_auth_state() -> Generator[None]:
    from miramedia.auth.runtime import reset_auth_runtime_for_tests
    from miramedia.settings.mutation import reset_settings_mutation_state_for_tests
    from miramedia.settings.service import apply_live_config_from_overrides

    reset_auth_runtime_for_tests()
    reset_settings_mutation_state_for_tests()
    apply_live_config_from_overrides({})
    yield
    reset_auth_runtime_for_tests()
    reset_settings_mutation_state_for_tests()
    apply_live_config_from_overrides({})


def _openid_client_with_profile(
    *,
    email_verified: bool | None,
    email: str = "user@example.com",
    account_id: str = "account-1",
) -> MagicMock:
    client = build_openid_client_mock(endpoint=ENDPOINT_DEFAULT)
    profile: dict[str, object] = {"sub": account_id, "email": email}
    if email_verified is not None:
        profile["email_verified"] = email_verified
    client.get_profile = AsyncMock(return_value=profile)
    client.get_id_email = AsyncMock(return_value=(account_id, email))
    return client


@pytest.fixture
def fake_openid(monkeypatch: pytest.MonkeyPatch) -> list[MagicMock]:
    created: list[MagicMock] = []

    def _factory(**_kwargs: object) -> MagicMock:
        client = _openid_client_with_profile(email_verified=True)
        created.append(client)
        return client

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _factory)
    return created


@contextmanager
def settings_client() -> Generator[TestClient]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.settings.dependencies import get_settings_repository
    from tests.fakes.repositories import FakeSettingsRepository

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    def _repo_dep() -> FakeSettingsRepository:
        return FakeSettingsRepository()

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
                yield client
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)


def _oidc_payload(*, enabled: bool = True) -> dict[str, Any]:
    return {
        "auth": {
            "openid_connect": {
                "enabled": enabled,
                "name": "ConfiguredProvider",
                "client_id": "client-a",
                "client_secret": "secret",
                "configuration_endpoint": ENDPOINT_DEFAULT,
            }
        }
    }


def _oauth_authorize_cookies(authorize_response: object) -> dict[str, str]:
    import re

    from fastapi_users.router.oauth import CSRF_TOKEN_COOKIE_NAME

    from miramedia.auth.oauth_state import OAUTH_SNAPSHOT_COOKIE_NAME

    headers = getattr(authorize_response, "headers", {})
    cookies: dict[str, str] = {}
    for header in headers.get_list("set-cookie"):
        for name in (CSRF_TOKEN_COOKIE_NAME, OAUTH_SNAPSHOT_COOKIE_NAME):
            match = re.search(rf"{re.escape(name)}=([^;]+)", header, re.IGNORECASE)
            if match:
                cookies[name] = match.group(1)
    return cookies


def _run_oauth_callback(
    client: TestClient,
    *,
    email_verified: bool | None,
    email: str = "user@example.com",
    account_id: str = "account-1",
) -> tuple[object, list[MagicMock]]:
    created: list[MagicMock] = []

    def _factory(**_kwargs: object) -> MagicMock:
        openid_client = _openid_client_with_profile(
            email_verified=email_verified,
            email=email,
            account_id=account_id,
        )
        created.append(openid_client)
        return openid_client

    with patch("miramedia.auth.runtime.OpenID", side_effect=_factory):
        client.put(SETTINGS_PREFIX, json=_oidc_payload())
        authorize = client.get(OIDC_AUTHORIZE_PATH)
        assert authorize.status_code == 200
        oauth_cookies = _oauth_authorize_cookies(authorize)
        state = parse_qs(urlparse(authorize.json()["authorization_url"]).query)[
            "state"
        ][0]
        callback = client.get(
            OIDC_CALLBACK_PATH,
            params={"code": "auth-code", "state": state},
            cookies=oauth_cookies,
            follow_redirects=False,
        )
    return callback, created


def _capture_oauth_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[bool, bool]]:
    from miramedia.auth.users import UserManager

    captured: list[tuple[bool, bool]] = []

    async def _oauth_callback(
        _self: UserManager,
        _provider: str,
        *_args: object,
        **kwargs: object,
    ) -> object:
        captured.append(
            (
                bool(kwargs.get("associate_by_email")),
                bool(kwargs.get("is_verified_by_default")),
            )
        )
        return types.SimpleNamespace(
            id=uuid.uuid4(),
            email="user@example.com",
            is_active=True,
        )

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)
    return captured


def test_oauth_callback_verified_email_enables_association(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    captured = _capture_oauth_callback(monkeypatch)

    with settings_client() as client:
        callback, _created = _run_oauth_callback(client, email_verified=True)

    assert callback.status_code in {200, 204, 302, 307}
    assert captured == [(True, True)]


def test_oauth_callback_unverified_email_disables_association(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_oauth_callback(monkeypatch)

    with settings_client() as client:
        callback, _created = _run_oauth_callback(client, email_verified=False)

    assert callback.status_code in {200, 204, 302, 307}
    assert captured == [(False, False)]


def test_oauth_callback_missing_email_verified_disables_association(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _capture_oauth_callback(monkeypatch)

    with settings_client() as client:
        callback, _created = _run_oauth_callback(client, email_verified=None)

    assert callback.status_code in {200, 204, 302, 307}
    assert captured == [(False, False)]


def test_oauth_callback_unverified_email_collision_returns_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.auth.users import UserManager

    async def _oauth_callback(
        _self: UserManager,
        _provider: str,
        *_args: object,
        **kwargs: object,
    ) -> object:
        if not kwargs.get("associate_by_email"):
            raise UserAlreadyExists()
        return types.SimpleNamespace(
            id=uuid.uuid4(),
            email="user@example.com",
            is_active=True,
        )

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)

    with settings_client() as client:
        callback, _created = _run_oauth_callback(
            client,
            email_verified=False,
            email="existing@example.com",
        )

    assert callback.status_code == 400
    assert callback.json()["detail"] == ErrorCode.OAUTH_USER_ALREADY_EXISTS


def test_oauth_callback_unverified_email_collision_with_superuser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.auth.users import UserManager

    async def _oauth_callback(
        _self: UserManager,
        _provider: str,
        *_args: object,
        **kwargs: object,
    ) -> object:
        if not kwargs.get("associate_by_email"):
            raise UserAlreadyExists()
        return types.SimpleNamespace(
            id=uuid.uuid4(),
            email="admin@example.com",
            is_active=True,
            is_superuser=True,
        )

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)

    with settings_client() as client:
        callback, _created = _run_oauth_callback(
            client,
            email_verified=False,
            email="admin@example.com",
        )

    assert callback.status_code == 400
    assert callback.json()["detail"] == ErrorCode.OAUTH_USER_ALREADY_EXISTS


def test_oauth_callback_already_linked_succeeds_with_unverified_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.auth.users import UserManager

    captured: list[tuple[bool, bool]] = []

    async def _oauth_callback(
        _self: UserManager,
        _provider: str,
        *_args: object,
        **kwargs: object,
    ) -> object:
        captured.append(
            (
                bool(kwargs.get("associate_by_email")),
                bool(kwargs.get("is_verified_by_default")),
            )
        )
        return types.SimpleNamespace(
            id=uuid.uuid4(),
            email="user@example.com",
            is_active=True,
        )

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)

    with settings_client() as client:
        callback, _created = _run_oauth_callback(client, email_verified=False)

    assert callback.status_code in {200, 204, 302, 307}
    assert captured == [(False, False)]
