"""OAuth issuer identity and adversarial reconciliation tests."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi_users import exceptions as user_exceptions

from miramedia.auth.oauth_identity import (
    OpenIdIssuerResolutionError,
    provider_key_from_issuer,
    resolve_openid_provider_key,
)
from miramedia.auth.oauth_provider import (
    OAuthProviderConflictError,
    reconcile_legacy_oauth_account,
)
from miramedia.auth.runtime import (
    AuthRuntimeActivationError,
    build_auth_runtime_generation,
)
from miramedia.config import BasicConfig
from tests.oauth_test_helpers import (
    ENDPOINT_A,
    ISSUER_A,
    KEY_A,
    KEY_B,
)


def test_provider_keys_differ_for_different_issuers() -> None:
    assert KEY_A != KEY_B
    assert provider_key_from_issuer(ISSUER_A) == KEY_A


def test_same_sub_different_issuer_keys_are_distinct_namespaces() -> None:
    assert KEY_A != KEY_B
    assert KEY_A.startswith("oidc:")
    assert KEY_B.startswith("oidc:")


def test_missing_issuer_rejects_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(_endpoint: str) -> str:
        msg = "missing issuer"
        raise OpenIdIssuerResolutionError(msg)

    monkeypatch.setattr(
        "miramedia.auth.runtime.resolve_openid_provider_key",
        _boom,
    )
    from miramedia.auth.config import AuthConfig, OpenIdConfig

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


def test_display_rename_preserves_issuer_derived_key() -> None:
    key_first = provider_key_from_issuer(ISSUER_A)
    key_second = provider_key_from_issuer(ISSUER_A)
    assert key_first == key_second


def test_reconcile_dedupes_provable_legacy_when_display_renamed() -> None:
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


def test_reconcile_removes_provable_legacy_matching_current_display() -> None:
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
        oauth_name="CurrentDisplay",
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
            display_name="CurrentDisplay",
            provider_key=KEY_A,
        )

    asyncio.run(_run())
    user_db.session.delete.assert_awaited_once_with(legacy)


def test_reconcile_migrates_legacy_display_row_to_issuer_key() -> None:
    user_id = uuid.uuid4()
    legacy = SimpleNamespace(
        id=uuid.uuid4(),
        account_id="sub-x",
        oauth_name="CurrentDisplay",
        access_token="token",
        account_email="user@example.com",
        expires_at=None,
        refresh_token=None,
    )
    user = SimpleNamespace(id=user_id, oauth_accounts=[legacy])
    user_db = AsyncMock()

    async def _lookup(oauth: str, _account_id: str) -> object:
        if oauth == KEY_A:
            raise user_exceptions.UserNotExists()
        return user

    user_db.get_by_oauth_account = AsyncMock(side_effect=_lookup)
    user_db.update_oauth_account = AsyncMock(return_value=user)
    user_db.session = AsyncMock()

    async def _run() -> None:
        await reconcile_legacy_oauth_account(
            user_db,
            account_id="sub-x",
            display_name="CurrentDisplay",
            provider_key=KEY_A,
        )

    asyncio.run(_run())
    user_db.update_oauth_account.assert_awaited_once()
    assert user_db.update_oauth_account.await_args.args[2]["oauth_name"] == KEY_A


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


def test_resolve_uses_exact_issuer_bytes_for_key() -> None:
    issuer = "https://CaseSensitive.example/OIDC"
    with patch(
        "miramedia.auth.oauth_identity._fetch_openid_discovery_issuer_sync",
        return_value=issuer,
    ):
        key = asyncio.run(resolve_openid_provider_key(ENDPOINT_A))
    assert key == provider_key_from_issuer(issuer)
    assert key != provider_key_from_issuer(issuer.lower())
