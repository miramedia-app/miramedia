"""PostgreSQL integration tests for set-based show progress refresh."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from miramedia.file_status import ImportOutcome
from miramedia.media_state import ProgressStatus, refresh_show_progress
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.torrents.schemas import Quality
from tests.pg_disposable import (
    disposable_database_sync_url,
    require_disposable_database_url,
)

pytestmark = pytest.mark.postgresql

_alembic_ready = False


def _async_url(sync_url: str) -> str:
    from sqlalchemy.engine.url import make_url

    url = make_url(sync_url)
    return url.set(drivername="postgresql+asyncpg").render_as_string(
        hide_password=False
    )


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


async def _truncate_tables(engine: AsyncEngine) -> None:
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
        if tables:
            quoted = ", ".join(f'"{name}"' for name in tables)
            await conn.execute(
                text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
            )


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


@pytest.fixture(scope="session")
def pg_engine(run_async: Callable) -> Iterator[AsyncEngine]:
    global _alembic_ready
    require_disposable_database_url()
    sync_url = disposable_database_sync_url()
    async_url = _async_url(sync_url)
    if not _alembic_ready:
        _run_alembic_upgrade(sync_url)
        _alembic_ready = True
    engine = create_async_engine(async_url, poolclass=NullPool)
    yield engine
    run_async(engine.dispose())


@pytest.fixture(autouse=True)
def clean_database(pg_engine: AsyncEngine, run_async: Callable) -> Iterator[None]:
    run_async(_truncate_tables(pg_engine))
    yield
    run_async(_truncate_tables(pg_engine))


@pytest.fixture
def db(pg_engine: AsyncEngine, run_async: Callable) -> Iterator[AsyncSession]:
    factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    session = factory()
    yield session
    run_async(session.close())


async def _seed_show(
    db: AsyncSession,
    *,
    name: str,
    episode_specs: list[tuple[bool, bool]],
) -> Show:
    """Each spec is (skipped, has_imported_file)."""
    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    show = Show(
        id=show_id,
        external_id=f"ext-{show_id.hex[:8]}",
        metadata_provider="native",
        name=name,
        overview="",
        year=2026,
    )
    season = Season(id=season_id, show_id=show_id, number=1)
    episodes: list[Episode] = []
    files: list[EpisodeFile] = []
    for number, (skipped, has_file) in enumerate(episode_specs, start=1):
        episode_id = uuid.uuid4()
        episodes.append(
            Episode(
                id=episode_id,
                season_id=season_id,
                number=number,
                title=f"Ep {number}",
                overview=None,
                skipped=skipped,
            )
        )
        if has_file:
            files.append(
                EpisodeFile(
                    id=uuid.uuid4(),
                    episode_id=episode_id,
                    quality=Quality.hd,
                    import_status=ImportOutcome.imported,
                )
            )
    db.add_all([show, season, *episodes, *files])
    await db.commit()
    return show


async def _seed_empty_show(db: AsyncSession, name: str) -> Show:
    show_id = uuid.uuid4()
    show = Show(
        id=show_id,
        external_id=f"ext-{show_id.hex[:8]}",
        metadata_provider="native",
        name=name,
        overview="",
        year=2026,
    )
    db.add(show)
    await db.commit()
    return show


def test_refresh_show_progress_set_based_semantics(
    db: AsyncSession, run_async: Callable
) -> None:
    run_async(_refresh_show_progress_set_based_semantics(db))


async def _refresh_show_progress_set_based_semantics(db: AsyncSession) -> None:
    complete = await _seed_show(
        db,
        name="Complete",
        episode_specs=[(False, True), (False, True), (False, True)],
    )
    partial = await _seed_show(
        db,
        name="Partial",
        episode_specs=[(False, True), (False, False), (False, False)],
    )
    empty = await _seed_empty_show(db, name="Empty")
    skipped_only = await _seed_show(
        db,
        name="Skipped",
        episode_specs=[(True, True), (True, False)],
    )

    await refresh_show_progress(db)
    await db.commit()

    rows = {row.id: row for row in (await db.execute(select(Show))).scalars().all()}

    assert rows[complete.id].wanted_episode_count == 3
    assert rows[complete.id].downloaded_episode_count == 3
    assert rows[complete.id].list_progress_status == ProgressStatus.complete

    assert rows[partial.id].wanted_episode_count == 3
    assert rows[partial.id].downloaded_episode_count == 1
    assert rows[partial.id].list_progress_status == ProgressStatus.partial

    assert rows[empty.id].wanted_episode_count == 0
    assert rows[empty.id].downloaded_episode_count == 0
    assert rows[empty.id].list_progress_status == ProgressStatus.none

    assert rows[skipped_only.id].wanted_episode_count == 0
    assert rows[skipped_only.id].downloaded_episode_count == 0
    assert rows[skipped_only.id].list_progress_status == ProgressStatus.none


def test_refresh_show_progress_scoped_show_id(
    db: AsyncSession, run_async: Callable
) -> None:
    run_async(_refresh_show_progress_scoped_show_id(db))


async def _refresh_show_progress_scoped_show_id(db: AsyncSession) -> None:
    target = await _seed_show(
        db,
        name="Target",
        episode_specs=[(False, True), (False, False)],
    )
    other = await _seed_show(
        db,
        name="Other",
        episode_specs=[(False, True), (False, True)],
    )

    await refresh_show_progress(db, show_id=target.id)
    await db.commit()

    target_row = await db.get(Show, target.id)
    other_row = await db.get(Show, other.id)

    assert target_row is not None
    assert other_row is not None
    assert target_row.wanted_episode_count == 2
    assert target_row.downloaded_episode_count == 1
    assert target_row.list_progress_status == ProgressStatus.partial
    assert other_row.wanted_episode_count == 0
    assert other_row.downloaded_episode_count == 0


def test_unscoped_refresh_uses_constant_show_update_count(
    db: AsyncSession, pg_engine: AsyncEngine, run_async: Callable
) -> None:
    run_async(_unscoped_refresh_uses_constant_show_update_count(db, pg_engine))


async def _unscoped_refresh_uses_constant_show_update_count(
    db: AsyncSession, pg_engine: AsyncEngine
) -> None:
    for idx in range(5):
        await _seed_show(
            db,
            name=f"Show {idx}",
            episode_specs=[(False, bool(idx % 2)), (False, False)],
        )

    show_updates = 0

    def _count_show_updates(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        nonlocal show_updates
        if "UPDATE" in statement.upper() and "SHOW" in statement.upper():
            show_updates += 1

    event.listen(pg_engine.sync_engine, "before_cursor_execute", _count_show_updates)
    try:
        await refresh_show_progress(db)
        await db.commit()
    finally:
        event.remove(
            pg_engine.sync_engine, "before_cursor_execute", _count_show_updates
        )

    assert show_updates == 1
