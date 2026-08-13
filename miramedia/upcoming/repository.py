from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.movies.models import Movie
from miramedia.shows.models import Episode, Season, Show
from miramedia.upcoming.schemas import UpcomingWindow


class UpcomingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def fetch_episode_rows(
        self, window: UpcomingWindow, *, limit: int
    ) -> list[Any]:
        stmt = (
            select(
                Episode.id,
                Episode.title,
                Episode.air_date,
                Episode.air_time,
                Episode.number,
                Episode.downloaded,
                Season.number,
                Show.id,
                Show.name,
            )
            .join(Season, Episode.season_id == Season.id)
            .join(Show, Season.show_id == Show.id)
            .where(
                Episode.air_date.is_not(None),
                Episode.air_date >= window.start,
                Episode.air_date <= window.end,
                Episode.skipped.is_(False),
                Season.skipped.is_(False),
                Show.skipped.is_(False),
            )
            .order_by(Episode.air_date.asc(), Show.name.asc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).all())

    async def fetch_movie_rows(
        self, window: UpcomingWindow, *, limit: int
    ) -> list[Any]:
        stmt = (
            select(
                Movie.id,
                Movie.name,
                Movie.release_date,
                Movie.downloaded,
            )
            .where(
                Movie.release_date.is_not(None),
                Movie.release_date >= window.start,
                Movie.release_date <= window.end,
                Movie.skipped.is_(False),
            )
            .order_by(Movie.release_date.asc(), Movie.name.asc())
            .limit(limit)
        )
        return list((await self.db.execute(stmt)).all())
