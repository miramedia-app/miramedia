"""Canonical OAuth provider identity and legacy account reconciliation."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi_users import exceptions as user_exceptions

from miramedia.auth.runtime import OAUTH_ROUTE_NAME

log = logging.getLogger(__name__)


class OAuthProviderConflictError(Exception):
    """Legacy OAuth account cannot be reconciled without merging users."""


@dataclass(frozen=True, slots=True)
class LegacyOAuthMigrationPlan:
    action: Literal["noop", "rename", "dedupe", "conflict"]
    legacy_oauth_name: str | None = None


def plan_legacy_oauth_migration(
    *,
    account_id: str,  # noqa: ARG001 -- reserved for conflict diagnostics
    display_name: str | None,
    canonical_user_id: uuid.UUID | None,
    legacy_user_id: uuid.UUID | None,
    same_user_legacy_count: int = 0,
    has_canonical_on_same_user: bool = False,
) -> LegacyOAuthMigrationPlan:
    """Pure decision for legacy oauth_name migration before oauth_callback."""
    canonical = OAUTH_ROUTE_NAME
    if canonical_user_id is not None and legacy_user_id is not None:
        if canonical_user_id != legacy_user_id:
            return LegacyOAuthMigrationPlan(action="conflict")
        if has_canonical_on_same_user and same_user_legacy_count > 0:
            return LegacyOAuthMigrationPlan(action="dedupe")
        return LegacyOAuthMigrationPlan(action="noop")

    if legacy_user_id is None:
        return LegacyOAuthMigrationPlan(action="noop")

    legacy_name = (display_name or "").strip()
    if not legacy_name or legacy_name == canonical:
        if same_user_legacy_count > 1:
            return LegacyOAuthMigrationPlan(action="dedupe")
        return LegacyOAuthMigrationPlan(action="noop")

    if same_user_legacy_count > 1:
        return LegacyOAuthMigrationPlan(action="dedupe", legacy_oauth_name=legacy_name)

    return LegacyOAuthMigrationPlan(action="rename", legacy_oauth_name=legacy_name)


def _legacy_accounts_for(
    user: Any,  # noqa: ANN401 -- fastapi-users user duck type
    *,
    account_id: str,
    canonical: str,
) -> list[Any]:
    return [
        account
        for account in user.oauth_accounts
        if account.account_id == account_id and account.oauth_name != canonical
    ]


def _pick_canonical_legacy_account(accounts: list[Any]) -> Any:  # noqa: ANN401
    return sorted(accounts, key=lambda account: str(account.id))[0]


async def reconcile_legacy_oauth_account(
    user_db: Any,  # noqa: ANN401 -- fastapi-users SQLAlchemyUserDatabase
    *,
    account_id: str,
    display_name: str | None,
) -> None:
    """Normalize legacy oauth_name rows for one external account id."""
    canonical = OAUTH_ROUTE_NAME
    canonical_user: Any | None = None
    legacy_user: Any | None = None

    try:
        canonical_user = await user_db.get_by_oauth_account(canonical, account_id)
    except user_exceptions.UserNotExists:
        canonical_user = None

    legacy_name = (display_name or "").strip()
    if legacy_name and legacy_name != canonical:
        try:
            legacy_user = await user_db.get_by_oauth_account(legacy_name, account_id)
        except user_exceptions.UserNotExists:
            legacy_user = None

    owner = canonical_user or legacy_user
    same_user_legacy_count = (
        len(_legacy_accounts_for(owner, account_id=account_id, canonical=canonical))
        if owner is not None
        else 0
    )
    plan = plan_legacy_oauth_migration(
        account_id=account_id,
        display_name=display_name,
        canonical_user_id=getattr(canonical_user, "id", None),
        legacy_user_id=getattr(legacy_user, "id", None),
        same_user_legacy_count=same_user_legacy_count,
        has_canonical_on_same_user=canonical_user is not None,
    )
    if plan.action == "conflict":
        msg = f"OAuth account {account_id!r} is bound to multiple users"
        raise OAuthProviderConflictError(msg)

    if owner is None:
        return

    legacy_accounts = _legacy_accounts_for(
        owner, account_id=account_id, canonical=canonical
    )
    if not legacy_accounts:
        return

    if plan.action == "dedupe" and canonical_user is not None:
        for account in legacy_accounts:
            await user_db.session.delete(account)
        await user_db.session.commit()
        log.info(
            "Removed %d duplicate legacy OAuth row(s) for account %s",
            len(legacy_accounts),
            account_id,
        )
        return

    keep = _pick_canonical_legacy_account(legacy_accounts)
    for account in legacy_accounts:
        if account.id != keep.id:
            await user_db.session.delete(account)
    await user_db.update_oauth_account(
        owner,
        keep,
        {
            "oauth_name": canonical,
            "access_token": keep.access_token,
            "account_id": keep.account_id,
            "account_email": keep.account_email,
            "expires_at": keep.expires_at,
            "refresh_token": keep.refresh_token,
        },
    )
    log.info(
        "Migrated legacy OAuth provider rows to %r for account %s",
        canonical,
        account_id,
    )
