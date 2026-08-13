"""Pydantic schemas and window types for the upcoming library list."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Matches the calendar page's default preset (today → +30 days).
DEFAULT_PAST_DAYS = 0
DEFAULT_FUTURE_DAYS = 30
UPCOMING_HARD_CAP = 100
# Widest caller-selectable window; keeps the two set-based queries bounded.
MAX_WINDOW_DAYS = 366

MediaType = Literal["episode", "movie"]


class UpcomingItem(BaseModel):
    """One row for the Upcoming / Calendar list."""

    media_type: MediaType
    id: UUID
    title: str
    date: date
    # Local air time-of-day when known (episodes from Cinemeta/TVMaze); None otherwise.
    air_time: time | None = None
    # Poster target for /static/image/{id}: show id for episodes, movie id otherwise.
    poster_id: UUID
    show_id: UUID | None = None
    show_name: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    downloaded: bool = False


class UpcomingResponse(BaseModel):
    items: list[UpcomingItem] = Field(default_factory=list)
    window_start: date
    window_end: date
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class UpcomingWindow:
    start: date
    end: date
    today: date
