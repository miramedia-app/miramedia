"""Auth route tests for session-only credential management."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from miramedia.auth.api_tokens import TOKEN_PREFIX, generate_token
from miramedia.auth.token_scopes import (
    SCOPE_DOWNLOADS_WRITE,
    attach_api_token_state,
    enforce_api_token_scopes,
    get_current_request,
)

CREATE_TOKEN_PATH = "/api/v1/users/me/tokens"
USERS_ME_PATH = "/api/v1/users/me"


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


def _make_verified_user() -> Any:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.is_active = True
    user.is_verified = True
    user.is_superuser = False
    user.credentials_changed_at = None
    user.last_login_at = None
    return user


@pytest.fixture
def credential_client(
    override_dependency: Callable[[Callable, object], None],
) -> tuple[TestClient, uuid.UUID, FakeCreateTokenSession]:
    from miramedia.auth.users import current_interactive_user
    from miramedia.database import get_session
    from miramedia.main import app

    user = _make_verified_user()
    session = FakeCreateTokenSession()

    async def _stub_session() -> Any:
        yield session

    async def _interactive_user() -> Any:
        return user

    override_dependency(get_session, _stub_session)
    override_dependency(current_interactive_user, _interactive_user)
    client = TestClient(app, raise_server_exceptions=False)
    return client, user.id, session


def test_create_token_rejects_unauthenticated() -> None:
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield FakeCreateTokenSession()

    app.dependency_overrides[get_session] = _stub_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(CREATE_TOKEN_PATH, json={"name": "automation"})
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_create_token_rejects_api_token_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.database import get_session
    from miramedia.main import app

    user = _make_verified_user()
    session = FakeCreateTokenSession()
    plaintext, _, _ = generate_token()

    async def _stub_session() -> Any:
        yield session

    async def _read_token(
        _self: Any,
        _token: str,
        _manager: Any,
    ) -> Any:
        return user

    monkeypatch.setattr(
        "miramedia.auth.api_tokens.DatabaseTokenStrategy.read_token",
        _read_token,
    )
    app.dependency_overrides[get_session] = _stub_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            CREATE_TOKEN_PATH,
            json={"name": "replacement"},
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 401
        assert session.added == []
    finally:
        app.dependency_overrides.clear()


def test_create_token_accepts_interactive_session(
    credential_client: tuple[TestClient, uuid.UUID, FakeCreateTokenSession],
) -> None:
    client, user_id, session = credential_client
    response = client.post(CREATE_TOKEN_PATH, json={"name": "nightly backup"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "nightly backup"
    assert body["token"].startswith(TOKEN_PREFIX)
    assert len(session.added) == 1
    assert session.added[0].user_id == user_id


def test_create_token_accepts_jwt_bearer() -> None:
    from miramedia.auth.users import UserManager, get_jwt_strategy, get_user_manager
    from miramedia.database import get_session
    from miramedia.main import app

    user = _make_verified_user()
    session = FakeCreateTokenSession()

    async def _stub_session() -> Any:
        yield session

    manager = MagicMock(spec=UserManager)
    manager.parse_id = lambda user_id: uuid.UUID(str(user_id))
    manager.get = AsyncMock(return_value=user)

    async def _user_manager() -> Any:
        yield manager

    async def _issue_token() -> str:
        strategy = get_jwt_strategy()
        return await strategy.write_token(user)

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_user_manager] = _user_manager
    token = asyncio.run(_issue_token())
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            CREATE_TOKEN_PATH,
            json={"name": "jwt session"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        assert response.json()["token"].startswith(TOKEN_PREFIX)
        assert len(session.added) == 1
    finally:
        app.dependency_overrides.clear()


def test_users_me_rejects_api_token_without_library_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.database import get_session
    from miramedia.main import app

    user = _make_verified_user()
    plaintext, _, _ = generate_token()

    async def _stub_session() -> Any:
        yield None

    async def _read_token(
        _self: Any,
        _token: str,
        _manager: Any,
    ) -> Any:
        request = get_current_request()
        if request is not None:
            attach_api_token_state(
                request,
                token_id=uuid.uuid4(),
                scopes=[SCOPE_DOWNLOADS_WRITE],
                preview="abcd",
            )
            await enforce_api_token_scopes(request)
        return user

    monkeypatch.setattr(
        "miramedia.auth.api_tokens.DatabaseTokenStrategy.read_token",
        _read_token,
    )
    app.dependency_overrides[get_session] = _stub_session
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get(
            USERS_ME_PATH,
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "This route does not accept API tokens"
    finally:
        app.dependency_overrides.clear()
