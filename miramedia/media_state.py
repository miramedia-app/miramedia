"""Denormalized download / progress counters for fast library list queries."""

from __future__ import annotations

import logging
from enum import StrEnum
from uuid import UUID

from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.file_status import ImportOutcome
from miramedia.movies.models import Movie, MovieFile
from miramedia.shows.models import Episode, EpisodeFile, Season, Show

log = logging.getLogger(__name__)


class ProgressStatus(StrEnum):
    none = "none"
    partial = "partial"
    complete = "complete"


def _progress_status(wanted: int, downloaded: int) -> ProgressStatus:
    if wanted > 0 and downloaded == wanted:
        return ProgressStatus.complete
    if downloaded > 0:
        return ProgressStatus.partial
    return ProgressStatus.none


async def refresh_movie_downloaded(
    db: AsyncSession, *, movie_id: UUID | None = None
) -> None:
    """Set ``movie.downloaded`` from imported ``movie_file`` rows."""
    imported_exists = (
        select(MovieFile.movie_id)
        .where(MovieFile.import_status == ImportOutcome.imported)
        .distinct()
    )
    if movie_id is not None:
        has_file = (
            await db.scalar(
                select(func.count())
                .select_from(MovieFile)
                .where(
                    MovieFile.movie_id == movie_id,
                    MovieFile.import_status == ImportOutcome.imported,
                )
            )
        ) or 0
        await db.execute(
            update(Movie).where(Movie.id == movie_id).values(downloaded=has_file > 0)
        )
        return

    await db.execute(
        update(Movie).where(Movie.id.in_(imported_exists)).values(downloaded=True)
    )
    await db.execute(
        update(Movie).where(Movie.id.not_in(imported_exists)).values(downloaded=False)
    )


async def refresh_episode_downloaded(
    db: AsyncSession,
    *,
    episode_id: UUID | None = None,
    show_id: UUID | None = None,
) -> None:
    """Set ``episode.downloaded`` from imported ``episode_file`` rows."""
    imported_episodes = (
        select(EpisodeFile.episode_id)
        .where(EpisodeFile.import_status == ImportOutcome.imported)
        .distinct()
    )
    if episode_id is not None:
        has_file = (
            await db.scalar(
                select(func.count())
                .select_from(EpisodeFile)
                .where(
                    EpisodeFile.episode_id == episode_id,
                    EpisodeFile.import_status == ImportOutcome.imported,
                )
            )
        ) or 0
        await db.execute(
            update(Episode)
            .where(Episode.id == episode_id)
            .values(downloaded=has_file > 0)
        )
        return

    scope = select(Episode.id)
    if show_id is not None:
        scope = scope.where(
            Episode.season_id.in_(select(Season.id).where(Season.show_id == show_id))
        )

    await db.execute(
        update(Episode)
        .where(Episode.id.in_(scope), Episode.id.in_(imported_episodes))
        .values(downloaded=True)
    )
    await db.execute(
        update(Episode)
        .where(Episode.id.in_(scope), Episode.id.not_in(imported_episodes))
        .values(downloaded=False)
    )


async def refresh_show_progress(
    db: AsyncSession, *, show_id: UUID | None = None
) -> None:
    """Recompute wanted/downloaded episode counters on ``show``."""
    if show_id is not None:
        await refresh_episode_downloaded(db, show_id=show_id)
    else:
        await refresh_episode_downloaded(db)

    # Wanted = non-skipped episodes. Specials (Season 0) are persisted as
    # skipped at add time when download_specials is off, so the skipped flag
    # alone keeps the denormalized list counters consistent with the detail
    # page in ShowService._show_to_public.
    wanted_filter: list[ColumnElement[bool]] = [Episode.skipped.is_(False)]

    stats = (
        select(
            Season.show_id.label("show_id"),
            func.count().filter(*wanted_filter).label("wanted"),
            func.count()
            .filter(*wanted_filter, Episode.downloaded.is_(True))
            .label("downloaded"),
        )
        .select_from(Episode)
        .join(Season, Season.id == Episode.season_id)
        .group_by(Season.show_id)
    )
    if show_id is not None:
        stats = stats.where(Season.show_id == show_id)

    rows = (await db.execute(stats)).all()
    seen: set[UUID] = set()
    for row in rows:
        seen.add(row.show_id)
        wanted = int(row.wanted or 0)
        downloaded = int(row.downloaded or 0)
        await db.execute(
            update(Show)
            .where(Show.id == row.show_id)
            .values(
                wanted_episode_count=wanted,
                downloaded_episode_count=downloaded,
                list_progress_status=_progress_status(wanted, downloaded),
            )
        )

    if show_id is not None:
        if show_id not in seen:
            await db.execute(
                update(Show)
                .where(Show.id == show_id)
                .values(
                    wanted_episode_count=0,
                    downloaded_episode_count=0,
                    list_progress_status=ProgressStatus.none,
                )
            )
        return

    # Zero out shows with no episodes in stats subquery.
    if seen:
        await db.execute(
            update(Show)
            .where(Show.id.not_in(seen))
            .values(
                wanted_episode_count=0,
                downloaded_episode_count=0,
                list_progress_status=ProgressStatus.none,
            )
        )
    else:
        await db.execute(
            update(Show).values(
                wanted_episode_count=0,
                downloaded_episode_count=0,
                list_progress_status=ProgressStatus.none,
            )
        )


async def refresh_media_state(
    db: AsyncSession,
    *,
    movie_id: UUID | None = None,
    show_id: UUID | None = None,
) -> None:
    if movie_id is not None:
        await refresh_movie_downloaded(db, movie_id=movie_id)
    if show_id is not None:
        await refresh_show_progress(db, show_id=show_id)
    if movie_id is None and show_id is None:
        await refresh_movie_downloaded(db)
        await refresh_show_progress(db)
