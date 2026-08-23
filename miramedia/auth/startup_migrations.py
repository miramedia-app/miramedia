"""Durable one-shot startup migration markers for auth."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.auth.db import User
from miramedia.database import Base

log = logging.getLogger(__name__)

ADMIN_EMAILS_SUPERUSER_PROMOTION = "admin_emails_superuser_promotion"
_ADMIN_EMAILS_PROMOTION_LOCK_ID = 4871260043


class AuthStartupMigration(Base):
    __tablename__ = "auth_startup_migration"

    name: Mapped[str] = mapped_column(String(length=128), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


def normalized_admin_emails(emails: list[str] | None) -> list[str]:
    return [e.strip().lower() for e in (emails or []) if e and e.strip()]


async def is_admin_emails_promotion_complete(db: AsyncSession) -> bool:
    row = await db.get(AuthStartupMigration, ADMIN_EMAILS_SUPERUSER_PROMOTION)
    return row is not None


async def should_grant_admin_emails_superuser(db: AsyncSession) -> bool:
    """Return whether deprecated ``admin_emails`` may still grant superuser."""
    return not await is_admin_emails_promotion_complete(db)


def log_stale_admin_emails_warning() -> None:
    log.warning(
        "auth.admin_emails is deprecated and no longer grants superuser on startup "
        "(one-time migration already completed). Manage superuser status from the "
        "Users page and remove admin_emails from your config to silence this warning."
    )


def log_admin_emails_deprecation_warning() -> None:
    log.warning(
        "auth.admin_emails is deprecated. Existing users matching this list have been "
        "promoted to superuser. Manage superuser status from the Users page going "
        "forward; remove admin_emails from your config to silence this warning."
    )


async def record_admin_emails_promotion_complete(db: AsyncSession) -> None:
    db.add(
        AuthStartupMigration(
            name=ADMIN_EMAILS_SUPERUSER_PROMOTION,
            completed_at=datetime.now(UTC),
        )
    )


async def acquire_admin_emails_promotion_lock(db: AsyncSession) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _ADMIN_EMAILS_PROMOTION_LOCK_ID},
    )


@dataclass(frozen=True, slots=True)
class AdminEmailPromotionResult:
    promoted: list[str]
    matched_emails: list[str]


async def promote_users_for_admin_emails(
    db: AsyncSession, emails: list[str]
) -> AdminEmailPromotionResult:
    from sqlalchemy import update as sa_update

    stmt = select(User.id, User.email, User.is_superuser).where(User.email.in_(emails))
    rows = (await db.execute(stmt)).all()
    matched_emails = [row.email for row in rows]
    promoted = [row.email for row in rows if not row.is_superuser]
    if promoted:
        await db.execute(
            sa_update(User).where(User.email.in_(promoted)).values(is_superuser=True)
        )
    return AdminEmailPromotionResult(promoted=promoted, matched_emails=matched_emails)
