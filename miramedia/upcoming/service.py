"""Upcoming library list: window bounds, merge/sort, and service orchestration."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from miramedia.config import MiraMediaConfig, configured_timezone
from miramedia.naming import format_episode_label
from miramedia.upcoming.repository import UpcomingRepository
from miramedia.upcoming.schemas import (
    DEFAULT_FUTURE_DAYS,
    DEFAULT_PAST_DAYS,
    MAX_WINDOW_DAYS,
    UPCOMING_HARD_CAP,
    UpcomingItem,
    UpcomingResponse,
    UpcomingWindow,
)


def local_today(now: datetime | None = None) -> date:
    """Server-local calendar date (matches auto-download air/release compares).

    An explicitly passed aware datetime is honored in its own timezone; the
    no-argument form uses the server's local zone.
    """
    target = now if now is not None else datetime.now(configured_timezone())
    return target.date()


def upcoming_window(
    today: date | None = None,
    *,
    past_days: int = DEFAULT_PAST_DAYS,
    future_days: int = DEFAULT_FUTURE_DAYS,
) -> UpcomingWindow:
    day = today if today is not None else local_today()
    return UpcomingWindow(
        start=day - timedelta(days=past_days),
        end=day + timedelta(days=future_days),
        today=day,
    )


def explicit_window(start: date | None, end: date | None) -> UpcomingWindow:
    """Caller-chosen window, clamped to MAX_WINDOW_DAYS.

    Either bound may be omitted: the missing side falls back to the default
    offset from today, so `?start=` alone still yields a sane range.
    """
    day = local_today()
    resolved_start = (
        start if start is not None else day - timedelta(days=DEFAULT_PAST_DAYS)
    )
    resolved_end = end if end is not None else day + timedelta(days=DEFAULT_FUTURE_DAYS)
    if resolved_end < resolved_start:
        msg = "end must not be earlier than start"
        raise ValueError(msg)
    if (resolved_end - resolved_start).days > MAX_WINDOW_DAYS:
        resolved_end = resolved_start + timedelta(days=MAX_WINDOW_DAYS)
    return UpcomingWindow(start=resolved_start, end=resolved_end, today=day)


def merge_upcoming(
    items: list[UpcomingItem],
    *,
    limit: int = UPCOMING_HARD_CAP,
) -> list[UpcomingItem]:
    """Sort by date asc, title asc; hard-cap."""
    dated = list(items)
    dated.sort(key=lambda item: (item.date, item.title.casefold()))
    if limit <= 0:
        return []
    return dated[:limit]


class UpcomingService:
    def __init__(self, repository: UpcomingRepository) -> None:
        self.repository = repository

    async def fetch_upcoming_items(
        self, window: UpcomingWindow, *, limit: int = UPCOMING_HARD_CAP
    ) -> tuple[list[UpcomingItem], bool]:
        """Two set-based queries (episodes + movies); merge in process. No N+1."""
        episode_rows = await self.repository.fetch_episode_rows(window, limit=limit)
        movie_rows = await self.repository.fetch_movie_rows(window, limit=limit)

        items: list[UpcomingItem] = []
        for (
            episode_id,
            episode_title,
            air_date,
            air_time,
            episode_number,
            downloaded,
            season_number,
            show_id,
            show_name,
        ) in episode_rows:
            if air_date is None:
                continue
            items.append(
                UpcomingItem(
                    media_type="episode",
                    id=episode_id,
                    title=format_episode_label(
                        show_name, season_number, episode_number, episode_title or ""
                    ),
                    date=air_date,
                    air_time=air_time,
                    poster_id=show_id,
                    show_id=show_id,
                    show_name=show_name,
                    season_number=season_number,
                    episode_number=episode_number,
                    downloaded=bool(downloaded),
                )
            )

        for movie_id, name, release_date, downloaded in movie_rows:
            if release_date is None:
                continue
            items.append(
                UpcomingItem(
                    media_type="movie",
                    id=movie_id,
                    title=name,
                    date=release_date,
                    poster_id=movie_id,
                    downloaded=bool(downloaded),
                )
            )

        episodes_truncated = len(episode_rows) == limit
        movies_truncated = len(movie_rows) == limit
        truncated = episodes_truncated or movies_truncated
        items = merge_upcoming(items, limit=max(len(items), 1))
        if truncated:
            cutoffs: list[date] = []
            if episodes_truncated:
                cutoffs.append(episode_rows[-1][2])  # air_date
            if movies_truncated:
                cutoffs.append(movie_rows[-1][2])  # release_date
            cutoff = min(cutoffs)
            clamped = [item for item in items if item.date < cutoff]
            if clamped:
                items = clamped
            else:
                # Every fetched row shares the cutoff date (e.g. 100+ items on one
                # day): strict clamping would return nothing useful — keep the
                # truncated prefix instead.
                items = [item for item in items if item.date <= cutoff]
        return items[:limit], truncated

    async def get_upcoming(
        self, start: date | None = None, end: date | None = None
    ) -> UpcomingResponse:
        if start or end:
            window = explicit_window(start, end)
        else:
            native = MiraMediaConfig().watchlists.native
            window = upcoming_window(
                past_days=native.upcoming_default_past_days,
                future_days=native.upcoming_default_future_days,
            )
        items, truncated = await self.fetch_upcoming_items(window)
        return UpcomingResponse(
            items=items,
            window_start=window.start,
            window_end=window.end,
            truncated=truncated,
        )
