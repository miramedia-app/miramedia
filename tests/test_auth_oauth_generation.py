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

from miramedia.auth.oauth_state import OAUTH_SNAPSHOT_COOKIE_NAME
from miramedia.auth.runtime import (
    OAUTH_ROUTE_NAME,
    auth_runtime_store,
    current_oauth_runtime_generation,
    dynamic_oauth_client,
)
from tests.fakes.repositories import FakeSettingsRepository
from tests.oauth_test_helpers import (
    ENDPOINT_A,
    ENDPOINT_B,
    ENDPOINT_DEFAULT,
    ISSUER_A,
    ISSUER_B,
    KEY_A,
    KEY_B,
    KEY_DEFAULT,
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
    configuration_endpoint: str = "https://idp.example/.well-known/openid-configuration",
) -> dict[str, Any]:
    return {
        "auth": {
            "openid_connect": {
                "enabled": enabled,
                "name": name,
                "client_id": client_id,
                "client_secret": "secret",
                "configuration_endpoint": configuration_endpoint,
            }
        }
    }


def _set_cookie_secure_flag(set_cookie: str) -> bool:
    return any(part.strip().lower() == "secure" for part in set_cookie.split(";"))


def _oauth_authorize_cookies(authorize_response: object) -> dict[str, str]:
    headers = getattr(authorize_response, "headers", {})
    cookies: dict[str, str] = {}
    for header in headers.get_list("set-cookie"):
        for name in (CSRF_TOKEN_COOKIE_NAME, OAUTH_SNAPSHOT_COOKIE_NAME):
            match = re.search(rf"{re.escape(name)}=([^;]+)", header, re.IGNORECASE)
            if match:
                cookies[name] = match.group(1)
    return cookies


def _snapshot_cookie_cleared(callback_response: object) -> bool:
    headers = getattr(callback_response, "headers", {})
    for header in headers.get_list("set-cookie"):
        if OAUTH_SNAPSHOT_COOKIE_NAME.lower() not in header.lower():
            continue
        lowered = header.lower()
        if "max-age=0" in lowered:
            return True
        value_match = re.search(
            rf"{re.escape(OAUTH_SNAPSHOT_COOKIE_NAME)}=([^;]*)",
            header,
            re.IGNORECASE,
        )
        if value_match is not None and value_match.group(1) == "":
            return True
    return False


def _jwt_lifetime_from_callback_response(response: object) -> int:
    import jwt

    from miramedia.auth.users import SECRET, openid_cookie_transport

    headers = getattr(response, "headers", {})
    set_cookie = headers.get("set-cookie", "")
    cookie_name = openid_cookie_transport.cookie_name
    match = re.search(
        rf"{re.escape(cookie_name)}=([^;]+)",
        set_cookie,
        re.IGNORECASE,
    )
    assert match is not None, set_cookie
    payload = jwt.decode(
        match.group(1),
        SECRET,
        algorithms=["HS256"],
        options={"verify_aud": False},
    )
    import time

    return int(payload["exp"]) - int(time.time())


def _enable_oidc(
    client: TestClient,
    *,
    name: str = "ConfiguredProvider",
    configuration_endpoint: str = ENDPOINT_DEFAULT,
) -> None:
    response = client.put(
        SETTINGS_PREFIX,
        json=_oidc_payload(
            enabled=True,
            name=name,
            configuration_endpoint=configuration_endpoint,
        ),
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
    assert (
        OAUTH_SNAPSHOT_COOKIE_NAME
        in " ".join(response.headers.get_list("set-cookie")).lower()
    )
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


def test_oauth_callback_uses_stable_provider_key_not_display_name(
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
        oauth_cookies = _oauth_authorize_cookies(authorize)

        auth_url = authorize.json()["authorization_url"]
        state = parse_qs(urlparse(auth_url).query)["state"][0]

        callback = client.get(
            OIDC_CALLBACK_PATH,
            params={"code": "auth-code", "state": state},
            cookies=oauth_cookies,
            follow_redirects=False,
        )
    assert callback.status_code in {200, 204, 302, 307}
    assert captured == [KEY_DEFAULT]
    assert dynamic_oauth_client.name == OAUTH_ROUTE_NAME
    assert fake_openid[-1].name == "MyIdentityProvider"


def test_oauth_provider_key_stable_across_display_name_rename(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],
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
        _enable_oidc(client, name="DisplayNameA")
        authorize_a = client.get(OIDC_AUTHORIZE_PATH)
        state_a = parse_qs(urlparse(authorize_a.json()["authorization_url"]).query)[
            "state"
        ][0]
        cookies_a = _oauth_authorize_cookies(authorize_a)
        client.get(
            OIDC_CALLBACK_PATH,
            params={"code": "auth-code", "state": state_a},
            cookies=cookies_a,
            follow_redirects=False,
        )

        client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(enabled=True, name="DisplayNameB"),
        )
        authorize_b = client.get(OIDC_AUTHORIZE_PATH)
        state_b = parse_qs(urlparse(authorize_b.json()["authorization_url"]).query)[
            "state"
        ][0]
        cookies_b = _oauth_authorize_cookies(authorize_b)
        client.get(
            OIDC_CALLBACK_PATH,
            params={"code": "auth-code", "state": state_b},
            cookies=cookies_b,
            follow_redirects=False,
        )

    assert calls == [
        (KEY_DEFAULT, "account-1"),
        (KEY_DEFAULT, "account-1"),
    ]
    assert fake_openid[-1].name == "DisplayNameB"


def test_oauth_callback_keeps_request_generation_after_concurrent_swap(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    import threading

    from miramedia.auth.users import (
        GenerationScopedRedirectingCookieTransport,
        UserManager,
    )

    entered = threading.Event()
    release = threading.Event()
    captured: list[tuple[str, int, bool]] = []

    original_login_response = (
        GenerationScopedRedirectingCookieTransport.get_login_response
    )

    async def _slow_login_response(
        self: GenerationScopedRedirectingCookieTransport, token: str
    ) -> object:
        generation = current_oauth_runtime_generation()
        captured.append(
            (
                generation.frontend_url,
                generation.session_lifetime,
                generation.cookie_secure,
            )
        )
        entered.set()
        assert release.wait(timeout=2)
        return await original_login_response(self, token)

    async def _oauth_callback(
        _self: UserManager,
        _provider: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        return types.SimpleNamespace(
            id=uuid.uuid4(),
            email="user@example.com",
            is_active=True,
        )

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)
    monkeypatch.setattr(
        GenerationScopedRedirectingCookieTransport,
        "get_login_response",
        _slow_login_response,
    )

    with settings_client() as (client, _repo):
        client.put(
            SETTINGS_PREFIX,
            json={
                "misc": {"frontend_url": "http://gen-a.example.com/"},
                "auth": {
                    "session_lifetime": 3600,
                    **_oidc_payload(enabled=True, name="GenA")["auth"],
                },
            },
        )
        authorize = client.get(OIDC_AUTHORIZE_PATH)
        oauth_cookies = _oauth_authorize_cookies(authorize)
        state = parse_qs(urlparse(authorize.json()["authorization_url"]).query)[
            "state"
        ][0]

        callback_error: list[BaseException] = []

        def _call_callback() -> None:
            try:
                client.get(
                    OIDC_CALLBACK_PATH,
                    params={"code": "auth-code", "state": state},
                    cookies=oauth_cookies,
                    follow_redirects=False,
                )
            except BaseException as exc:
                callback_error.append(exc)

        thread = threading.Thread(target=_call_callback)
        thread.start()
        assert entered.wait(timeout=2)

        swap = client.put(
            SETTINGS_PREFIX,
            json={
                "misc": {"frontend_url": "https://gen-b.example.com/"},
                "auth": {"session_lifetime": 7200},
            },
        )
        assert swap.status_code == 200
        release.set()
        thread.join(timeout=3)
        assert not callback_error

    assert captured == [("http://gen-a.example.com/", 3600, False)]
    assert auth_runtime_store.get_active().frontend_url == "https://gen-b.example.com/"
    assert auth_runtime_store.get_active().session_lifetime == 7200


def test_oauth_callback_uses_authorize_generation_after_settings_swap_between_requests(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],
) -> None:
    from miramedia.auth.users import UserManager

    token_clients: list[str] = []

    def _tracking_factory(**kwargs: object) -> MagicMock:
        endpoint = str(kwargs.get("openid_configuration_endpoint", ENDPOINT_DEFAULT))
        client = build_openid_client_mock(
            endpoint=endpoint,
            name=str(kwargs.get("name", "Provider")),
            client_id=str(kwargs.get("client_id", "client-a")),
            client_secret=str(kwargs.get("client_secret", "secret")),
        )

        async def _exchange(
            _code: str,
            _redirect_uri: str,
            _code_verifier: str | None = None,
        ) -> dict[str, str]:
            token_clients.append(str(client.client_id))
            return {"access_token": "access-token", "token_type": "bearer"}

        client.get_access_token = AsyncMock(side_effect=_exchange)
        fake_openid.append(client)
        return client

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _tracking_factory)

    captured_login: list[tuple[str, int, bool]] = []

    async def _oauth_callback(
        _self: UserManager,
        _provider: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        generation = current_oauth_runtime_generation()
        captured_login.append(
            (
                generation.frontend_url,
                generation.session_lifetime,
                generation.cookie_secure,
            )
        )
        return types.SimpleNamespace(
            id=uuid.uuid4(),
            email="user@example.com",
            is_active=True,
        )

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)

    with settings_client() as (client, _repo):
        client.put(
            SETTINGS_PREFIX,
            json={
                "misc": {"frontend_url": "http://gen-a.example.com/"},
                "auth": {
                    "session_lifetime": 3600,
                    **_oidc_payload(enabled=True, name="GenA", client_id="client-a")[
                        "auth"
                    ],
                },
            },
        )
        authorize = client.get(OIDC_AUTHORIZE_PATH)
        oauth_cookies = _oauth_authorize_cookies(authorize)
        state = parse_qs(urlparse(authorize.json()["authorization_url"]).query)[
            "state"
        ][0]

        swap = client.put(
            SETTINGS_PREFIX,
            json={
                "misc": {"frontend_url": "https://gen-b.example.com/"},
                "auth": {
                    "session_lifetime": 7200,
                    **_oidc_payload(enabled=True, name="GenB", client_id="client-b")[
                        "auth"
                    ],
                },
            },
        )
        assert swap.status_code == 200

        callback = client.get(
            OIDC_CALLBACK_PATH,
            params={"code": "auth-code", "state": state},
            cookies=oauth_cookies,
            follow_redirects=False,
        )
        assert callback.status_code in {200, 204, 302, 307}

    assert token_clients == ["client-a"]
    assert captured_login == [("http://gen-a.example.com/", 3600, False)]
    assert auth_runtime_store.get_active().frontend_url == "https://gen-b.example.com/"
    assert auth_runtime_store.get_active().session_lifetime == 7200

    jwt_lifetime = _jwt_lifetime_from_callback_response(callback)
    assert 3590 <= jwt_lifetime <= 3610


def test_oauth_callback_uses_authorize_issuer_after_runtime_switches_to_issuer_b(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    from miramedia.auth.users import UserManager

    captured: list[str] = []

    async def _oauth_callback(
        _self: UserManager,
        provider: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        captured.append(provider)
        return types.SimpleNamespace(
            id=uuid.uuid4(),
            email="user@example.com",
            is_active=True,
        )

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)

    with settings_client() as (client, _repo):
        client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(
                enabled=True,
                name="IssuerA",
                configuration_endpoint=ENDPOINT_A,
            ),
        )
        authorize_a = client.get(OIDC_AUTHORIZE_PATH)
        assert authorize_a.status_code == 200
        oauth_cookies = _oauth_authorize_cookies(authorize_a)
        state_a = parse_qs(urlparse(authorize_a.json()["authorization_url"]).query)[
            "state"
        ][0]

        swap = client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(
                enabled=True,
                name="IssuerB",
                configuration_endpoint=ENDPOINT_B,
            ),
        )
        assert swap.status_code == 200
        assert auth_runtime_store.get_active().account_provider_name == KEY_B
        assert auth_runtime_store.get_active().openid_issuer == ISSUER_B

        callback = client.get(
            OIDC_CALLBACK_PATH,
            params={"code": "auth-code", "state": state_a},
            cookies=oauth_cookies,
            follow_redirects=False,
        )
        assert callback.status_code in {200, 204, 302, 307}

    assert captured == [KEY_A]
    assert KEY_A != KEY_B


def test_invalid_issuer_rejects_activation_without_mutating_prior_runtime(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    def _factory(**kwargs: object) -> MagicMock:
        endpoint = str(kwargs.get("openid_configuration_endpoint", ENDPOINT_DEFAULT))
        if "broken.example" in endpoint:
            return build_openid_client_mock(endpoint=endpoint, issuer="")
        return build_openid_client_mock(
            endpoint=endpoint,
            name=str(kwargs.get("name", "Provider")),
            client_id=str(kwargs.get("client_id", "client-a")),
            client_secret=str(kwargs.get("client_secret", "secret")),
        )

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _factory)

    with settings_client() as (client, _repo):
        good = client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(enabled=True, name="Good"),
        )
        assert good.status_code == 200
        prior_key = auth_runtime_store.get_active().account_provider_name
        prior_issuer = auth_runtime_store.get_active().openid_issuer

        bad = client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(
                enabled=True,
                name="Bad",
                configuration_endpoint="https://broken.example/.well-known/openid-configuration",
            ),
        )
        assert bad.status_code == 400
        assert auth_runtime_store.get_active().account_provider_name == prior_key
        assert auth_runtime_store.get_active().openid_issuer == prior_issuer


def test_callback_rejects_issuer_flip_on_same_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],
) -> None:
    call_count = 0

    def _factory(**kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        endpoint = str(kwargs.get("openid_configuration_endpoint", ENDPOINT_DEFAULT))
        issuer = ISSUER_A if call_count == 1 else ISSUER_B
        client = build_openid_client_mock(endpoint=endpoint, issuer=issuer)
        fake_openid.append(client)
        return client

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _factory)

    with settings_client() as (client, _repo):
        _enable_oidc(client, name="IssuerA", configuration_endpoint=ENDPOINT_A)
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

    assert callback.status_code == 400


def test_callback_rejects_missing_snapshot_cookie(
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    with settings_client() as (client, _repo):
        _enable_oidc(client)
        authorize = client.get(OIDC_AUTHORIZE_PATH)
        assert authorize.status_code == 200
        oauth_cookies = _oauth_authorize_cookies(authorize)
        state = parse_qs(urlparse(authorize.json()["authorization_url"]).query)[
            "state"
        ][0]
        client.cookies.pop(OAUTH_SNAPSHOT_COOKIE_NAME, None)

        callback = client.get(
            OIDC_CALLBACK_PATH,
            params={"code": "auth-code", "state": state},
            cookies={CSRF_TOKEN_COOKIE_NAME: oauth_cookies[CSRF_TOKEN_COOKIE_NAME]},
            follow_redirects=False,
        )

    assert callback.status_code == 400


def test_callback_clears_snapshot_cookie(
    monkeypatch: pytest.MonkeyPatch,
    fake_openid: list[MagicMock],  # noqa: ARG001
) -> None:
    from miramedia.auth.users import UserManager

    async def _oauth_callback(
        _self: UserManager,
        _provider: str,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        return types.SimpleNamespace(
            id=uuid.uuid4(),
            email="user@example.com",
            is_active=True,
        )

    monkeypatch.setattr(UserManager, "oauth_callback", _oauth_callback)

    with settings_client() as (client, _repo):
        _enable_oidc(client)
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

    assert callback.status_code in {200, 204, 302, 307}
    assert _snapshot_cookie_cleared(callback)
