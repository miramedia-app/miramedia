"""Plan 289: upcoming window bounds on real Postgres."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.shows.models import Episode, Season, Show
from miramedia.upcoming.repository import UpcomingRepository
from miramedia.upcoming.schemas import DEFAULT_FUTURE_DAYS, DEFAULT_PAST_DAYS
from miramedia.upcoming.service import UpcomingService, upcoming_window

pytestmark = pytest.mark.integration


async def _seed_episode(
    db: AsyncSession,
    *,
    air_date: date,
    skipped: bool = False,
    title: str = "Ep",
    episode_number: int = 1,
) -> Episode:
    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    episode_id = uuid.uuid4()
    show = Show(
        id=show_id,
        external_id=f"ext-{show_id.hex}",
        metadata_provider="native",
        name=f"Show {episode_number}",
        overview="",
        year=2026,
    )
    season = Season(id=season_id, show_id=show_id, number=1)
    episode = Episode(
        id=episode_id,
        season_id=season_id,
        number=episode_number,
        title=title,
        overview=None,
        air_date=air_date,
        skipped=skipped,
    )
    db.add_all([show, season, episode])
    await db.commit()
    return episode


def test_upcoming_window_includes_edges_excludes_outside_and_skipped(
    db: AsyncSession,
    run_async: Callable,
) -> None:
    async def _run() -> None:
        today = date(2026, 8, 7)
        window = upcoming_window(today)
        assert window.start == today - timedelta(days=DEFAULT_PAST_DAYS)
        assert window.end == today + timedelta(days=DEFAULT_FUTURE_DAYS)

        at_start = await _seed_episode(
            db, air_date=window.start, title="At start", episode_number=1
        )
        at_end = await _seed_episode(
            db, air_date=window.end, title="At end", episode_number=2
        )
        await _seed_episode(
            db,
            air_date=window.start - timedelta(days=1),
            title="Before window",
            episode_number=3,
        )
        await _seed_episode(
            db,
            air_date=window.end + timedelta(days=1),
            title="After window",
            episode_number=4,
        )
        await _seed_episode(
            db,
            air_date=today,
            title="Skipped in window",
            episode_number=5,
            skipped=True,
        )

        service = UpcomingService(UpcomingRepository(db))
        items, truncated = await service.fetch_upcoming_items(window)

        assert truncated is False
        assert len(items) == 2
        returned_ids = {item.id for item in items}
        assert returned_ids == {at_start.id, at_end.id}

    run_async(_run())
