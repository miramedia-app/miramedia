"""Unit tests for password-reset session and API token revocation."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi_users import BaseUserManager
from fastapi_users.jwt import generate_jwt

import miramedia.movies.models
import miramedia.shows.models  # noqa: F401
from miramedia.auth.db import User
from miramedia.auth.users import (
    _IAT_LEEWAY_S,
    CachedJWTStrategy,
    UserManager,
    invalidate_auth_cache,
)


@pytest.fixture(autouse=True)
def _clear_auth_cache() -> None:
    invalidate_auth_cache()
    yield
    invalidate_auth_cache()


def _make_user(
    *,
    credentials_changed_at: datetime | None = None,
) -> User:
    user = User()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.is_active = True
    user.is_superuser = False
    user.is_verified = True
    user.oauth_accounts = []
    user.credentials_changed_at = credentials_changed_at
    return user


def _make_user_manager(user: User) -> BaseUserManager[User, uuid.UUID]:
    manager = MagicMock(spec=BaseUserManager)
    manager.parse_id = lambda user_id: uuid.UUID(str(user_id))
    manager.get = AsyncMock(return_value=user)
    return manager


def _make_strategy() -> CachedJWTStrategy:
    return CachedJWTStrategy(secret="test", lifetime_seconds=3600)


def test_jwt_minted_before_reset_is_rejected() -> None:
    async def _run() -> None:
        strategy = _make_strategy()
        user = _make_user(credentials_changed_at=None)
        manager = _make_user_manager(user)

        token = await strategy.write_token(user)
        user.credentials_changed_at = datetime.now(UTC) + timedelta(
            seconds=_IAT_LEEWAY_S + 5
        )

        assert await strategy.read_token(token, manager) is None

    asyncio.run(_run())


def test_jwt_minted_after_reset_within_leeway_is_accepted() -> None:
    async def _run() -> None:
        strategy = _make_strategy()
        user = _make_user(
            credentials_changed_at=datetime.now(UTC) - timedelta(seconds=1)
        )
        manager = _make_user_manager(user)

        token = await strategy.write_token(user)

        result = await strategy.read_token(token, manager)
        assert result is not None
        assert result.id == user.id

    asyncio.run(_run())


def test_legacy_token_without_iat_accepted_when_no_credentials_change() -> None:
    async def _run() -> None:
        strategy = _make_strategy()
        user = _make_user(credentials_changed_at=None)
        manager = _make_user_manager(user)

        token = generate_jwt(
            {"sub": str(user.id), "aud": strategy.token_audience},
            "test",
            3600,
        )

        result = await strategy.read_token(token, manager)
        assert result is not None
        assert result.id == user.id

    asyncio.run(_run())


def test_legacy_token_without_iat_rejected_after_credentials_change() -> None:
    async def _run() -> None:
        strategy = _make_strategy()
        user = _make_user(credentials_changed_at=datetime.now(UTC))
        manager = _make_user_manager(user)

        token = generate_jwt(
            {"sub": str(user.id), "aud": strategy.token_audience},
            "test",
            3600,
        )

        assert await strategy.read_token(token, manager) is None

    asyncio.run(_run())


def test_cache_invalidation_allows_stale_token_rejection() -> None:
    async def _run() -> None:
        strategy = _make_strategy()
        user = _make_user(credentials_changed_at=None)
        manager = _make_user_manager(user)

        token = await strategy.write_token(user)
        first_read = await strategy.read_token(token, manager)
        assert first_read is not None

        user.credentials_changed_at = datetime.now(UTC) + timedelta(
            seconds=_IAT_LEEWAY_S + 5
        )
        invalidate_auth_cache(user.id)

        assert await strategy.read_token(token, manager) is None

    asyncio.run(_run())


def test_write_token_includes_iat_claim() -> None:
    async def _run() -> None:
        strategy = _make_strategy()
        user = _make_user()

        token = await strategy.write_token(user)
        payload = jwt.decode(
            token, options={"verify_signature": False}, algorithms=["HS256"]
        )

        assert "iat" in payload

    asyncio.run(_run())


def test_on_after_reset_password_revokes_sessions_and_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        manager = UserManager.__new__(UserManager)
        user = _make_user()

        update_calls: list[dict[str, Any]] = []
        execute_calls: list[Any] = []
        commit_calls: list[None] = []
        cache_invalidations: list[uuid.UUID | None] = []

        async def fake_update(updated_user: User, update_dict: dict[str, Any]) -> User:
            update_calls.append(update_dict)
            return updated_user

        session = MagicMock()
        session.execute = AsyncMock(side_effect=lambda stmt: execute_calls.append(stmt))
        session.commit = AsyncMock(side_effect=lambda: commit_calls.append(None))

        user_db = MagicMock()
        user_db.update = fake_update
        user_db.session = session
        manager.user_db = user_db

        monkeypatch.setattr(
            "miramedia.auth.users.invalidate_auth_cache",
            lambda user_id=None: cache_invalidations.append(user_id),
        )

        await manager.on_after_reset_password(user)

        assert len(update_calls) == 1
        assert "credentials_changed_at" in update_calls[0]
        assert len(execute_calls) == 1
        assert len(commit_calls) == 1
        assert cache_invalidations == [user.id]

    asyncio.run(_run())


def test_on_after_update_without_password_does_not_revoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        manager = UserManager.__new__(UserManager)
        user = _make_user()

        update_calls: list[dict[str, Any]] = []
        execute_calls: list[Any] = []
        commit_calls: list[None] = []
        cache_invalidations: list[uuid.UUID | None] = []

        async def fake_update(updated_user: User, update_dict: dict[str, Any]) -> User:
            update_calls.append(update_dict)
            return updated_user

        session = MagicMock()
        session.execute = AsyncMock(side_effect=lambda stmt: execute_calls.append(stmt))
        session.commit = AsyncMock(side_effect=lambda: commit_calls.append(None))

        user_db = MagicMock()
        user_db.update = fake_update
        user_db.session = session
        manager.user_db = user_db

        monkeypatch.setattr(
            "miramedia.auth.users.invalidate_auth_cache",
            lambda user_id=None: cache_invalidations.append(user_id),
        )

        await manager.on_after_update(user, {"is_active": False})

        assert not any("credentials_changed_at" in call for call in update_calls)
        assert len(execute_calls) == 0
        assert len(commit_calls) == 0
        assert cache_invalidations == [user.id]

    asyncio.run(_run())


def test_on_after_update_with_password_revokes_sessions_and_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _run() -> None:
        manager = UserManager.__new__(UserManager)
        user = _make_user()

        update_calls: list[dict[str, Any]] = []
        execute_calls: list[Any] = []
        commit_calls: list[None] = []
        cache_invalidations: list[uuid.UUID | None] = []

        async def fake_update(updated_user: User, update_dict: dict[str, Any]) -> User:
            update_calls.append(update_dict)
            return updated_user

        session = MagicMock()
        session.execute = AsyncMock(side_effect=lambda stmt: execute_calls.append(stmt))
        session.commit = AsyncMock(side_effect=lambda: commit_calls.append(None))

        user_db = MagicMock()
        user_db.update = fake_update
        user_db.session = session
        manager.user_db = user_db

        monkeypatch.setattr(
            "miramedia.auth.users.invalidate_auth_cache",
            lambda user_id=None: cache_invalidations.append(user_id),
        )

        await manager.on_after_update(user, {"password": "newpass123"})

        assert len(update_calls) == 1
        assert "credentials_changed_at" in update_calls[0]
        assert len(execute_calls) == 1
        assert len(commit_calls) == 1
        assert cache_invalidations == [user.id, user.id]

    asyncio.run(_run())
