"""Canonical OAuth provider identity and legacy account reconciliation."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from fastapi_users import exceptions as user_exceptions

from miramedia.auth.runtime import OAUTH_ROUTE_NAME

if TYPE_CHECKING:
    from fastapi_users.db import BaseUserDatabase

    from miramedia.auth.db import User

log = logging.getLogger(__name__)


class OAuthProviderConflictError(Exception):
    """Legacy OAuth account cannot be reconciled without merging users."""


@dataclass(frozen=True, slots=True)
class LegacyOAuthMigrationPlan:
    action: Literal["noop", "rename", "conflict"]
    legacy_oauth_name: str | None = None


def plan_legacy_oauth_migration(
    *,
    account_id: str,  # noqa: ARG001 -- reserved for conflict diagnostics
    display_name: str | None,
    canonical_user_id: uuid.UUID | None,
    legacy_user_id: uuid.UUID | None,
) -> LegacyOAuthMigrationPlan:
    """Pure decision for legacy oauth_name migration before oauth_callback."""
    canonical = OAUTH_ROUTE_NAME
    if canonical_user_id is not None:
        if legacy_user_id is not None and legacy_user_id != canonical_user_id:
            return LegacyOAuthMigrationPlan(action="conflict")
        return LegacyOAuthMigrationPlan(action="noop")

    if legacy_user_id is None:
        return LegacyOAuthMigrationPlan(action="noop")

    legacy_name = (display_name or "").strip()
    if not legacy_name or legacy_name == canonical:
        return LegacyOAuthMigrationPlan(action="noop")

    return LegacyOAuthMigrationPlan(action="rename", legacy_oauth_name=legacy_name)


async def reconcile_legacy_oauth_account(
    user_db: Any,
    *,
    account_id: str,
    display_name: str | None,
) -> None:
    """Idempotently rename a legacy oauth_name row to the canonical provider key."""
    canonical = OAUTH_ROUTE_NAME
    canonical_user: User | None = None
    legacy_user: User | None = None

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

    plan = plan_legacy_oauth_migration(
        account_id=account_id,
        display_name=display_name,
        canonical_user_id=getattr(canonical_user, "id", None),
        legacy_user_id=getattr(legacy_user, "id", None),
    )
    if plan.action == "conflict":
        msg = f"OAuth account {account_id!r} is bound to multiple users"
        raise OAuthProviderConflictError(msg)
    if plan.action != "rename" or legacy_user is None or plan.legacy_oauth_name is None:
        return

    for account in legacy_user.oauth_accounts:
        if (
            account.account_id == account_id
            and account.oauth_name == plan.legacy_oauth_name
        ):
            await user_db.update_oauth_account(
                legacy_user,
                account,
                {
                    "oauth_name": canonical,
                    "access_token": account.access_token,
                    "account_id": account.account_id,
                    "account_email": account.account_email,
                    "expires_at": account.expires_at,
                    "refresh_token": account.refresh_token,
                },
            )
            log.info(
                "Migrated legacy OAuth provider %r to %r for account %s",
                plan.legacy_oauth_name,
                canonical,
                account_id,
            )
            return
