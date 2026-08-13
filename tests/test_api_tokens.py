"""Characterization tests for personal API-token auth (DatabaseTokenStrategy)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.sql.dml import Update

from miramedia.auth.api_tokens import (
    TOKEN_PREFIX,
    DatabaseTokenStrategy,
    generate_token,
    hash_token,
)
from miramedia.auth.router import ApiTokenCreate

CREATE_TOKEN_PATH = "/api/v1/users/me/tokens"


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


# --- ApiTokenCreate schema boundaries ------------------------------------------------


def test_api_token_create_trims_name() -> None:
    model = ApiTokenCreate(name="  my token  ")
    assert model.name == "my token"


def test_api_token_create_rejects_whitespace_only_name() -> None:
    with pytest.raises(ValidationError):
        ApiTokenCreate(name="   ")


def test_api_token_create_rejects_name_too_long_after_trim() -> None:
    with pytest.raises(ValidationError):
        ApiTokenCreate(name=" " + "x" * 121)


def test_api_token_create_rejects_naive_expires_at() -> None:
    naive = datetime.fromisoformat("2026-12-31T00:00:00")
    with pytest.raises(ValidationError):
        ApiTokenCreate(name="test", expires_at=naive)


def test_api_token_create_accepts_aware_future_expires_at() -> None:
    expires_at = datetime.now(UTC) + timedelta(days=1)
    model = ApiTokenCreate(name="test", expires_at=expires_at)
    assert model.expires_at == expires_at


def test_api_token_create_accepts_offset_aware_expires_at() -> None:
    offset = timezone(timedelta(hours=-5))
    expires_at = datetime(2026, 12, 31, tzinfo=offset)
    model = ApiTokenCreate(name="test", expires_at=expires_at)
    assert model.expires_at == expires_at


# --- create_my_token route boundaries ----------------------------------------------


@dataclass
class FakeCreateTokenSession:
    added: list[Any] = field(default_factory=list)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def refresh(self, obj: Any) -> None:
        obj.id = uuid.uuid4()
        obj.created_at = datetime.now(UTC)
        obj.last_used_at = None


@pytest.fixture
def api_token_client(
    override_dependency: Callable[[Callable, object], None],
) -> tuple[TestClient, uuid.UUID, FakeCreateTokenSession]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app

    user_id = uuid.uuid4()
    session = FakeCreateTokenSession()

    async def _stub_session() -> Any:
        yield session

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = user_id
        return user

    override_dependency(get_session, _stub_session)
    override_dependency(current_active_user, _active_user)
    client = TestClient(app, raise_server_exceptions=False)
    return client, user_id, session


def test_create_token_rejects_whitespace_only_name(
    api_token_client: tuple[TestClient, uuid.UUID, FakeCreateTokenSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _user_id, session = api_token_client
    called = False

    def _fail_generate() -> tuple[str, str, str]:
        nonlocal called
        called = True
        return generate_token()

    monkeypatch.setattr("miramedia.auth.router.generate_token", _fail_generate)
    response = client.post(CREATE_TOKEN_PATH, json={"name": "   "})
    assert response.status_code == 422
    assert called is False
    assert session.added == []


def test_create_token_rejects_naive_expires_at(
    api_token_client: tuple[TestClient, uuid.UUID, FakeCreateTokenSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _user_id, session = api_token_client
    called = False

    def _fail_generate() -> tuple[str, str, str]:
        nonlocal called
        called = True
        return generate_token()

    monkeypatch.setattr("miramedia.auth.router.generate_token", _fail_generate)
    response = client.post(
        CREATE_TOKEN_PATH,
        json={"name": "test", "expires_at": "2026-12-31T00:00:00"},
    )
    assert response.status_code == 422
    assert called is False
    assert session.added == []


def test_create_token_rejects_past_aware_expires_at(
    api_token_client: tuple[TestClient, uuid.UUID, FakeCreateTokenSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _user_id, session = api_token_client
    called = False

    def _fail_generate() -> tuple[str, str, str]:
        nonlocal called
        called = True
        return generate_token()

    monkeypatch.setattr("miramedia.auth.router.generate_token", _fail_generate)
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    response = client.post(
        CREATE_TOKEN_PATH,
        json={"name": "test", "expires_at": past},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "expires_at must be in the future"
    assert called is False
    assert session.added == []


def test_create_token_accepts_trimmed_name_and_future_aware_expiry(
    api_token_client: tuple[TestClient, uuid.UUID, FakeCreateTokenSession],
) -> None:
    client, user_id, session = api_token_client
    future = datetime.now(UTC) + timedelta(days=1)
    response = client.post(
        CREATE_TOKEN_PATH,
        json={
            "name": "  nightly backup  ",
            "expires_at": future.isoformat(),
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "nightly backup"
    assert body["token"].startswith(TOKEN_PREFIX)
    assert len(session.added) == 1
    row = session.added[0]
    assert row.user_id == user_id
    assert row.name == "nightly backup"
    assert row.expires_at == future
