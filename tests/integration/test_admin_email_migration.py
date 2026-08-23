"""PostgreSQL integration tests for one-shot admin_emails migration."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from unittest.mock import patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.auth.db import User
from miramedia.auth.startup_migrations import (
    ADMIN_EMAILS_SUPERUSER_PROMOTION,
    AuthStartupMigration,
)
from miramedia.auth.users import migrate_admin_emails_to_superuser_flag

pytestmark = pytest.mark.integration

_ADMIN_EMAIL = "legacy-admin@example.com"


@pytest.fixture(autouse=True)
def wire_app_session_local(session_factory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Route startup migration helpers at the integration database."""
    monkeypatch.setattr("miramedia.database.SessionLocal", session_factory)


async def _insert_user(
    db: AsyncSession,
    *,
    email: str = _ADMIN_EMAIL,
    is_superuser: bool = False,
) -> uuid.UUID:
    user_id = uuid.uuid4()
    db.add(
        User(
            id=user_id,
            email=email,
            hashed_password="hash",
            is_active=True,
            is_superuser=is_superuser,
            is_verified=True,
        )
    )
    await db.commit()
    return user_id


async def _user_is_superuser(db: AsyncSession, user_id: uuid.UUID) -> bool:
    row = await db.get(User, user_id)
    assert row is not None
    return bool(row.is_superuser)


async def _marker_exists(db: AsyncSession) -> bool:
    row = await db.get(AuthStartupMigration, ADMIN_EMAILS_SUPERUSER_PROMOTION)
    return row is not None


def test_first_run_promotes_legacy_user(db: AsyncSession, run_async: Callable) -> None:
    async def _run() -> None:
        user_id = await _insert_user(db, is_superuser=False)

        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = [_ADMIN_EMAIL]
            await migrate_admin_emails_to_superuser_flag()

        assert await _user_is_superuser(db, user_id) is True
        assert await _marker_exists(db) is True

    run_async(_run())


def test_second_run_after_demotion_does_not_repromote(
    db: AsyncSession, run_async: Callable
) -> None:
    async def _run() -> None:
        user_id = await _insert_user(db, is_superuser=False)

        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = [_ADMIN_EMAIL]
            await migrate_admin_emails_to_superuser_flag()

        await db.execute(
            update(User).where(User.id == user_id).values(is_superuser=False)
        )
        await db.commit()
        assert await _user_is_superuser(db, user_id) is False

        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = [_ADMIN_EMAIL]
            await migrate_admin_emails_to_superuser_flag()

        assert await _user_is_superuser(db, user_id) is False

    run_async(_run())


def test_empty_admin_emails_list_is_noop_without_marker(
    db: AsyncSession, run_async: Callable
) -> None:
    async def _run() -> None:
        user_id = await _insert_user(db, is_superuser=False)

        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = []
            await migrate_admin_emails_to_superuser_flag()

        assert await _user_is_superuser(db, user_id) is False
        assert await _marker_exists(db) is False

    run_async(_run())


def test_failed_transaction_retry_promotes_on_next_startup(
    db: AsyncSession, run_async: Callable
) -> None:
    async def _run() -> None:
        user_id = await _insert_user(db, is_superuser=False)

        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = [_ADMIN_EMAIL]
            with patch(
                "miramedia.auth.startup_migrations.record_admin_emails_promotion_complete",
                side_effect=RuntimeError("marker write failed"),
            ):
                with pytest.raises(RuntimeError, match="marker write failed"):
                    await migrate_admin_emails_to_superuser_flag()

        assert await _marker_exists(db) is False
        assert await _user_is_superuser(db, user_id) is False

        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = [_ADMIN_EMAIL]
            await migrate_admin_emails_to_superuser_flag()

        assert await _user_is_superuser(db, user_id) is True
        assert await _marker_exists(db) is True

        marker = await db.scalar(
            select(AuthStartupMigration.name).where(
                AuthStartupMigration.name == ADMIN_EMAILS_SUPERUSER_PROMOTION
            )
        )
        assert marker == ADMIN_EMAILS_SUPERUSER_PROMOTION

    run_async(_run())


def test_no_matching_users_defers_marker(db: AsyncSession, run_async: Callable) -> None:
    async def _run() -> None:
        other_user_id = await _insert_user(db, email="other@example.com")

        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = [_ADMIN_EMAIL]
            await migrate_admin_emails_to_superuser_flag()

        assert await _user_is_superuser(db, other_user_id) is False
        assert await _marker_exists(db) is False

    run_async(_run())


def test_already_superuser_match_records_marker_without_promotion(
    db: AsyncSession, run_async: Callable
) -> None:
    async def _run() -> None:
        user_id = await _insert_user(db, is_superuser=True)

        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = [_ADMIN_EMAIL]
            await migrate_admin_emails_to_superuser_flag()

        assert await _user_is_superuser(db, user_id) is True
        assert await _marker_exists(db) is True

    run_async(_run())


def test_second_startup_with_no_matches_still_defers_marker(
    db: AsyncSession, run_async: Callable
) -> None:
    async def _run() -> None:
        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = [_ADMIN_EMAIL]
            await migrate_admin_emails_to_superuser_flag()

        assert await _marker_exists(db) is False

        with patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg:
            mock_cfg.return_value.auth.admin_emails = [_ADMIN_EMAIL]
            await migrate_admin_emails_to_superuser_flag()

        assert await _marker_exists(db) is False

    run_async(_run())
