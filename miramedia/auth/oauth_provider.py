"""Canonical OAuth provider identity and legacy account reconciliation."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi_users import exceptions as user_exceptions

from miramedia.auth.oauth_identity import is_issuer_derived_provider_key

log = logging.getLogger(__name__)


class OAuthProviderConflictError(Exception):
    """Legacy OAuth account cannot be reconciled without merging users."""


class OAuthProviderReconciliationError(Exception):
    """Legacy OAuth account cannot be reconciled automatically."""


@dataclass(frozen=True, slots=True)
class LegacyOAuthMigrationPlan:
    action: Literal["noop", "rename", "dedupe", "conflict", "manual"]
    legacy_oauth_name: str | None = None


def plan_legacy_oauth_migration(
    *,
    account_id: str,  # noqa: ARG001 -- reserved for conflict diagnostics
    display_name: str | None,
    canonical_user_id: uuid.UUID | None,
    legacy_user_id: uuid.UUID | None,
    same_user_legacy_count: int = 0,
    provable_legacy_count: int = 0,
) -> LegacyOAuthMigrationPlan:
    if canonical_user_id is not None and legacy_user_id is not None:
        if canonical_user_id != legacy_user_id:
            return LegacyOAuthMigrationPlan(action="conflict")

    if canonical_user_id is not None:
        if provable_legacy_count > 0:
            return LegacyOAuthMigrationPlan(action="dedupe")
        return LegacyOAuthMigrationPlan(action="noop")

    if legacy_user_id is None:
        return LegacyOAuthMigrationPlan(action="noop")

    legacy_name = (display_name or "").strip()
    if not legacy_name or is_issuer_derived_provider_key(legacy_name):
        if same_user_legacy_count > 1:
            return LegacyOAuthMigrationPlan(action="dedupe")
        return LegacyOAuthMigrationPlan(action="noop")

    if provable_legacy_count == 0 and same_user_legacy_count > 0:
        return LegacyOAuthMigrationPlan(action="manual")

    if same_user_legacy_count > 1:
        return LegacyOAuthMigrationPlan(action="dedupe", legacy_oauth_name=legacy_name)

    if provable_legacy_count == 1:
        return LegacyOAuthMigrationPlan(action="rename", legacy_oauth_name=legacy_name)

    return LegacyOAuthMigrationPlan(action="noop")


def _legacy_accounts_for(
    user: Any,  # noqa: ANN401 -- fastapi-users user duck type
    *,
    account_id: str,
    provider_key: str,
) -> list[Any]:
    return [
        account
        for account in user.oauth_accounts
        if account.account_id == account_id
        and account.oauth_name != provider_key
        and not is_issuer_derived_provider_key(account.oauth_name)
    ]


def _provable_legacy_accounts_for(
    user: Any,  # noqa: ANN401
    *,
    account_id: str,
    display_name: str,
    provider_key: str,
) -> list[Any]:
    legacy_name = display_name.strip()
    if not legacy_name or is_issuer_derived_provider_key(legacy_name):
        return []
    return [
        account
        for account in user.oauth_accounts
        if account.account_id == account_id
        and account.oauth_name == legacy_name
        and account.oauth_name != provider_key
    ]


def _pick_canonical_legacy_account(accounts: list[Any]) -> Any:  # noqa: ANN401
    return sorted(accounts, key=lambda account: str(account.id))[0]


async def reconcile_legacy_oauth_account(
    user_db: Any,  # noqa: ANN401 -- fastapi-users SQLAlchemyUserDatabase
    *,
    account_id: str,
    display_name: str | None,
    provider_key: str,
) -> None:
    """Normalize legacy oauth_name rows for one external account id."""
    if not is_issuer_derived_provider_key(provider_key):
        msg = "OAuth provider key is invalid"
        raise OAuthProviderReconciliationError(msg)

    canonical_user: Any | None = None
    legacy_user: Any | None = None

    try:
        canonical_user = await user_db.get_by_oauth_account(provider_key, account_id)
    except user_exceptions.UserNotExists:
        canonical_user = None

    legacy_name = (display_name or "").strip()
    if legacy_name and not is_issuer_derived_provider_key(legacy_name):
        try:
            legacy_user = await user_db.get_by_oauth_account(legacy_name, account_id)
        except user_exceptions.UserNotExists:
            legacy_user = None

    if canonical_user is not None:
        if legacy_user is not None and legacy_user.id != canonical_user.id:
            msg = f"OAuth account {account_id!r} is bound to multiple users"
            raise OAuthProviderConflictError(msg)
        provable = _provable_legacy_accounts_for(
            canonical_user,
            account_id=account_id,
            display_name=legacy_name,
            provider_key=provider_key,
        )
        for account in provable:
            await user_db.session.delete(account)
        if provable:
            await user_db.session.commit()
            log.info(
                "Removed %d provable legacy OAuth row(s) for account %s",
                len(provable),
                account_id,
            )
        return

    owner = legacy_user
    provable_legacy_count = (
        len(
            _provable_legacy_accounts_for(
                owner,
                account_id=account_id,
                display_name=legacy_name,
                provider_key=provider_key,
            )
        )
        if owner is not None
        else 0
    )
    same_user_legacy_count = (
        len(
            _legacy_accounts_for(
                owner, account_id=account_id, provider_key=provider_key
            )
        )
        if owner is not None
        else 0
    )
    plan = plan_legacy_oauth_migration(
        account_id=account_id,
        display_name=display_name,
        canonical_user_id=getattr(canonical_user, "id", None),
        legacy_user_id=getattr(legacy_user, "id", None),
        same_user_legacy_count=same_user_legacy_count,
        provable_legacy_count=provable_legacy_count,
    )
    if plan.action == "conflict":
        msg = f"OAuth account {account_id!r} is bound to multiple users"
        raise OAuthProviderConflictError(msg)
    if plan.action == "manual":
        log.warning(
            "OAuth account %s has legacy provider aliases that do not match the "
            "current display name %r; manual reconciliation is required",
            account_id,
            legacy_name,
        )
        return

    if owner is None:
        return

    legacy_accounts = _provable_legacy_accounts_for(
        owner,
        account_id=account_id,
        display_name=legacy_name,
        provider_key=provider_key,
    )
    if not legacy_accounts:
        return

    if plan.action == "dedupe":
        keep = _pick_canonical_legacy_account(legacy_accounts)
        for account in legacy_accounts:
            if account.id != keep.id:
                await user_db.session.delete(account)
        legacy_accounts = [keep]

    keep = _pick_canonical_legacy_account(legacy_accounts)
    for account in legacy_accounts:
        if account.id != keep.id:
            await user_db.session.delete(account)
    await user_db.update_oauth_account(
        owner,
        keep,
        {
            "oauth_name": provider_key,
            "access_token": keep.access_token,
            "account_id": keep.account_id,
            "account_email": keep.account_email,
            "expires_at": keep.expires_at,
            "refresh_token": keep.refresh_token,
        },
    )
    log.info(
        "Migrated legacy OAuth provider row to issuer-derived key for account %s",
        account_id,
    )
