"""Tests for CachedJWTStrategy auth-cache expiry on hits."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from miramedia.auth.db import User
from miramedia.auth.users import (
    CachedJWTStrategy,
    _detached_user_copy,
    _token_cache_key,
    _user_cache,
    invalidate_auth_cache,
)

_TEST_SECRET = "test-jwt-cache-secret"


def _make_user() -> User:
    user = User()
    user.id = uuid.uuid4()
    user.email = "cache-test@example.com"
    user.is_active = True
    user.is_verified = True
    user.is_superuser = False
    user.hashed_password = "hashed"
    user.oauth_accounts = []
    return user


class _FakeUserManager:
    def __init__(self, user: User) -> None:
        self.user = user
        self.get_calls = 0

    def parse_id(self, user_id: str) -> uuid.UUID:
        return uuid.UUID(user_id)

    async def get(self, user_id: uuid.UUID) -> User:  # noqa: ARG002
        self.get_calls += 1
        return self.user


@pytest.fixture(autouse=True)
def _clear_auth_cache() -> None:
    invalidate_auth_cache()
    yield
    invalidate_auth_cache()


def test_cache_hit_skips_user_manager_get() -> None:
    user = _make_user()
    manager = _FakeUserManager(user)
    strategy = CachedJWTStrategy(secret=_TEST_SECRET, lifetime_seconds=3600)
    token = asyncio.run(strategy.write_token(user))

    first = asyncio.run(strategy.read_token(token, manager))
    second = asyncio.run(strategy.read_token(token, manager))

    assert first is not None
    assert second is not None
    assert manager.get_calls == 1


def test_expired_cached_token_is_rejected() -> None:
    user = _make_user()
    manager = _FakeUserManager(user)
    strategy = CachedJWTStrategy(secret=_TEST_SECRET, lifetime_seconds=3600)
    expired_at = datetime.now(UTC) - timedelta(seconds=10)
    token = jwt.encode(
        {
            "sub": str(user.id),
            "aud": ["fastapi-users:auth"],
            "exp": expired_at,
        },
        _TEST_SECRET,
        algorithm="HS256",
    )
    key = _token_cache_key(token)
    assert key is not None
    _user_cache[key] = (_detached_user_copy(user), expired_at.timestamp())

    result = asyncio.run(strategy.read_token(token, manager))

    assert result is None
    assert manager.get_calls == 0


def test_invalidate_auth_cache_evicts_tuple_entries() -> None:
    user = _make_user()
    manager = _FakeUserManager(user)
    strategy = CachedJWTStrategy(secret=_TEST_SECRET, lifetime_seconds=3600)
    token = asyncio.run(strategy.write_token(user))

    asyncio.run(strategy.read_token(token, manager))
    assert len(_user_cache) == 1

    invalidate_auth_cache(user.id)

    assert len(_user_cache) == 0
