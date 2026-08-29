"""DB-free API tests for the superuser-only diagnostics routes."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from miramedia.diagnostics.router import router as diagnostics_router
from miramedia.file_status import ImportOutcome
from miramedia.shows.schemas import EpisodeFile
from miramedia.torrents.integrity import (
    INTEGRITY_MISMATCH_DEFAULT_LIMIT,
    INTEGRITY_MISMATCH_MAX_LIMIT,
)
from miramedia.torrents.schemas import Quality
from tests.fakes.db import FakeDb
from tests.fakes.repositories import FakeMovieRepository, FakeShowRepository, make_show
from tests.test_diagnostics_collectors import (
    _install_fake_pools,
    _PopulatedFakeDb,
)
from tests.test_storage_health_service import _service

os.environ.setdefault("MIRAMEDIA_LOG_FILE", "/dev/null")

PREFIX = "/api/v1/diagnostics"


@contextmanager
def diagnostics_client(
    *,
    superuser: bool = True,
    anonymous: bool = False,
    show_repo: FakeShowRepository | None = None,
    movie_repo: FakeMovieRepository | None = None,
    db: Any | None = None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient, FakeShowRepository, FakeMovieRepository]]:
    from miramedia.auth.users import current_active_user, current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_repository
    from miramedia.shows.dependencies import get_show_repository
    from miramedia.storage.dependencies import get_storage_health_service

    s_repo = show_repo or FakeShowRepository()
    m_repo = movie_repo or FakeMovieRepository()
    svc = _service(s_repo, m_repo, tmp_path)

    session = FakeDb() if db is None else db

    async def _stub_session() -> Any:
        yield session

    async def _active_user() -> Any:
        if anonymous:
            raise HTTPException(status_code=401, detail="Unauthorized")
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = superuser
        return user

    async def _superuser() -> Any:
        if anonymous:
            raise HTTPException(status_code=401, detail="Unauthorized")
        if not superuser:
            raise HTTPException(status_code=403, detail="Forbidden")
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_episode_paths_async",
        AsyncMock(return_value={}),
    )
    monkeypatch.setattr(
        "miramedia.storage.service.batch_resolve_movie_paths_async",
        AsyncMock(return_value={}),
    )

    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_show_repository] = lambda: s_repo
    app.dependency_overrides[get_movie_repository] = lambda: m_repo
    app.dependency_overrides[get_storage_health_service] = lambda: svc
    try:
        client = TestClient(app, raise_server_exceptions=False)
        yield client, s_repo, m_repo
    finally:
        app.dependency_overrides.clear()


def test_non_superuser_storage_forbidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with diagnostics_client(
        superuser=False, tmp_path=tmp_path, monkeypatch=monkeypatch
    ) as (client, _s, _m):
        r = client.get(f"{PREFIX}/storage")
    assert r.status_code == 403


def test_anonymous_storage_unauthorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with diagnostics_client(
        anonymous=True, tmp_path=tmp_path, monkeypatch=monkeypatch
    ) as (client, _s, _m):
        r = client.get(f"{PREFIX}/storage")
    assert r.status_code == 401


def test_superuser_storage_summary_and_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    show_repo = FakeShowRepository()
    show = make_show(name="Severance")
    show_repo.add_show(show)
    episode = show.seasons[0].episodes[0]
    fid = uuid.uuid4()
    show_repo.episode_files[fid] = EpisodeFile(
        id=fid,
        episode_id=episode.id,
        quality=Quality.fullhd,
        torrent_id=uuid.uuid4(),
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="abc",
    )
    with diagnostics_client(
        show_repo=show_repo, tmp_path=tmp_path, monkeypatch=monkeypatch
    ) as (client, _s, _m):
        summary = client.get(f"{PREFIX}/storage")
        listing = client.get(f"{PREFIX}/storage/files")
        detail = client.get(f"{PREFIX}/storage/files/show/{fid}")
    assert summary.status_code == 200
    body = summary.json()
    assert body["counts"]["corrupt"] == 1
    assert body["counts"]["missing"] is None
    assert isinstance(body["volumes"], list)
    assert len(body["volumes"]) >= 2
    assert listing.status_code == 200
    listed = listing.json()
    assert listed["limit"] == INTEGRITY_MISMATCH_DEFAULT_LIMIT
    assert listed["items"][0]["file_id"] == str(fid)
    assert listed["items"][0]["state"] in {"corrupt", "missing", "inaccessible"}
    assert detail.status_code == 200


def test_limit_over_max_is_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with diagnostics_client(tmp_path=tmp_path, monkeypatch=monkeypatch) as (
        client,
        _s,
        _m,
    ):
        r = client.get(
            f"{PREFIX}/storage/files",
            params={"limit": INTEGRITY_MISMATCH_MAX_LIMIT + 1},
        )
    assert r.status_code == 422


def test_missing_file_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with diagnostics_client(tmp_path=tmp_path, monkeypatch=monkeypatch) as (
        client,
        _s,
        _m,
    ):
        r = client.get(f"{PREFIX}/storage/files/movie/{uuid.uuid4()}")
    assert r.status_code == 404


def test_database_snapshot_is_superuser_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with diagnostics_client(
        superuser=False, tmp_path=tmp_path, monkeypatch=monkeypatch
    ) as (client, _s, _m):
        r = client.get(f"{PREFIX}/database")
    assert r.status_code == 403


def test_superuser_database_omits_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with diagnostics_client(tmp_path=tmp_path, monkeypatch=monkeypatch) as (
        client,
        _s,
        _m,
    ):
        r = client.get(f"{PREFIX}/database")
    assert r.status_code == 200
    body = r.json()
    assert "password" not in body
    assert "host" in body
    assert "name" in body
    assert "user" in body
    dumped = str(body).lower()
    assert "password" not in dumped


def test_superuser_database_returns_populated_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_pools(monkeypatch)
    with diagnostics_client(
        db=_PopulatedFakeDb(), tmp_path=tmp_path, monkeypatch=monkeypatch
    ) as (client, _s, _m):
        r = client.get(f"{PREFIX}/database")
    assert r.status_code == 200
    body = r.json()
    assert body["server_version"] == "17.4 (Debian)"
    assert body["size_bytes"] == 4096
    assert body["max_connections"] == 100
    assert body["started_at"] == "2026-01-15T12:00:00Z"
    assert body["connections"] == [
        {"state": "active", "count": 3},
        {"state": "idle", "count": 7},
    ]
    assert body["largest_tables"] == [
        {
            "name": "episode_file",
            "total_bytes": 1_048_576,
            "table_bytes": 786_432,
            "index_bytes": 262_144,
            "estimated_rows": 42,
        }
    ]
    pool_names = {pool["name"] for pool in body["pools"]}
    assert pool_names == {"request", "background"}
    request = next(pool for pool in body["pools"] if pool["name"] == "request")
    assert request["size"] == 5
    assert request["checked_out"] == 1
    assert request["overflow"] == -1
    assert "Pool size: 5" in request["status"]
    assert "password" not in body
    dumped = str(body).lower()
    assert "password" not in dumped
    assert "super-secret" not in dumped


def test_superuser_scheduler_lists_catalog_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with diagnostics_client(tmp_path=tmp_path, monkeypatch=monkeypatch) as (
        client,
        _s,
        _m,
    ):
        r = client.get(f"{PREFIX}/scheduler")
    assert r.status_code == 200
    body = r.json()
    names = {task["task_name"] for task in body["tasks"]}
    assert "miramedia.scheduler:verify_imported_files_task" in names
    assert "miramedia.scheduler:detect_finished_downloads_task" in names
    assert all("cron" in task for task in body["tasks"])
    assert body["schedules_loaded"] is False


def test_diagnostics_router_has_no_mutations() -> None:
    for route in diagnostics_router.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        assert "POST" not in methods
        assert "PATCH" not in methods
        assert "DELETE" not in methods
        assert "PUT" not in methods
