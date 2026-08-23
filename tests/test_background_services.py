"""Lifecycle tests for background session and service composition."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import miramedia.background_services as background_services
import miramedia.database as database


class _SessionContext:
    def __init__(self, db: MagicMock) -> None:
        self._db = db

    async def __aenter__(self) -> MagicMock:
        return self._db

    async def __aexit__(self, *_args: object) -> bool:
        return False


@pytest.fixture
def mock_bg_sessionmaker(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    db = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    def _factory() -> _SessionContext:
        return _SessionContext(db)

    sessionmaker = MagicMock(side_effect=_factory)
    monkeypatch.setattr(database, "SessionLocalBackground", sessionmaker)
    return db


def test_background_session_commits_on_clean_exit(
    mock_bg_sessionmaker: MagicMock,
) -> None:
    async def _run() -> None:
        async with database.background_session():
            pass

        mock_bg_sessionmaker.commit.assert_awaited_once()
        mock_bg_sessionmaker.rollback.assert_not_awaited()

    asyncio.run(_run())


def test_background_session_rolls_back_on_exception(
    mock_bg_sessionmaker: MagicMock,
) -> None:
    async def _run() -> None:
        boom = "boom"
        with pytest.raises(RuntimeError, match=boom):
            async with database.background_session():
                raise RuntimeError(boom)

        mock_bg_sessionmaker.rollback.assert_awaited_once()
        mock_bg_sessionmaker.commit.assert_not_awaited()

    asyncio.run(_run())


@pytest.mark.parametrize(
    ("factory_name", "repo_attr"),
    [
        ("bg_show_service", "show_repository"),
        ("bg_movie_service", "movie_repository"),
        ("bg_torrent_service", "torrent_repository"),
    ],
)
def test_service_factories_share_background_session(
    mock_bg_sessionmaker: MagicMock,
    factory_name: str,
    repo_attr: str,
) -> None:
    factory = getattr(background_services, factory_name)

    async def _run() -> None:
        async with factory() as svc:
            repo = getattr(svc, repo_attr)
            assert repo.db is mock_bg_sessionmaker

        mock_bg_sessionmaker.commit.assert_awaited_once()

    asyncio.run(_run())


def test_bg_subtitle_service_shares_session_across_repos(
    mock_bg_sessionmaker: MagicMock,
) -> None:
    async def _run() -> None:
        async with background_services.bg_subtitle_service() as svc:
            assert svc.subtitle_repository.db is mock_bg_sessionmaker
            assert svc.show_service.show_repository.db is mock_bg_sessionmaker
            assert svc.movie_service.movie_repository.db is mock_bg_sessionmaker

        mock_bg_sessionmaker.commit.assert_awaited_once()

    asyncio.run(_run())


def test_bg_request_service_closes_seerr_client(
    mock_bg_sessionmaker: MagicMock,
) -> None:
    client = MagicMock()
    client.aclose = AsyncMock()
    service = MagicMock()
    repo = MagicMock()

    @asynccontextmanager
    async def _bg_session():
        yield mock_bg_sessionmaker

    with (
        patch.object(database, "background_session", _bg_session),
        patch(
            "miramedia.requests.dependencies.build_seerr_client",
            return_value=client,
        ),
        patch(
            "miramedia.requests.backends.composite.CompositeRequestProvider",
            return_value=MagicMock(),
        ),
        patch(
            "miramedia.requests.backends.native.NativeRequestProvider",
            return_value=MagicMock(),
        ),
        patch(
            "miramedia.requests.service.RequestService",
            return_value=service,
        ),
        patch(
            "miramedia.requests.repository.RequestRepository",
            return_value=repo,
        ),
    ):

        async def _run() -> None:
            async with background_services.bg_request_service() as (svc, request_repo):
                assert svc is service
                assert request_repo is repo

            client.aclose.assert_awaited_once()
            mock_bg_sessionmaker.commit.assert_awaited_once()

        asyncio.run(_run())


def test_bg_request_service_closes_client_after_body_exception(
    mock_bg_sessionmaker: MagicMock,
) -> None:
    client = MagicMock()
    client.aclose = AsyncMock()

    @asynccontextmanager
    async def _bg_session():
        yield mock_bg_sessionmaker

    with (
        patch.object(database, "background_session", _bg_session),
        patch(
            "miramedia.requests.dependencies.build_seerr_client",
            return_value=client,
        ),
        patch(
            "miramedia.requests.backends.composite.CompositeRequestProvider",
            return_value=MagicMock(),
        ),
        patch(
            "miramedia.requests.backends.native.NativeRequestProvider",
            return_value=MagicMock(),
        ),
        patch(
            "miramedia.requests.service.RequestService",
            return_value=MagicMock(),
        ),
        patch(
            "miramedia.requests.repository.RequestRepository",
            return_value=MagicMock(),
        ),
    ):

        async def _run() -> None:
            boom = "boom"
            with pytest.raises(RuntimeError, match=boom):
                async with background_services.bg_request_service():
                    raise RuntimeError(boom)

            client.aclose.assert_awaited_once()
            mock_bg_sessionmaker.rollback.assert_awaited_once()

        asyncio.run(_run())


def test_database_import_does_not_load_domain_service_modules() -> None:
    from pathlib import Path

    repo_root = str(Path(database.__file__).resolve().parents[2])
    script = f"""
import sys
sys.path.insert(0, {repo_root!r})
for mod in list(sys.modules):
    if mod.startswith("miramedia."):
        del sys.modules[mod]
import miramedia.database  # noqa: F401
blocked = (
    "miramedia.shows.service",
    "miramedia.movies.service",
    "miramedia.torrents.service",
    "miramedia.subtitles.service",
    "miramedia.requests.service",
)
loaded = [m for m in blocked if m in sys.modules]
if loaded:
    raise SystemExit(f"unexpected modules loaded: {{loaded}}")
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
