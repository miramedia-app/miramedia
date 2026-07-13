"""PostgreSQL integration test harness."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from tests.integration._db_url import (
    alembic_sync_url,
    assert_safe_integration_database,
    integration_database_url,
)

_alembic_ready = False


class _TrackedSessions:
    """Track independent sessions opened via ``make_session`` for teardown."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory
        self._sessions: list[AsyncSession] = []

    def open(self) -> AsyncSession:
        session = self._factory()
        self._sessions.append(session)
        return session

    async def close_all(self) -> None:
        for session in self._sessions:
            if session.in_transaction():
                await session.rollback()
            await session.close()
        self._sessions.clear()


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def run_async(event_loop: asyncio.AbstractEventLoop) -> Callable:
    def _run(coro):
        return event_loop.run_until_complete(coro)

    return _run


def _run_alembic_upgrade(sync_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": sync_url}
    proc = subprocess.run(
        ["uv", "run", "--python", "3.13", "alembic", "upgrade", "head"],
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


async def _assert_database_ready(url: str) -> None:
    """Single connection check — CI/service health must have Postgres up already."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        detail = (
            "PostgreSQL connection check failed — set MIRAMEDIA_TEST_DATABASE_URL "
            f"to a reachable disposable database: {exc}"
        )
        pytest.fail(detail)
    finally:
        await engine.dispose()


async def _truncate_application_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename <> 'alembic_version'
                ORDER BY tablename
                """
            )
        )
        tables = [row[0] for row in result]
        if not tables:
            return
        quoted = ", ".join(f'"{name}"' for name in tables)
        await conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))


@pytest.fixture(scope="session")
def integration_db_url() -> str:
    url = integration_database_url()
    assert_safe_integration_database(url)
    return url


@pytest.fixture(scope="session")
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


@pytest.fixture(scope="session")
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
    """Yield a session factory that is rolled back/closed before truncation."""

    tracker = _TrackedSessions(session_factory)
    yield tracker.open
    run_async(tracker.close_all())


@pytest.fixture
def tracked_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> _TrackedSessions:
    """Expose the session registry for harness regression tests."""
    return _TrackedSessions(session_factory)
