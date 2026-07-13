"""Legacy OAuth provider identity reconciliation tests."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi_users import exceptions as user_exceptions

from miramedia.auth.oauth_provider import (
    OAuthProviderConflictError,
    plan_legacy_oauth_migration,
    reconcile_legacy_oauth_account,
)
from miramedia.auth.runtime import OAUTH_ROUTE_NAME


def test_plan_legacy_migration_noop_when_canonical_exists() -> None:
    user_id = uuid.uuid4()
    plan = plan_legacy_oauth_migration(
        account_id="acct-1",
        display_name="LegacyName",
        canonical_user_id=user_id,
        legacy_user_id=user_id,
        has_canonical_on_same_user=True,
    )
    assert plan.action == "noop"


def test_plan_legacy_migration_dedupe_when_canonical_and_legacy_same_user() -> None:
    user_id = uuid.uuid4()
    plan = plan_legacy_oauth_migration(
        account_id="acct-1",
        display_name="LegacyName",
        canonical_user_id=user_id,
        legacy_user_id=user_id,
        same_user_legacy_count=1,
        has_canonical_on_same_user=True,
    )
    assert plan.action == "dedupe"


def test_plan_legacy_migration_rename_when_only_legacy_exists() -> None:
    plan = plan_legacy_oauth_migration(
        account_id="acct-1",
        display_name="LegacyName",
        canonical_user_id=None,
        legacy_user_id=uuid.uuid4(),
    )
    assert plan.action == "rename"
    assert plan.legacy_oauth_name == "LegacyName"


def test_plan_legacy_migration_conflict_for_different_users() -> None:
    plan = plan_legacy_oauth_migration(
        account_id="acct-1",
        display_name="LegacyName",
        canonical_user_id=uuid.uuid4(),
        legacy_user_id=uuid.uuid4(),
    )
    assert plan.action == "conflict"


def test_reconcile_legacy_oauth_account_renames_row() -> None:
    user_id = uuid.uuid4()
    account = SimpleNamespace(
        id=uuid.uuid4(),
        account_id="acct-1",
        oauth_name="LegacyName",
        access_token="token",
        account_email="user@example.com",
        expires_at=None,
        refresh_token=None,
    )
    user = SimpleNamespace(id=user_id, oauth_accounts=[account])
    user_db = AsyncMock()

    async def _lookup(oauth: str, _account_id: str) -> object:
        if oauth == OAUTH_ROUTE_NAME:
            raise user_exceptions.UserNotExists()
        return user

    user_db.get_by_oauth_account = AsyncMock(side_effect=_lookup)
    user_db.update_oauth_account = AsyncMock(return_value=user)
    user_db.session = AsyncMock()

    async def _run() -> None:
        await reconcile_legacy_oauth_account(
            user_db,
            account_id="acct-1",
            display_name="LegacyName",
        )

    asyncio.run(_run())
    user_db.update_oauth_account.assert_awaited_once()
    assert (
        user_db.update_oauth_account.await_args.args[2]["oauth_name"]
        == OAUTH_ROUTE_NAME
    )


def test_reconcile_legacy_oauth_account_dedupes_canonical_and_legacy_same_user() -> (
    None
):
    user_id = uuid.uuid4()
    canonical = SimpleNamespace(
        id=uuid.uuid4(),
        account_id="acct-1",
        oauth_name=OAUTH_ROUTE_NAME,
        access_token="token",
        account_email="user@example.com",
        expires_at=None,
        refresh_token=None,
    )
    legacy = SimpleNamespace(
        id=uuid.uuid4(),
        account_id="acct-1",
        oauth_name="LegacyName",
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
            account_id="acct-1",
            display_name="LegacyName",
        )

    asyncio.run(_run())
    user_db.session.delete.assert_awaited_once_with(legacy)
    user_db.session.commit.assert_awaited_once()


def test_reconcile_legacy_oauth_account_fails_closed_on_conflict() -> None:
    canonical_user = SimpleNamespace(id=uuid.uuid4(), oauth_accounts=[])
    legacy_user = SimpleNamespace(id=uuid.uuid4(), oauth_accounts=[])

    async def _lookup(oauth: str, _account_id: str) -> object:
        if oauth == OAUTH_ROUTE_NAME:
            return canonical_user
        return legacy_user

    user_db = AsyncMock()
    user_db.get_by_oauth_account = AsyncMock(side_effect=_lookup)

    async def _run() -> None:
        with pytest.raises(OAuthProviderConflictError):
            await reconcile_legacy_oauth_account(
                user_db,
                account_id="acct-1",
                display_name="LegacyName",
            )

    asyncio.run(_run())
