"""OAuth issuer identity and adversarial reconciliation tests."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miramedia.auth.config import AuthConfig, OpenIdConfig
from miramedia.auth.oauth_identity import (
    OpenIdIssuerResolutionError,
    provider_identity_from_openid_configuration,
    provider_key_from_issuer,
    validate_discovery_issuer,
)
from miramedia.auth.oauth_provider import (
    OAuthProviderConflictError,
    reconcile_legacy_oauth_account,
)
from miramedia.auth.oauth_state import (
    OAuthAuthorizeSnapshot,
    OAuthAuthorizeSnapshotError,
    auth_runtime_generation_from_snapshot,
    decrypt_oauth_authorize_snapshot,
    encrypt_oauth_authorize_snapshot,
)
from miramedia.auth.runtime import (
    AuthRuntimeActivationError,
    build_auth_runtime_generation,
)
from miramedia.config import BasicConfig
from tests.oauth_test_helpers import (
    ENDPOINT_A,
    ISSUER_A,
    ISSUER_B,
    KEY_A,
    KEY_B,
    build_openid_client_mock,
    discovery_configuration_for_endpoint,
)


def test_provider_keys_differ_for_different_issuers() -> None:
    assert KEY_A != KEY_B
    assert provider_key_from_issuer(ISSUER_A) == KEY_A


def test_same_sub_different_issuer_keys_are_distinct_namespaces() -> None:
    assert KEY_A != KEY_B
    assert KEY_A.startswith("oidc:")
    assert KEY_B.startswith("oidc:")


def test_validate_discovery_issuer_rejects_whitespace_padding() -> None:
    with pytest.raises(OpenIdIssuerResolutionError):
        validate_discovery_issuer(" https://issuer.example/ ")


def test_validate_discovery_issuer_preserves_trailing_slash_and_case() -> None:
    issuer = "https://CaseSensitive.example/OIDC/"
    assert validate_discovery_issuer(issuer) == issuer
    assert provider_key_from_issuer(issuer) != provider_key_from_issuer(
        "https://casesensitive.example/OIDC/"
    )


def test_validate_discovery_issuer_rejects_query_and_fragment() -> None:
    with pytest.raises(OpenIdIssuerResolutionError):
        validate_discovery_issuer("https://issuer.example/?x=1")
    with pytest.raises(OpenIdIssuerResolutionError):
        validate_discovery_issuer("https://issuer.example/#frag")


def test_validate_discovery_issuer_allows_loopback_http() -> None:
    issuer = "http://127.0.0.1:8080/"
    assert validate_discovery_issuer(issuer) == issuer


def test_provider_identity_from_openid_configuration() -> None:
    issuer, key = provider_identity_from_openid_configuration(
        discovery_configuration_for_endpoint(ENDPOINT_A, issuer=ISSUER_A)
    )
    assert issuer == ISSUER_A
    assert key == KEY_A


def test_missing_issuer_rejects_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    def _factory(**_kwargs: object) -> MagicMock:
        return build_openid_client_mock(endpoint=ENDPOINT_A, issuer="")

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _factory)
    auth = AuthConfig(
        openid_connect=OpenIdConfig(
            enabled=True,
            name="Provider",
            client_id="client",
            client_secret="secret",
            configuration_endpoint=ENDPOINT_A,
        )
    )

    async def _run() -> None:
        with pytest.raises(AuthRuntimeActivationError):
            await build_auth_runtime_generation(auth, BasicConfig())

    asyncio.run(_run())


def test_build_generation_uses_single_discovery_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _factory(**kwargs: object) -> MagicMock:
        endpoint = str(kwargs["openid_configuration_endpoint"])
        calls.append(endpoint)
        return build_openid_client_mock(endpoint=endpoint)

    monkeypatch.setattr("miramedia.auth.runtime.OpenID", _factory)
    auth = AuthConfig(
        openid_connect=OpenIdConfig(
            enabled=True,
            name="Provider",
            client_id="client",
            client_secret="secret",
            configuration_endpoint=ENDPOINT_A,
        )
    )

    async def _run() -> None:
        generation = await build_auth_runtime_generation(auth, BasicConfig())
        assert generation.openid_issuer == ISSUER_A
        assert generation.account_provider_name == KEY_A

    asyncio.run(_run())
    assert calls == [ENDPOINT_A]


def test_snapshot_tampered_provider_key_rejects() -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id="client-a",
        client_secret="secret-a",
        configuration_endpoint=ENDPOINT_A,
        provider_name="Display",
        openid_issuer=ISSUER_A,
        account_provider_name=KEY_B,
        frontend_url="http://localhost/",
        cookie_secure=False,
        session_lifetime=3600,
    )
    secret = "a" * 64
    token = encrypt_oauth_authorize_snapshot(snapshot, secret)
    with pytest.raises(OAuthAuthorizeSnapshotError):
        decrypt_oauth_authorize_snapshot(token, secret)


def test_callback_snapshot_rejects_flipped_issuer() -> None:
    snapshot = OAuthAuthorizeSnapshot(
        client_id="client-a",
        client_secret="secret-a",
        configuration_endpoint=ENDPOINT_A,
        provider_name="Display",
        openid_issuer=ISSUER_A,
        account_provider_name=KEY_A,
        frontend_url="http://localhost/",
        cookie_secure=False,
        session_lifetime=3600,
    )

    def _factory(**_kwargs: object) -> MagicMock:
        return build_openid_client_mock(endpoint=ENDPOINT_A, issuer=ISSUER_B)

    with patch("miramedia.auth.runtime.OpenID", side_effect=_factory):

        async def _run() -> None:
            with pytest.raises(OAuthAuthorizeSnapshotError):
                await auth_runtime_generation_from_snapshot(snapshot)

        asyncio.run(_run())


def test_display_rename_preserves_issuer_derived_key() -> None:
    assert provider_key_from_issuer(ISSUER_A) == provider_key_from_issuer(ISSUER_A)


def test_reconcile_leaves_unprovable_legacy_when_display_renamed() -> None:
    user_id = uuid.uuid4()
    canonical = SimpleNamespace(
        id=uuid.uuid4(),
        account_id="sub-x",
        oauth_name=KEY_A,
        access_token="token",
        account_email="user@example.com",
        expires_at=None,
        refresh_token=None,
    )
    legacy = SimpleNamespace(
        id=uuid.uuid4(),
        account_id="sub-x",
        oauth_name="OldDisplay",
        access_token="token",
        account_email="user@example.com",
        expires_at=None,
        refresh_token=None,
    )
    user = SimpleNamespace(id=user_id, oauth_accounts=[canonical, legacy])
    user_db = AsyncMock()
    user_db.get_by_oauth_account = AsyncMock(return_value=user)
    user_db.session = AsyncMock()
    user_db.session.delete = AsyncMock()
    user_db.session.commit = AsyncMock()

    async def _run() -> None:
        await reconcile_legacy_oauth_account(
            user_db,
            account_id="sub-x",
            display_name="NewDisplay",
            provider_key=KEY_A,
        )

    asyncio.run(_run())
    user_db.session.delete.assert_not_awaited()


def test_reconcile_fails_closed_on_cross_owner_conflict() -> None:
    canonical_user = SimpleNamespace(id=uuid.uuid4(), oauth_accounts=[])
    legacy_user = SimpleNamespace(id=uuid.uuid4(), oauth_accounts=[])

    async def _lookup(oauth: str, _account_id: str) -> object:
        if oauth == KEY_A:
            return canonical_user
        return legacy_user

    user_db = AsyncMock()
    user_db.get_by_oauth_account = AsyncMock(side_effect=_lookup)

    async def _run() -> None:
        with pytest.raises(OAuthProviderConflictError):
            await reconcile_legacy_oauth_account(
                user_db,
                account_id="sub-x",
                display_name="LegacyName",
                provider_key=KEY_A,
            )

    asyncio.run(_run())
