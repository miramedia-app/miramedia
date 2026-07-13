"""Auth settings hot-reload and OIDC runtime lifecycle regression tests."""

from __future__ import annotations

import asyncio
import copy
import types
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from miramedia.auth.runtime import (
    auth_runtime_store,
    build_auth_runtime_generation,
    commit_auth_runtime_generation,
    current_oauth_runtime_generation,
    dynamic_oauth_client,
    oauth_runtime_request_scope,
    reset_auth_runtime_for_tests,
)
from miramedia.config import MiraMediaConfig
from tests.fakes.repositories import FakeSettingsRepository

SETTINGS_PREFIX = "/api/v1/system/settings"
OIDC_AUTHORIZE_PATH = "/api/v1/auth/oauth/authorize"
METADATA_PATH = "/api/v1/auth/metadata"

pytestmark = pytest.mark.usefixtures("fake_openid")


@pytest.fixture(autouse=True)
def _reset_auth_state() -> Generator[None]:
    from miramedia.settings.service import apply_live_config_from_overrides

    reset_auth_runtime_for_tests()
    apply_live_config_from_overrides({})
    yield
    reset_auth_runtime_for_tests()
    apply_live_config_from_overrides({})


@pytest.fixture
def fake_openid(monkeypatch: pytest.MonkeyPatch) -> list[MagicMock]:
    created: list[MagicMock] = []

    def _factory(**kwargs: object) -> MagicMock:
        client = MagicMock()
        client.client_id = kwargs.get("client_id")
        client.client_secret = kwargs.get("client_secret")
        client.name = kwargs.get("name", "Provider")
        client.get_authorization_url = AsyncMock(
            return_value="https://idp.example/authorize"
        )
        client.get_access_token = AsyncMock(return_value={"access_token": "at"})
        client.get_id_email = AsyncMock(return_value=("sub-1", "user@example.com"))
        created.append(client)
        return client

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _factory)
    return created


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
            yield client, fake_repo
    finally:
        app.dependency_overrides.clear()


def _oidc_payload(
    *,
    enabled: bool,
    name: str = "TestProvider",
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


def _make_user_manager():
    from miramedia.auth.users import UserManager

    return UserManager.__new__(UserManager)


def _make_stub_user():
    return types.SimpleNamespace(id=uuid.uuid4(), email="stub@example.com")


def test_forgot_password_reads_live_email_password_resets_after_put(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.auth.runtime import get_live_auth_config

    email_sent = False

    def _send_email(**_kwargs: object) -> None:
        nonlocal email_sent
        email_sent = True

    monkeypatch.setattr("miramedia.notifications.utils.send_email", _send_email)

    with settings_client() as (client, _repo):
        assert (
            client.put(
                SETTINGS_PREFIX,
                json={"auth": {"email_password_resets": True}},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"{SETTINGS_PREFIX}/override/clear",
                json={"path": ["auth", "email_password_resets"]},
            ).status_code
            == 200
        )

    assert get_live_auth_config().email_password_resets is False

    manager = _make_user_manager()
    user = _make_stub_user()
    asyncio.run(manager.on_after_forgot_password(user, "reset-token-after-put"))

    assert email_sent is False


def test_metadata_and_oauth_disabled_to_enabled_via_put(
    fake_openid: list[MagicMock],
) -> None:
    with settings_client() as (client, _repo):
        disabled = client.get(METADATA_PATH)
        assert disabled.status_code == 200
        assert disabled.json()["oauth_providers"] == []

        blocked = client.get(OIDC_AUTHORIZE_PATH, follow_redirects=False)
        assert blocked.status_code == 503

        enabled = client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(enabled=True, name="ProviderOne"),
        )
        assert enabled.status_code == 200

        metadata = client.get(METADATA_PATH)
        assert metadata.json()["oauth_providers"] == ["ProviderOne"]

        authorize = client.get(OIDC_AUTHORIZE_PATH, follow_redirects=False)
        assert authorize.status_code == 200
        assert authorize.json()["authorization_url"] == "https://idp.example/authorize"
        assert fake_openid[-1].client_id == "client-a"


def test_metadata_and_oauth_enabled_to_disabled_via_reset() -> None:
    with settings_client() as (client, _repo):
        client.put(
            SETTINGS_PREFIX, json=_oidc_payload(enabled=True, name="LiveProvider")
        )
        assert client.get(METADATA_PATH).json()["oauth_providers"] == ["LiveProvider"]

        reset = client.delete(SETTINGS_PREFIX)
        assert reset.status_code == 204

        metadata = client.get(METADATA_PATH)
        assert metadata.json()["oauth_providers"] == []

        blocked = client.get(OIDC_AUTHORIZE_PATH, follow_redirects=False)
        assert blocked.status_code == 503


def test_provider_change_via_import(fake_openid: list[MagicMock]) -> None:
    with settings_client() as (client, _repo):
        client.put(SETTINGS_PREFIX, json=_oidc_payload(enabled=True, name="First"))
        import_response = client.post(
            f"{SETTINGS_PREFIX}/import",
            json={
                "mode": "merge",
                "overrides": _oidc_payload(
                    enabled=True, name="ImportedProvider", client_id="client-b"
                ),
            },
        )
        assert import_response.status_code == 200
        assert client.get(METADATA_PATH).json()["oauth_providers"] == [
            "ImportedProvider"
        ]
        assert fake_openid[-1].client_id == "client-b"


def test_oidc_refresh_failure_does_not_persist_or_activate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(**_kwargs: object) -> MagicMock:
        msg = "discovery failed"
        raise RuntimeError(msg)

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _boom)

    repo = FakeSettingsRepository()
    before = auth_runtime_store.get_active().generation_id

    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(enabled=True, name="BrokenProvider"),
        )
        assert response.status_code == 400
        assert (
            "Failed to configure OpenID Connect provider" in response.json()["detail"]
        )
        assert fake_repo.save_calls == []
        assert client.get(METADATA_PATH).json()["oauth_providers"] == []

    assert auth_runtime_store.get_active().generation_id == before


def test_startup_db_override_refresh(fake_openid: list[MagicMock]) -> None:
    from miramedia.auth.runtime import initialize_auth_runtime
    from miramedia.settings.service import apply_overrides_to_config

    overrides = _oidc_payload(enabled=True, name="StartupProvider")
    apply_overrides_to_config(MiraMediaConfig(), overrides)
    generation = asyncio.run(initialize_auth_runtime())

    assert generation.oidc_enabled is True
    assert generation.provider_name == "StartupProvider"
    assert fake_openid[-1].client_id == "client-a"


def test_token_secret_remains_restart_only_after_runtime_refresh() -> None:
    from miramedia.auth.users import SECRET, UserManager

    original_secret = SECRET
    with settings_client() as (client, _repo):
        response = client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(enabled=True, name="SecretStable"),
        )
        assert response.status_code == 200

    assert SECRET == original_secret
    assert UserManager.reset_password_token_secret == original_secret
    assert UserManager.verification_token_secret == original_secret


def test_request_snapshot_is_immutable_across_runtime_swap() -> None:
    from miramedia.auth.config import AuthConfig, OpenIdConfig

    auth_a = AuthConfig(
        openid_connect=OpenIdConfig(
            enabled=True,
            name="GenA",
            client_id="a",
            client_secret="secret",
            configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        )
    )
    auth_b = AuthConfig(
        openid_connect=OpenIdConfig(
            enabled=True,
            name="GenB",
            client_id="b",
            client_secret="secret",
            configuration_endpoint="https://idp.example/.well-known/openid-configuration",
        )
    )

    async def _run() -> None:
        gen_a = await build_auth_runtime_generation(auth_a)
        gen_b = await build_auth_runtime_generation(auth_b)
        commit_auth_runtime_generation(gen_a)

        async with oauth_runtime_request_scope():
            bound = current_oauth_runtime_generation()
            assert bound.provider_name == "GenA"
            commit_auth_runtime_generation(gen_b)
            assert current_oauth_runtime_generation().provider_name == "GenA"
            client = dynamic_oauth_client._require_client()
            assert client.client_id == "a"

        assert auth_runtime_store.get_active().provider_name == "GenB"

    asyncio.run(_run())


def test_cookie_secure_refreshes_from_misc_frontend_url() -> None:
    from miramedia.auth.users import openid_cookie_transport

    with settings_client() as (client, _repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"frontend_url": "https://app.example.com/"}},
        )
        assert response.status_code == 200
        assert openid_cookie_transport.cookie_secure is True

        insecure = client.put(
            SETTINGS_PREFIX,
            json={"misc": {"frontend_url": "http://app.example.com/"}},
        )
        assert insecure.status_code == 200
        assert openid_cookie_transport.cookie_secure is False


def test_override_clear_updates_runtime_metadata() -> None:
    with settings_client() as (client, _repo):
        client.put(SETTINGS_PREFIX, json=_oidc_payload(enabled=True, name="ClearMe"))
        assert client.get(METADATA_PATH).json()["oauth_providers"] == ["ClearMe"]

        cleared = client.post(
            f"{SETTINGS_PREFIX}/override/clear",
            json={"path": ["auth", "openid_connect", "enabled"]},
        )
        assert cleared.status_code == 200
        assert client.get(METADATA_PATH).json()["oauth_providers"] == []
        assert (
            client.get(OIDC_AUTHORIZE_PATH, follow_redirects=False).status_code == 503
        )


def _capture_runtime_state() -> dict[str, Any]:
    from miramedia.auth.users import openid_cookie_transport

    return {
        "generation_id": auth_runtime_store.get_active().generation_id,
        "metadata": auth_runtime_store.get_active().provider_name,
        "cookie_secure": openid_cookie_transport.cookie_secure,
    }


def test_clear_staging_failure_leaves_state_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.settings.service import compute_clear_override_path

    def _openid_reject_empty_client_id(**kwargs: object) -> MagicMock:
        if not kwargs.get("client_id"):
            msg = "OIDC client_id required when enabled"
            raise RuntimeError(msg)
        client = MagicMock()
        client.client_id = kwargs.get("client_id")
        client.get_authorization_url = AsyncMock(
            return_value="https://idp.example/authorize"
        )
        return client

    monkeypatch.setattr(
        "miramedia.auth.runtime.OpenID",
        _openid_reject_empty_client_id,
    )

    repo = FakeSettingsRepository()
    with settings_client(repo=repo) as (client, fake_repo):
        assert (
            client.put(
                SETTINGS_PREFIX,
                json=_oidc_payload(enabled=True, name="BeforeClear"),
            ).status_code
            == 200
        )
        before_overrides = copy.deepcopy(fake_repo.overrides)
        before_runtime = _capture_runtime_state()
        save_calls_before = len(fake_repo.save_calls)

        response = client.post(
            f"{SETTINGS_PREFIX}/override/clear",
            json={"path": ["auth", "openid_connect", "client_id"]},
        )
        assert response.status_code == 400
        assert (
            "Failed to configure OpenID Connect provider" in response.json()["detail"]
        )
        assert fake_repo.overrides == before_overrides
        assert len(fake_repo.save_calls) == save_calls_before
        assert _capture_runtime_state() == before_runtime
        assert client.get(METADATA_PATH).json()["oauth_providers"] == ["BeforeClear"]

    prospective = compute_clear_override_path(
        before_overrides, ["auth", "openid_connect", "client_id"]
    )
    assert "client_id" not in prospective.get("auth", {}).get("openid_connect", {})


def test_put_persist_failure_rolls_back_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = FakeSettingsRepository()

    async def _boom_save(_overrides: dict) -> dict:
        msg = "db write failed"
        raise RuntimeError(msg)

    with settings_client(repo=repo) as (client, fake_repo):
        monkeypatch.setattr(fake_repo, "save_overrides", _boom_save)
        before_runtime = _capture_runtime_state()
        response = client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(enabled=True, name="ShouldNotStick"),
        )
        assert response.status_code == 500
        assert fake_repo.overrides == {}
        assert _capture_runtime_state() == before_runtime
        assert client.get(METADATA_PATH).json()["oauth_providers"] == []


def test_reset_apply_failure_rolls_back_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.settings import service as settings_service

    repo = FakeSettingsRepository()
    original_apply = settings_service.apply_live_config_from_overrides
    calls = 0

    def _flaky_apply(overrides: dict) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "apply failed"
            raise RuntimeError(msg)
        original_apply(overrides)

    with settings_client(repo=repo) as (client, fake_repo):
        assert (
            client.put(
                SETTINGS_PREFIX,
                json=_oidc_payload(enabled=True, name="ResetGuard"),
            ).status_code
            == 200
        )
        before_overrides = copy.deepcopy(fake_repo.overrides)
        before_runtime = _capture_runtime_state()

        monkeypatch.setattr(
            "miramedia.settings.mutation.apply_live_config_from_overrides",
            _flaky_apply,
        )
        response = client.delete(SETTINGS_PREFIX)
        assert response.status_code == 500
        assert fake_repo.overrides == before_overrides
        assert _capture_runtime_state() == before_runtime
        assert client.get(METADATA_PATH).json()["oauth_providers"] == ["ResetGuard"]


def test_import_runtime_activation_failure_rolls_back_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.auth.runtime import commit_auth_runtime_generation as original_commit

    repo = FakeSettingsRepository(overrides={"misc": {"development": True}})
    calls = 0

    def _flaky_commit(prospective: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            msg = "swap failed"
            raise RuntimeError(msg)
        return original_commit(prospective)

    with settings_client(repo=repo) as (client, fake_repo):
        before_overrides = copy.deepcopy(fake_repo.overrides)
        before_runtime = _capture_runtime_state()

        monkeypatch.setattr(
            "miramedia.settings.mutation.commit_auth_runtime_generation",
            _flaky_commit,
        )
        response = client.post(
            f"{SETTINGS_PREFIX}/import",
            json={
                "mode": "merge",
                "overrides": _oidc_payload(enabled=True, name="ImportFail"),
            },
        )
        assert response.status_code == 500
        assert fake_repo.overrides == before_overrides
        assert _capture_runtime_state() == before_runtime


def test_authorize_uses_request_generation_under_concurrent_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    created: list[MagicMock] = []
    entered = threading.Event()
    release = threading.Event()

    def _blocking_factory(**kwargs: object) -> MagicMock:
        client = MagicMock()
        client.client_id = kwargs.get("client_id")
        client.name = kwargs.get("name", "Provider")

        async def _slow_authorize(*_args: object, **_kwargs: object) -> str:
            entered.set()
            assert release.wait(timeout=2)
            return f"https://idp.example/{client.client_id}"

        client.get_authorization_url = _slow_authorize
        created.append(client)
        return client

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _blocking_factory)

    with settings_client() as (client, _repo):
        assert (
            client.put(
                SETTINGS_PREFIX,
                json=_oidc_payload(enabled=True, name="GenA", client_id="client-a"),
            ).status_code
            == 200
        )

        authorize_response: dict[str, Any] = {}
        error: list[BaseException] = []

        def _call_authorize() -> None:
            try:
                authorize_response["response"] = client.get(
                    OIDC_AUTHORIZE_PATH,
                    follow_redirects=False,
                )
            except BaseException as exc:
                error.append(exc)

        thread = threading.Thread(target=_call_authorize)
        thread.start()
        assert entered.wait(timeout=2)

        swap = client.put(
            SETTINGS_PREFIX,
            json=_oidc_payload(enabled=True, name="GenB", client_id="client-b"),
        )
        assert swap.status_code == 200
        release.set()
        thread.join(timeout=3)
        assert not error

        response = authorize_response["response"]
        assert response.status_code == 200
        assert response.json()["authorization_url"] == "https://idp.example/client-a"
        assert created[0].client_id == "client-a"
        assert client.get(METADATA_PATH).json()["oauth_providers"] == ["GenB"]
