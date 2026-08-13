from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

WatchlistMediaKind = Literal["movie", "show", "episode"]
ShowAvailabilityStatus = Literal[
    "all_available_episodes_watched",
    "no_downloaded_episode_available",
]


# Schema bounds match DB columns (255/2000) and reject pathological payloads at
# the edge; the service still enforces tighter post-trim limits (80/500).
# Reorder cap is fixed because max_items_per_list defaults to 0 (unlimited).
class WatchlistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class WatchlistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class WatchlistItemCreate(BaseModel):
    media_kind: WatchlistMediaKind
    media_id: UUID


class WatchlistReorder(BaseModel):
    item_ids: list[UUID] = Field(min_length=1, max_length=10_000)


class WatchlistNextEpisode(BaseModel):
    file_id: UUID
    media_id: UUID
    season_number: int
    episode_number: int
    episode_title: str | None
    title: str
    watched: bool = False
    position_ms: int = 0
    duration_ms: int | None = None


class WatchlistItemView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    media_kind: WatchlistMediaKind
    media_id: UUID
    title: str
    poster_media_id: UUID
    watched: bool
    year: int | None = None
    file_id: UUID | None = None
    position_ms: int | None = None
    duration_ms: int | None = None
    show_id: UUID | None = None
    season_number: int | None = None
    episode_number: int | None = None
    next_episode: WatchlistNextEpisode | None = None
    show_status: ShowAvailabilityStatus | None = None


class WatchlistSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    item_count: int
    cover_poster_media_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class WatchlistDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    items: list[WatchlistItemView]
    created_at: datetime
    updated_at: datetime
