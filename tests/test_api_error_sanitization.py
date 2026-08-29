"""Plan 180 — API responses must not leak raw exception text."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miramedia.shows.schemas import (
    Episode,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.torrents.route_orchestration import safe_bulk_item_error_message
from miramedia.torrents.schemas import ManualMapTargetType, Quality
from tests.fakes.repositories import FakeShowRepository, make_torrent
from tests.fakes.services import (
    build_movie_service,
    build_show_service,
    build_torrent_service,
)

SETTINGS_PREFIX = "/api/v1/system/settings"
TORRENTS_PREFIX = "/api/v1/torrents"
USERS_PREFIX = "/api/v1/users"


@contextmanager
def settings_client(
    *,
    superuser: bool = True,
) -> Generator[TestClient]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.settings.dependencies import get_settings_repository
    from tests.fakes.repositories import FakeSettingsRepository

    fake_repo = FakeSettingsRepository()

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        if not superuser:
            raise HTTPException(status_code=403, detail="Forbidden")
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    def _repo_dep() -> FakeSettingsRepository:
        return fake_repo

    prior_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_settings_repository] = _repo_dep
    try:
        with patch(
            "miramedia.settings.router.refresh_dynamic_schedules",
            new_callable=AsyncMock,
            create=True,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            try:
                yield client
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)


def test_put_settings_validation_failure_hides_raw_exception_detail() -> None:
    secret = "sqlalchemy.engine: /var/secret/path failed"
    with patch(
        "miramedia.settings.router.validate_incoming_settings_update",
        side_effect=RuntimeError(secret),
    ):
        with settings_client() as client:
            response = client.put(
                SETTINGS_PREFIX,
                json={"misc": {"development": True}},
            )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail == "Invalid settings payload"
    assert "sqlalchemy" not in detail.lower()
    assert "/var/" not in detail
    assert "Traceback" not in detail


def test_put_settings_validation_error_returns_fixed_message() -> None:
    with patch(
        "miramedia.settings.router.validate_incoming_settings_update",
        side_effect=ValidationError.from_exception_data(
            "SystemSettingsUpdate",
            [{"type": "missing", "loc": ("misc",), "msg": "Field required"}],
        ),
    ):
        with settings_client() as client:
            response = client.put(
                SETTINGS_PREFIX,
                json={"misc": {"development": True}},
            )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail == "Invalid settings payload"
    assert "Field required" not in detail
    assert "/var/" not in detail
    assert "Traceback" not in detail


def test_put_settings_validation_error_hides_non_path_secret_text() -> None:
    with patch(
        "miramedia.settings.router.validate_incoming_settings_update",
        side_effect=ValidationError.from_exception_data(
            "SystemSettingsUpdate",
            [
                {
                    "type": "missing",
                    "loc": ("misc",),
                    "msg": "database password supersecret",
                }
            ],
        ),
    ):
        with settings_client() as client:
            response = client.put(
                SETTINGS_PREFIX,
                json={"misc": {"development": True}},
            )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail == "Invalid settings payload"
    assert "supersecret" not in detail
    assert "database password" not in detail


@contextmanager
def admin_user_client(
    *,
    create_side_effect: Exception | None = None,
) -> Generator[TestClient]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.main import app

    user_manager = MagicMock()
    user_manager.create = AsyncMock(
        side_effect=create_side_effect or RuntimeError("db driver: /etc/passwd leaked")
    )
    user_manager.forgot_password = AsyncMock()

    @asynccontextmanager
    async def _session_ctx():
        yield MagicMock()

    @asynccontextmanager
    async def _user_db_ctx(_session):
        yield MagicMock()

    @asynccontextmanager
    async def _user_manager_ctx(_user_db):
        yield user_manager

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_superuser] = _superuser
    try:
        with (
            patch(
                "miramedia.auth.users.get_async_session_context",
                _session_ctx,
            ),
            patch("miramedia.auth.users.get_user_db_context", _user_db_ctx),
            patch(
                "miramedia.auth.users.get_user_manager_context",
                _user_manager_ctx,
            ),
        ):
            client = TestClient(app, raise_server_exceptions=False)
            try:
                yield client
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_admin_create_failure_returns_fixed_message() -> None:
    with admin_user_client() as client:
        response = client.post(
            f"{USERS_PREFIX}/create",
            json={
                "email": "new@example.com",
                "password": "long-enough-password",
            },
        )
    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Could not create user"
    assert "/etc/passwd" not in body["detail"]
    assert "db driver" not in body["detail"]


def test_admin_invite_failure_returns_fixed_message() -> None:
    with admin_user_client() as client:
        response = client.post(
            f"{USERS_PREFIX}/invite",
            json={"email": "invite@example.com"},
        )
    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Could not create user"
    assert "/etc/passwd" not in body["detail"]
    assert "db driver" not in body["detail"]


def _episode_show() -> tuple[Show, Episode, Season]:
    show_id = ShowId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    episode = Episode(
        id=EpisodeId(uuid.uuid4()),
        number=EpisodeNumber(1),
        title="Pilot",
    )
    season = Season(
        id=season_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=[episode],
    )
    show = Show(
        id=show_id,
        name="Sanitize Show",
        overview="",
        year=2024,
        external_id="ext-sanitize",
        metadata_provider="native",
        seasons=[season],
    )
    return show, episode, season


@contextmanager
def manual_map_client(
    *,
    tmp_path,
    import_side_effect: Exception,
) -> Generator[tuple[TestClient, str, EpisodeId]]:
    from miramedia.auth.users import current_active_user, current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_repository, get_movie_service
    from miramedia.shows.dependencies import get_show_repository, get_show_service
    from miramedia.torrents.dependencies import (
        get_torrent_by_id,
        get_torrent_service,
    )

    show, episode, _season = _episode_show()
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    show_service, show_repo, torrent_repo = build_show_service(show_repo=show_repo)
    movie_service, movie_repo, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    torrent = make_torrent(title="Sanitize.Show.S01E01")
    torrent_repo.torrents[torrent.id] = torrent

    media_file = tmp_path / "episode.mkv"
    media_file.write_bytes(b"video")

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        user.is_active = True
        user.is_verified = True
        return user

    async def _superuser() -> Any:
        return await _active_user()

    async def _torrent_dep() -> Any:
        return torrent

    show_service.import_episode_from_file = AsyncMock(side_effect=import_side_effect)

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_torrent_by_id] = _torrent_dep
    app.dependency_overrides[get_torrent_service] = lambda: torrent_service
    app.dependency_overrides[get_show_service] = lambda: show_service
    app.dependency_overrides[get_movie_service] = lambda: movie_service
    app.dependency_overrides[get_show_repository] = lambda: show_repo
    app.dependency_overrides[get_movie_repository] = lambda: movie_repo
    try:
        with patch(
            "miramedia.torrents.paths.get_torrent_filepath",
            return_value=tmp_path,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            rel = "episode.mkv"
            try:
                yield client, rel, episode.id
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_manual_map_item_failure_hides_raw_exception_text(tmp_path) -> None:
    secret = "OSError: [Errno 13] Permission denied: '/secret/path'"
    with manual_map_client(
        tmp_path=tmp_path,
        import_side_effect=RuntimeError(secret),
    ) as (client, rel_path, episode_id):
        response = client.post(
            f"{TORRENTS_PREFIX}/{uuid.uuid4()}/map",
            json={
                "items": [
                    {
                        "relative_path": rel_path,
                        "target_type": ManualMapTargetType.episode.value,
                        "episode_id": str(episode_id),
                        "quality_override": Quality.fullhd.value,
                    }
                ]
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 1
    assert len(body["errors"]) == 1
    error = body["errors"][0]
    assert rel_path in error
    assert error.endswith("import failed")
    assert "/secret/path" not in error
    assert "Permission denied" not in error


def test_safe_bulk_item_error_message_allows_control_flow_value_errors() -> None:
    assert (
        safe_bulk_item_error_message(
            ValueError("episode_id required for target_type=episode"),
            fallback="import failed",
        )
        == "episode_id required for target_type=episode"
    )


def test_safe_bulk_item_error_message_hides_unexpected_value_error_secrets() -> None:
    secret = "OSError: [Errno 13] Permission denied: '/secret/path'"
    assert (
        safe_bulk_item_error_message(ValueError(secret), fallback="import failed")
        == "import failed"
    )
    assert "/secret/path" not in safe_bulk_item_error_message(
        ValueError(secret), fallback="import failed"
    )


def test_safe_bulk_item_error_message_hides_malicious_unknown_target_suffix() -> None:
    malicious = "unknown target_type /secret/db/path"
    assert (
        safe_bulk_item_error_message(ValueError(malicious), fallback="import failed")
        == "import failed"
    )
    assert "/secret/" not in safe_bulk_item_error_message(
        ValueError(malicious), fallback="import failed"
    )
