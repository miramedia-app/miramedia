"""Minimal FK graphs for integration tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.file_status import ImportOutcome
from miramedia.imports.models import ImportBatch, ScanResultCache
from miramedia.movies.models import Movie, MovieFile
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.torrents.schemas import Quality


async def seed_import_batch(db: AsyncSession, *, total: int = 0) -> None:
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = pg_insert(ImportBatch).values(id="current", total=total)
    stmt = stmt.on_conflict_do_update(
        index_elements=["id"],
        set_={"total": total},
    )
    await db.execute(stmt)
    await db.commit()


async def seed_pending_scan_row(
    db: AsyncSession,
    *,
    directory: str,
    media_type: str = "show",
    status: str = "pending",
    extra_payload: dict | None = None,
) -> None:
    payload = {
        "status": status,
        "media_type_hint": media_type,
        **(extra_payload or {}),
    }
    db.add(
        ScanResultCache(
            id=uuid.uuid4(),
            directory=directory,
            payload=payload,
            scanned_at=datetime.now(UTC),
        )
    )
    await db.commit()


async def insert_show_episode_file(
    db: AsyncSession,
    *,
    sha1: str | None = None,
    import_error: str | None = None,
    import_status: ImportOutcome = ImportOutcome.imported,
) -> tuple[Show, EpisodeFile]:
    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    file_id = uuid.uuid4()
    show = Show(
        id=show_id,
        external_id="ext-show",
        metadata_provider="native",
        name="Integration Show",
        overview="",
        year=2026,
    )
    season = Season(id=season_id, show_id=show_id, number=1)
    episode = Episode(
        id=episode_id,
        season_id=season_id,
        number=1,
        title="Pilot",
        overview=None,
    )
    episode_file = EpisodeFile(
        id=file_id,
        episode_id=episode_id,
        quality=Quality.hd,
        import_status=import_status,
        import_error=import_error,
        sha1=sha1,
    )
    db.add_all([show, season, episode, episode_file])
    await db.commit()
    return show, episode_file


async def insert_movie_file(
    db: AsyncSession,
    *,
    sha1: str | None = None,
    import_error: str | None = None,
    import_status: ImportOutcome = ImportOutcome.imported,
) -> tuple[Movie, MovieFile]:
    movie_id = uuid.uuid4()
    file_id = uuid.uuid4()
    movie = Movie(
        id=movie_id,
        external_id="ext-movie",
        metadata_provider="native",
        name="Integration Movie",
        overview="",
        year=2026,
    )
    movie_file = MovieFile(
        id=file_id,
        movie_id=movie_id,
        quality=Quality.hd,
        import_status=import_status,
        import_error=import_error,
        sha1=sha1,
    )
    db.add_all([movie, movie_file])
    await db.commit()
    return movie, movie_file
