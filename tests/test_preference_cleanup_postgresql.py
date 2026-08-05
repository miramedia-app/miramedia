"""PostgreSQL characterization tests for persisted show/movie preference cleanup."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from miramedia.config import MiraMediaConfig
from miramedia.indexers.config import CodecOption, IndexerConfig, QualityOption
from miramedia.movies.cleanup import cleanup_stale_movie_preferences
from miramedia.movies.models import Movie
from miramedia.shows.cleanup import cleanup_stale_show_preferences
from miramedia.shows.models import Show
from tests.integration._db_url import (
    alembic_sync_url,
    assert_safe_integration_database,
    integration_database_url,
)
from tests.integration.conftest import _TrackedSessions, _truncate_application_tables
from tests.integration.db_ready import (
    DatabaseReadyTimeoutError,
    wait_for_database_ready,
)

pytestmark = pytest.mark.postgresql

_alembic_ready = False
_DISABLED_QUALITY = "4K"
_DISABLED_CODEC = "AV1"


def _cleanup_config() -> MiraMediaConfig:
    config = MiraMediaConfig.load_isolated()
    config.indexers = IndexerConfig(
        quality_options=[
            QualityOption(name="1080p", keywords=["1080p"], enabled=True),
            QualityOption(name="720p", keywords=["720p"], enabled=True),
            QualityOption(name=_DISABLED_QUALITY, keywords=["4k"], enabled=False),
        ],
        codec_options=[
            CodecOption(name="x265", keywords=["x265"], enabled=True),
            CodecOption(name="x264", keywords=["x264"], enabled=True),
            CodecOption(name=_DISABLED_CODEC, keywords=["av1"], enabled=False),
        ],
    )
    return config


@dataclass(frozen=True)
class PreferenceCase:
    id: str
    initial_quality: list[str] | None
    initial_codec: list[str] | None
    expected_quality: list[str] | None
    expected_codec: list[str] | None
    should_commit: bool


_PREFERENCE_CASES = [
    PreferenceCase(
        id="unchanged_enabled",
        initial_quality=["1080p", "720p"],
        initial_codec=["x265"],
        expected_quality=["1080p", "720p"],
        expected_codec=["x265"],
        should_commit=False,
    ),
    PreferenceCase(
        id="mixed_filtered",
        initial_quality=["1080p", _DISABLED_QUALITY],
        initial_codec=["x265", "x264"],
        expected_quality=["1080p"],
        expected_codec=["x265", "x264"],
        should_commit=True,
    ),
    PreferenceCase(
        id="nonempty_to_null",
        initial_quality=[_DISABLED_QUALITY],
        initial_codec=[_DISABLED_CODEC],
        expected_quality=None,
        expected_codec=None,
        should_commit=True,
    ),
    PreferenceCase(
        id="existing_null",
        initial_quality=None,
        initial_codec=None,
        expected_quality=None,
        expected_codec=None,
        should_commit=False,
    ),
    PreferenceCase(
        id="explicit_empty",
        initial_quality=[],
        initial_codec=[],
        expected_quality=[],
        expected_codec=[],
        should_commit=False,
    ),
    PreferenceCase(
        id="quality_only",
        initial_quality=["1080p", _DISABLED_QUALITY],
        initial_codec=None,
        expected_quality=["1080p"],
        expected_codec=None,
        should_commit=True,
    ),
    PreferenceCase(
        id="codec_only",
        initial_quality=None,
        initial_codec=["x264", _DISABLED_CODEC],
        expected_quality=None,
        expected_codec=["x264"],
        should_commit=True,
    ),
    PreferenceCase(
        id="both_fields",
        initial_quality=[_DISABLED_QUALITY],
        initial_codec=[_DISABLED_CODEC],
        expected_quality=None,
        expected_codec=None,
        should_commit=True,
    ),
]


async def _probe_database_connection(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


async def _assert_database_ready(url: str) -> None:
    try:
        await wait_for_database_ready(lambda: _probe_database_connection(url))
    except DatabaseReadyTimeoutError as exc:
        detail = (
            "PostgreSQL connection check failed — set MIRAMEDIA_TEST_DATABASE_URL "
            "to a reachable disposable database"
        )
        if exc.last_error is not None:
            pytest.fail(f"{detail}: {exc.last_error}")
        pytest.fail(detail)


def _run_alembic_upgrade(sync_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": sync_url}
    proc = subprocess.run(
        ["uv", "run", "--python", "3.13", "alembic", "upgrade", "head"],  # noqa: S607
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        msg = (
            "alembic upgrade head failed\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        pytest.fail(msg)


@pytest.fixture(scope="module")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def run_async(event_loop: asyncio.AbstractEventLoop) -> Callable:
    def _run(coro: Any) -> Any:
        return event_loop.run_until_complete(coro)

    return _run


@pytest.fixture(scope="module")
def integration_db_url() -> str:
    url = integration_database_url()
    assert_safe_integration_database(url)
    return url


@pytest.fixture(scope="module")
def integration_engine(
    integration_db_url: str, run_async: Callable
) -> Iterator[AsyncEngine]:
    global _alembic_ready
    run_async(_assert_database_ready(integration_db_url))
    if not _alembic_ready:
        _run_alembic_upgrade(alembic_sync_url(integration_db_url))
        _alembic_ready = True
    engine = create_async_engine(integration_db_url, poolclass=NullPool)
    yield engine
    run_async(engine.dispose())


@pytest.fixture(scope="module")
def session_factory(
    integration_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        integration_engine,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


@pytest.fixture(autouse=True)
def _no_queue_rebuild(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "miramedia.imports.queue_hooks.schedule_import_queue_rebuild",
        lambda: None,
    )


@pytest.fixture(autouse=True)
def clean_database(
    integration_engine: AsyncEngine, run_async: Callable
) -> Iterator[None]:
    run_async(_truncate_application_tables(integration_engine))
    yield
    run_async(_truncate_application_tables(integration_engine))


@pytest.fixture
def db(
    session_factory: async_sessionmaker[AsyncSession], run_async: Callable
) -> Iterator[AsyncSession]:
    session = session_factory()
    yield session
    run_async(session.close())


@pytest.fixture
def make_session(
    session_factory: async_sessionmaker[AsyncSession], run_async: Callable
) -> Iterator[Callable[[], AsyncSession]]:
    tracker = _TrackedSessions(session_factory)
    yield tracker.open
    run_async(tracker.close_all())


async def _insert_show(
    db: AsyncSession,
    *,
    name: str,
    preferred_quality: list[str] | None,
    preferred_codec: list[str] | None,
) -> Show:
    show = Show(
        id=uuid.uuid4(),
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        metadata_provider="native",
        name=name,
        overview="",
        year=2026,
        preferred_quality=preferred_quality,
        preferred_codec=preferred_codec,
    )
    db.add(show)
    await db.commit()
    return show


async def _insert_movie(
    db: AsyncSession,
    *,
    name: str,
    preferred_quality: list[str] | None,
    preferred_codec: list[str] | None,
) -> Movie:
    movie = Movie(
        id=uuid.uuid4(),
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        metadata_provider="native",
        name=name,
        overview="",
        year=2026,
        preferred_quality=preferred_quality,
        preferred_codec=preferred_codec,
    )
    db.add(movie)
    await db.commit()
    return movie


async def _load_show_preferences(
    make_session: Callable[[], AsyncSession], show_id: uuid.UUID
) -> tuple[list[str] | None, list[str] | None]:
    session = make_session()
    show = await session.get(Show, show_id)
    assert show is not None
    return show.preferred_quality, show.preferred_codec


async def _load_movie_preferences(
    make_session: Callable[[], AsyncSession], movie_id: uuid.UUID
) -> tuple[list[str] | None, list[str] | None]:
    session = make_session()
    movie = await session.get(Movie, movie_id)
    assert movie is not None
    return movie.preferred_quality, movie.preferred_codec


async def _run_cleanup_tracked(
    db: AsyncSession,
    config: MiraMediaConfig,
    cleanup_fn: Any,
) -> AsyncMock:
    commit_spy = AsyncMock(wraps=db.commit)
    db.commit = commit_spy
    await cleanup_fn(db, config)
    return commit_spy


@pytest.mark.parametrize(
    "case", _PREFERENCE_CASES, ids=[c.id for c in _PREFERENCE_CASES]
)
def test_cleanup_stale_show_preferences_persistence(
    db: AsyncSession,
    make_session: Callable[[], AsyncSession],
    run_async: Callable,
    case: PreferenceCase,
) -> None:
    async def _exercise() -> None:
        config = _cleanup_config()
        show = await _insert_show(
            db,
            name=f"show-{case.id}",
            preferred_quality=case.initial_quality,
            preferred_codec=case.initial_codec,
        )
        commit_spy = await _run_cleanup_tracked(
            db, config, cleanup_stale_show_preferences
        )
        if case.should_commit:
            commit_spy.assert_awaited_once()
        else:
            commit_spy.assert_not_awaited()
        quality, codec = await _load_show_preferences(make_session, show.id)
        assert quality == case.expected_quality
        assert codec == case.expected_codec

    run_async(_exercise())


@pytest.mark.parametrize(
    "case", _PREFERENCE_CASES, ids=[c.id for c in _PREFERENCE_CASES]
)
def test_cleanup_stale_movie_preferences_persistence(
    db: AsyncSession,
    make_session: Callable[[], AsyncSession],
    run_async: Callable,
    case: PreferenceCase,
) -> None:
    async def _exercise() -> None:
        config = _cleanup_config()
        movie = await _insert_movie(
            db,
            name=f"movie-{case.id}",
            preferred_quality=case.initial_quality,
            preferred_codec=case.initial_codec,
        )
        commit_spy = await _run_cleanup_tracked(
            db, config, cleanup_stale_movie_preferences
        )
        if case.should_commit:
            commit_spy.assert_awaited_once()
        else:
            commit_spy.assert_not_awaited()
        quality, codec = await _load_movie_preferences(make_session, movie.id)
        assert quality == case.expected_quality
        assert codec == case.expected_codec

    run_async(_exercise())
