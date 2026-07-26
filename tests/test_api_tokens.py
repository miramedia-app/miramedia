"""Characterization tests for personal API-token auth (DatabaseTokenStrategy)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Self

import pytest
from sqlalchemy.sql.dml import Update

from miramedia.auth.api_tokens import (
    TOKEN_PREFIX,
    DatabaseTokenStrategy,
    generate_token,
    hash_token,
)


@dataclass
class _RowResult:
    row: Any | None

    def first(self) -> Any | None:
        return self.row


@dataclass
class FakeAsyncSession:
    """Minimal async session for DatabaseTokenStrategy.read_token."""

    row: Any | None = None
    user: Any | None = None
    executes: list[Any] = field(default_factory=list)
    expunge_calls: list[Any] = field(default_factory=list)
    committed: bool = False

    async def execute(self, stmt: Any) -> _RowResult:
        self.executes.append(stmt)
        # SELECT returns the configured row; UPDATE returns an empty result.
        if isinstance(stmt, Update):
            return _RowResult(None)
        return _RowResult(self.row)

    async def commit(self) -> None:
        self.committed = True

    async def get(self, _model: Any, _pk: Any) -> Any | None:
        return self.user

    def expunge(self, obj: Any) -> None:
        self.expunge_calls.append(obj)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class FakeSessionLocal:
    """Callable factory that also supports ``async with SessionLocal() as db``."""

    def __init__(self, session: FakeAsyncSession) -> None:
        self._session = session
        self.calls = 0

    def __call__(self) -> FakeAsyncSession:
        self.calls += 1
        return self._session


class RaisingSessionLocal:
    def __call__(self) -> None:
        msg = "SessionLocal must not be called"
        raise AssertionError(msg)


def test_generate_token_shape() -> None:
    plaintext, token_hash, preview = generate_token()
    assert plaintext.startswith(TOKEN_PREFIX)
    assert token_hash == hash_token(plaintext)
    assert preview == plaintext[-4:]


def test_read_token_rejects_non_prefixed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miramedia.database.SessionLocal",
        RaisingSessionLocal(),
    )
    strategy = DatabaseTokenStrategy()
    assert (
        asyncio.run(strategy.read_token("Bearer xyz", user_manager=None)) is None  # type: ignore[arg-type]
    )
    assert asyncio.run(strategy.read_token(None, user_manager=None)) is None  # type: ignore[arg-type]


def test_read_token_uninitialized_sessionlocal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("miramedia.database.SessionLocal", None)
    strategy = DatabaseTokenStrategy()
    plaintext, _, _ = generate_token()
    assert (
        asyncio.run(strategy.read_token(plaintext, user_manager=None)) is None  # type: ignore[arg-type]
    )


def test_read_token_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    token_id = uuid.uuid4()
    row = SimpleNamespace(
        id=token_id, user_id=user_id, expires_at=None, last_used_at=None
    )
    user = SimpleNamespace(id=user_id, is_active=True)
    session = FakeAsyncSession(row=row, user=user)
    factory = FakeSessionLocal(session)
    monkeypatch.setattr("miramedia.database.SessionLocal", factory)

    plaintext, _, _ = generate_token()
    strategy = DatabaseTokenStrategy()
    result = asyncio.run(strategy.read_token(plaintext, user_manager=None))  # type: ignore[arg-type]

    assert result is user
    assert session.committed is True
    assert session.expunge_calls == [user]
    assert any(isinstance(stmt, Update) for stmt in session.executes)
    assert factory.calls == 1


def test_read_token_skips_last_used_update_when_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        expires_at=None,
        last_used_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    user = SimpleNamespace(id=user_id, is_active=True)
    session = FakeAsyncSession(row=row, user=user)
    monkeypatch.setattr("miramedia.database.SessionLocal", FakeSessionLocal(session))

    plaintext, _, _ = generate_token()
    strategy = DatabaseTokenStrategy()
    result = asyncio.run(strategy.read_token(plaintext, user_manager=None))  # type: ignore[arg-type]

    assert result is user
    assert session.committed is False
    assert not any(isinstance(stmt, Update) for stmt in session.executes)
    assert session.expunge_calls == [user]


def test_read_token_updates_last_used_when_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        expires_at=None,
        last_used_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    user = SimpleNamespace(id=user_id, is_active=True)
    session = FakeAsyncSession(row=row, user=user)
    monkeypatch.setattr("miramedia.database.SessionLocal", FakeSessionLocal(session))

    plaintext, _, _ = generate_token()
    strategy = DatabaseTokenStrategy()
    result = asyncio.run(strategy.read_token(plaintext, user_manager=None))  # type: ignore[arg-type]

    assert result is user
    assert session.committed is True
    assert any(isinstance(stmt, Update) for stmt in session.executes)
    assert session.expunge_calls == [user]


def test_read_token_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        last_used_at=None,
    )
    session = FakeAsyncSession(
        row=row, user=SimpleNamespace(id=user_id, is_active=True)
    )
    monkeypatch.setattr("miramedia.database.SessionLocal", FakeSessionLocal(session))

    plaintext, _, _ = generate_token()
    strategy = DatabaseTokenStrategy()
    assert (
        asyncio.run(strategy.read_token(plaintext, user_manager=None)) is None  # type: ignore[arg-type]
    )
    assert not any(isinstance(stmt, Update) for stmt in session.executes)


def test_read_token_inactive_user(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        expires_at=None,
        last_used_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    user = SimpleNamespace(id=user_id, is_active=False)
    session = FakeAsyncSession(row=row, user=user)
    monkeypatch.setattr("miramedia.database.SessionLocal", FakeSessionLocal(session))

    plaintext, _, _ = generate_token()
    strategy = DatabaseTokenStrategy()
    assert (
        asyncio.run(strategy.read_token(plaintext, user_manager=None)) is None  # type: ignore[arg-type]
    )
    assert session.expunge_calls == []


def test_write_token_raises() -> None:
    strategy = DatabaseTokenStrategy()
    with pytest.raises(NotImplementedError):
        asyncio.run(strategy.write_token(SimpleNamespace()))  # type: ignore[arg-type]


def test_destroy_token_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("miramedia.database.SessionLocal", RaisingSessionLocal())
    strategy = DatabaseTokenStrategy()
    result = asyncio.run(strategy.destroy_token("mm_test", SimpleNamespace()))  # type: ignore[arg-type]
    assert result is None
