"""Authorization tests for scoped personal API tokens (design 388 Slice A)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from miramedia.auth.api_tokens import generate_token
from miramedia.auth.token_scopes import (
    SCOPE_DOWNLOADS_WRITE,
    SCOPE_LIBRARY_READ,
    attach_api_token_state,
    enforce_api_token_scopes,
    get_current_request,
)
from miramedia.movies.schemas import PublicMovie
from tests.fakes.repositories import make_movie

CREATE_TOKEN_PATH = "/api/v1/users/me/tokens"
MOVIES_PATH = "/api/v1/movies"
TORRENTS_DOWNLOAD_PATH = "/api/v1/torrents/download"
SETTINGS_PATH = "/api/v1/system/settings"
TOKENS_LIST_PATH = "/api/v1/users/me/tokens"
STREAMS_MOVIE_PATH = "/api/v1/streams/movies/00000000-0000-0000-0000-000000000001"


def _make_verified_user(*, superuser: bool = False) -> Any:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = "user@example.com"
    user.is_active = True
    user.is_verified = True
    user.is_superuser = superuser
    user.credentials_changed_at = None
    user.last_login_at = None
    return user


def _patch_read_token(
    monkeypatch: pytest.MonkeyPatch,
    user: Any,
    scopes: list[str],
) -> str:
    plaintext, _, _ = generate_token()

    async def _read_token(_self: Any, _token: str, _manager: Any) -> Any:
        request = get_current_request()
        if request is not None:
            attach_api_token_state(
                request,
                token_id=uuid.uuid4(),
                scopes=scopes,
                preview="abcd",
            )
            await enforce_api_token_scopes(request)
        return user

    monkeypatch.setattr(
        "miramedia.auth.api_tokens.DatabaseTokenStrategy.read_token",
        _read_token,
    )
    return plaintext


@contextmanager
def _api_token_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    scopes: list[str],
    superuser: bool = False,
) -> Generator[tuple[TestClient, str, Any]]:
    from miramedia.database import get_session
    from miramedia.main import app

    user = _make_verified_user(superuser=superuser)
    plaintext = _patch_read_token(monkeypatch, user, scopes)

    async def _stub_session() -> Any:
        yield None

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, plaintext, user
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


@contextmanager
def _session_movies_client() -> Generator[tuple[TestClient, MagicMock]]:
    from miramedia.auth.users import current_active_user
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_service

    user = _make_verified_user()

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        return user

    service = MagicMock()
    page_item = PublicMovie.model_validate(make_movie(name="Listed Movie"))
    service.get_paginated_public_movies = AsyncMock(return_value=([page_item], 1))

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[get_movie_service] = lambda: service
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client, service
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_session_get_movies_skips_scope_check() -> None:
    with _session_movies_client() as (client, _service):
        response = client.get(MOVIES_PATH)
        assert response.status_code == 200


def test_token_library_read_get_movies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.movies.dependencies import get_movie_service

    with _api_token_client(monkeypatch, scopes=[SCOPE_LIBRARY_READ]) as (
        client,
        plaintext,
        _user,
    ):
        service = MagicMock()
        page_item = PublicMovie.model_validate(make_movie(name="Token Movie"))
        service.get_paginated_public_movies = AsyncMock(return_value=([page_item], 1))
        client.app.dependency_overrides[get_movie_service] = lambda: service
        response = client.get(
            MOVIES_PATH,
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 200


def test_token_downloads_only_get_movies_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _api_token_client(monkeypatch, scopes=[SCOPE_DOWNLOADS_WRITE]) as (
        client,
        plaintext,
        _user,
    ):
        response = client.get(
            MOVIES_PATH,
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing required scope"


def test_token_library_read_post_download_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _api_token_client(monkeypatch, scopes=[SCOPE_LIBRARY_READ]) as (
        client,
        plaintext,
        _user,
    ):
        response = client.post(
            TORRENTS_DOWNLOAD_PATH,
            json={
                "indexer_result_id": str(uuid.uuid4()),
                "media_type": "movie",
                "media_id": str(uuid.uuid4()),
            },
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing required scope"


def test_token_downloads_non_superuser_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _api_token_client(
        monkeypatch, scopes=[SCOPE_DOWNLOADS_WRITE], superuser=False
    ) as (client, plaintext, _user):
        response = client.post(
            TORRENTS_DOWNLOAD_PATH,
            json={
                "indexer_result_id": str(uuid.uuid4()),
                "media_type": "movie",
                "media_id": str(uuid.uuid4()),
            },
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 403


def test_superuser_token_downloads_write_post_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from miramedia.indexers.dependencies import get_indexer_service
    from miramedia.movies.dependencies import get_movie_repository, get_movie_service
    from miramedia.shows.dependencies import get_show_repository, get_show_service
    from miramedia.torrents.dependencies import get_torrent_service
    from miramedia.torrents.schemas import ImportProgress, Quality, TorrentStatus

    torrent = MagicMock()
    torrent.id = uuid.uuid4()
    torrent.status = TorrentStatus.downloading
    torrent.progress = 0.0
    torrent.num_peers = 0
    torrent.num_seeds = 0
    torrent.title = "Example"
    torrent.quality = Quality.hd
    torrent.hash = "abc"
    torrent.usenet = False

    torrent_service = MagicMock()
    torrent_service.download_and_link = AsyncMock(return_value=torrent)
    torrent_service.compute_import_progress = AsyncMock(return_value=ImportProgress())

    indexer_service = MagicMock()
    indexer_service.get_result = AsyncMock(return_value=MagicMock())

    with _api_token_client(
        monkeypatch, scopes=[SCOPE_DOWNLOADS_WRITE], superuser=True
    ) as (client, plaintext, _user):
        client.app.dependency_overrides[get_torrent_service] = lambda: torrent_service
        client.app.dependency_overrides[get_indexer_service] = lambda: indexer_service
        client.app.dependency_overrides[get_show_repository] = lambda: MagicMock()
        client.app.dependency_overrides[get_movie_repository] = lambda: MagicMock()
        client.app.dependency_overrides[get_show_service] = lambda: MagicMock()
        client.app.dependency_overrides[get_movie_service] = lambda: MagicMock()
        response = client.post(
            TORRENTS_DOWNLOAD_PATH,
            json={
                "indexer_result_id": str(uuid.uuid4()),
                "media_type": "movie",
                "media_id": str(uuid.uuid4()),
            },
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 200


def test_token_unscoped_settings_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _api_token_client(
        monkeypatch,
        scopes=[SCOPE_LIBRARY_READ, SCOPE_DOWNLOADS_WRITE],
    ) as (client, plaintext, _user):
        response = client.get(
            SETTINGS_PATH,
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "This route does not accept API tokens"


def test_token_list_tokens_session_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _api_token_client(monkeypatch, scopes=[SCOPE_LIBRARY_READ]) as (
        client,
        plaintext,
        _user,
    ):
        response = client.get(
            TOKENS_LIST_PATH,
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "This route does not accept API tokens"


def test_token_streams_session_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _api_token_client(monkeypatch, scopes=[SCOPE_LIBRARY_READ]) as (
        client,
        plaintext,
        _user,
    ):
        response = client.get(
            STREAMS_MOVIE_PATH,
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "This route does not accept API tokens"


def test_anonymous_get_movies_unauthorized() -> None:
    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session() -> Any:
        yield None

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get(MOVIES_PATH)
        assert response.status_code == 401
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_unknown_scope_on_token_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _api_token_client(monkeypatch, scopes=["legacy:full"]) as (
        client,
        plaintext,
        _user,
    ):
        response = client.get(
            MOVIES_PATH,
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 403
        assert response.json()["detail"] == "Missing required scope"


def test_legacy_token_empty_scopes_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _api_token_client(monkeypatch, scopes=[]) as (client, plaintext, _user):
        response = client.get(
            MOVIES_PATH,
            headers={"Authorization": f"Bearer {plaintext}"},
        )
        assert response.status_code == 403


def test_api_token_create_rejects_unknown_scope(
    credential_client: tuple[TestClient, uuid.UUID, Any],
) -> None:
    client, _user_id, _session = credential_client
    response = client.post(
        CREATE_TOKEN_PATH,
        json={"name": "bad", "scopes": ["not-a-real-scope"]},
    )
    assert response.status_code == 422


def test_api_token_scopes_migration_metadata() -> None:
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/o3p4q5r6s7t8_add_api_token_scopes.py"
    )
    text = migration_path.read_text()
    assert 'revision: str = "o3p4q5r6s7t8"' in text
    assert 'down_revision: str | None = "n2b3c4d5e6f7"' in text


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
        if not getattr(obj, "scopes", None):
            obj.scopes = []


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


def test_api_token_create_stores_scopes(
    credential_client: tuple[TestClient, uuid.UUID, FakeCreateTokenSession],
) -> None:
    client, user_id, session = credential_client
    response = client.post(
        CREATE_TOKEN_PATH,
        json={
            "name": "scoped",
            "scopes": [SCOPE_LIBRARY_READ, SCOPE_DOWNLOADS_WRITE],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["scopes"] == [SCOPE_LIBRARY_READ, SCOPE_DOWNLOADS_WRITE]
    assert len(session.added) == 1
    assert session.added[0].scopes == [SCOPE_LIBRARY_READ, SCOPE_DOWNLOADS_WRITE]
    assert session.added[0].user_id == user_id
