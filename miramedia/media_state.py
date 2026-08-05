"""Denormalized download / progress counters for fast library list queries."""

from __future__ import annotations

import logging
from enum import StrEnum
from uuid import UUID

from sqlalchemy import and_, case, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

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


def _list_progress_status_sql(
    wanted_col: ColumnElement[int],
    downloaded_col: ColumnElement[int],
) -> ColumnElement[str]:
    """SQL expression mirroring :func:`_progress_status`."""
    return case(
        (
            and_(wanted_col > 0, downloaded_col == wanted_col),
            literal(ProgressStatus.complete),
        ),
        (downloaded_col > 0, literal(ProgressStatus.partial)),
        else_=literal(ProgressStatus.none),
    )


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


async def _update_show_progress_counters(
    db: AsyncSession, *, show_id: UUID | None = None
) -> None:
    """Set wanted/downloaded counters and list progress on ``show`` rows."""
    # Wanted = non-skipped episodes. Specials (Season 0) are persisted as
    # skipped at add time when download_specials is off, so the skipped flag
    # alone keeps the denormalized list counters consistent with the detail
    # page in ShowService._show_to_public.
    wanted_filter = Episode.skipped.is_(False)

    stats_stmt = (
        select(
            Season.show_id.label("show_id"),
            func.count().filter(wanted_filter).label("wanted"),
            func.count()
            .filter(wanted_filter, Episode.downloaded.is_(True))
            .label("downloaded"),
        )
        .select_from(Episode)
        .join(Season, Season.id == Episode.season_id)
        .group_by(Season.show_id)
    )
    if show_id is not None:
        stats_stmt = stats_stmt.where(Season.show_id == show_id)
    stats_subq = stats_stmt.subquery("episode_stats")

    computed_stmt = (
        select(
            Show.id.label("show_id"),
            func.coalesce(stats_subq.c.wanted, 0).label("wanted"),
            func.coalesce(stats_subq.c.downloaded, 0).label("downloaded"),
        )
        .select_from(Show)
        .outerjoin(stats_subq, Show.id == stats_subq.c.show_id)
    )
    if show_id is not None:
        computed_stmt = computed_stmt.where(Show.id == show_id)
    computed_subq = computed_stmt.subquery("show_progress")

    wanted_col = computed_subq.c.wanted
    downloaded_col = computed_subq.c.downloaded

    await db.execute(
        update(Show)
        .where(Show.id == computed_subq.c.show_id)
        .values(
            wanted_episode_count=wanted_col,
            downloaded_episode_count=downloaded_col,
            list_progress_status=_list_progress_status_sql(wanted_col, downloaded_col),
        )
    )


async def refresh_show_progress(
    db: AsyncSession, *, show_id: UUID | None = None
) -> None:
    """Recompute wanted/downloaded episode counters on ``show``."""
    if show_id is not None:
        await refresh_episode_downloaded(db, show_id=show_id)
    else:
        await refresh_episode_downloaded(db)

    await _update_show_progress_counters(db, show_id=show_id)


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
