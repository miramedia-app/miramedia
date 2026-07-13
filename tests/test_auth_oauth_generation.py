"""OAuth authorize/callback generation-scoped behavior tests."""

from __future__ import annotations

import re
import types
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from fastapi_users.router.oauth import CSRF_TOKEN_COOKIE_NAME

from miramedia.auth.runtime import OAUTH_ROUTE_NAME, dynamic_oauth_client
from tests.fakes.repositories import FakeSettingsRepository

SETTINGS_PREFIX = "/api/v1/system/settings"
OIDC_AUTHORIZE_PATH = "/api/v1/auth/oauth/authorize"
OIDC_CALLBACK_PATH = "/api/v1/auth/oauth/callback"


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


@pytest.fixture
def fake_openid(monkeypatch: pytest.MonkeyPatch) -> list[MagicMock]:
    created: list[MagicMock] = []

    def _factory(**kwargs: object) -> MagicMock:
        client = MagicMock()
        client.client_id = kwargs.get("client_id")
        client.client_secret = kwargs.get("client_secret")
        client.name = kwargs.get("name", "Provider")

        async def _authorize_url(
            redirect_url: str, state: str, *_args: object, **_kwargs: object
        ) -> str:
            return (
                f"https://idp.example/authorize?state={state}"
                f"&redirect_uri={redirect_url}"
            )

        client.get_authorization_url = AsyncMock(side_effect=_authorize_url)
        client.get_access_token = AsyncMock(
            return_value={"access_token": "access-token", "token_type": "bearer"}
        )
        client.get_id_email = AsyncMock(return_value=("account-1", "user@example.com"))
        created.append(client)
        return client

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _factory)
    return created


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
                yield client, fake_repo
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)


def _oidc_payload(
    *,
    enabled: bool,
    name: str = "ConfiguredProvider",
    client_id: str = "client-a",
) -> dict[str, Any]:
    return {
        "auth": {
            "openid_connect": {
                "enabled": enabled,
                "name": name,
                "client_id": client_id,
                "client_secret": "secret",
                "configuration_endpoint": "https://idp.example/.well-known/openid-configuration",
            }
        }
    }


def _set_cookie_secure_flag(set_cookie: str) -> bool:
    return any(part.strip().lower() == "secure" for part in set_cookie.split(";"))


def _enable_oidc(client: TestClient, *, name: str = "ConfiguredProvider") -> None:
    response = client.put(
        SETTINGS_PREFIX,
        json=_oidc_payload(enabled=True, name=name),
    )
    assert response.status_code == 200


def test_authorize_csrf_cookie_not_secure_for_http_frontend(
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    with settings_client() as (client, _repo):
        client.put(
            SETTINGS_PREFIX,
            json={
                "misc": {"frontend_url": "http://localhost:8080/"},
                **_oidc_payload(enabled=True),
            },
        )
        response = client.get(OIDC_AUTHORIZE_PATH)
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert CSRF_TOKEN_COOKIE_NAME in set_cookie.lower()
    assert not _set_cookie_secure_flag(set_cookie)


def test_authorize_csrf_cookie_secure_for_https_frontend(
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    with settings_client() as (client, _repo):
        client.put(
            SETTINGS_PREFIX,
            json={
                "misc": {"frontend_url": "https://media.example.com/"},
                **_oidc_payload(enabled=True),
            },
        )
        response = client.get(OIDC_AUTHORIZE_PATH)
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert _set_cookie_secure_flag(set_cookie)


def test_authorize_csrf_cookie_secure_explicit_override(
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    payload = _oidc_payload(enabled=True)
    payload["misc"] = {"frontend_url": "http://localhost:8080/"}
    payload["auth"]["cookie_secure"] = True
    with settings_client() as (client, _repo):
        client.put(SETTINGS_PREFIX, json=payload)
        response = client.get(OIDC_AUTHORIZE_PATH)
    assert response.status_code == 200
    assert _set_cookie_secure_flag(response.headers.get("set-cookie", ""))


def test_oauth_callback_uses_configured_provider_name_not_route_name(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],
) -> None:
    from miramedia.auth.users import UserManager

    captured: list[str] = []
    existing_user = types.SimpleNamespace(
        id=uuid.uuid4(),
        email="user@example.com",
        is_active=True,
    )

    async def _oauth_callback(
        _self: UserManager,
        provider: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        captured.append(provider)
        return existing_user

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)

    with settings_client() as (client, _repo):
        _enable_oidc(client, name="MyIdentityProvider")
        authorize = client.get(OIDC_AUTHORIZE_PATH)
        assert authorize.status_code == 200
        set_cookie = authorize.headers.get("set-cookie", "")
        csrf_match = re.search(
            rf"{re.escape(CSRF_TOKEN_COOKIE_NAME)}=([^;]+)",
            set_cookie,
            re.IGNORECASE,
        )
        assert csrf_match is not None
        csrf_token = csrf_match.group(1)

        auth_url = authorize.json()["authorization_url"]
        state = parse_qs(urlparse(auth_url).query)["state"][0]

        callback = client.get(
            OIDC_CALLBACK_PATH,
            params={"code": "auth-code", "state": state},
            cookies={CSRF_TOKEN_COOKIE_NAME: csrf_token},
            follow_redirects=False,
        )
    assert callback.status_code in {200, 204, 302, 307}
    assert captured == ["MyIdentityProvider"]
    assert dynamic_oauth_client.name == OAUTH_ROUTE_NAME
    assert fake_openid[-1].name == "MyIdentityProvider"


def test_oauth_callback_reuses_existing_account_without_duplicate_provider(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    from miramedia.auth.users import UserManager

    calls: list[tuple[str, str]] = []

    async def _oauth_callback(
        _self: UserManager,
        provider: str,
        _access_token: str,
        account_id: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        calls.append((provider, account_id))
        return types.SimpleNamespace(
            id=uuid.uuid4(),
            email="user@example.com",
            is_active=True,
        )

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)

    with settings_client() as (client, _repo):
        _enable_oidc(client, name="StableProvider")
        for _ in range(2):
            authorize = client.get(OIDC_AUTHORIZE_PATH)
            set_cookie = authorize.headers.get("set-cookie", "")
            csrf_match = re.search(
                rf"{re.escape(CSRF_TOKEN_COOKIE_NAME)}=([^;]+)",
                set_cookie,
                re.IGNORECASE,
            )
            assert csrf_match is not None
            state = parse_qs(urlparse(authorize.json()["authorization_url"]).query)[
                "state"
            ][0]
            callback = client.get(
                OIDC_CALLBACK_PATH,
                params={"code": "auth-code", "state": state},
                cookies={CSRF_TOKEN_COOKIE_NAME: csrf_match.group(1)},
                follow_redirects=False,
            )
            assert callback.status_code in {200, 204, 302, 307}

    assert calls == [
        ("StableProvider", "account-1"),
        ("StableProvider", "account-1"),
    ]
