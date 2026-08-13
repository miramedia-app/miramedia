from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miramedia.movies.schemas import MovieId
from miramedia.shows.schemas import EpisodeId, ShowId

WatchStateSource = Literal["derived", "manual"]


class MediaKind(StrEnum):
    movie = "movie"
    episode = "episode"


class PlaybackProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_id: UUID
    media_kind: MediaKind
    position_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=1_000, le=86_400_000)
    completed: bool
    updated_at: datetime


class PlaybackProgressUpsert(BaseModel):
    file_id: UUID
    media_kind: MediaKind
    position_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=1_000, le=86_400_000)

    @model_validator(mode="after")
    def validate_position_within_duration(self) -> PlaybackProgressUpsert:
        if self.position_ms > self.duration_ms + 1_000:
            msg = "position_ms must be at most duration_ms + 1000"
            raise ValueError(msg)
        return self


class WatchState(BaseModel):
    media_kind: Literal["movie", "episode"]
    media_id: UUID
    watched: bool
    source: WatchStateSource | None
    watched_at: datetime | None


class WatchStateUpdate(BaseModel):
    media_kind: Literal["movie", "episode"]
    media_id: UUID
    watched: bool


class SeasonWatchStateUpdate(BaseModel):
    show_id: UUID
    season_number: int = Field(ge=0)
    watched: bool
    include_specials: bool = False


class ShowWatchStateUpdate(BaseModel):
    show_id: UUID
    watched: bool
    include_specials: bool = False


class ContinueWatchingItem(BaseModel):
    file_id: UUID
    media_kind: MediaKind
    media_id: UUID
    show_id: ShowId | None = None
    title: str
    poster_media_id: UUID
    position_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=1_000)
    updated_at: datetime
    year: int | None = None
    season_number: int | None = None
    episode_number: int | None = None


class UpNextItem(BaseModel):
    file_id: UUID
    media_kind: Literal["episode"] = "episode"
    media_id: UUID
    show_id: UUID
    show_name: str
    season_number: int
    episode_number: int
    episode_title: str | None
    title: str
    poster_media_id: UUID
    watched: bool = False
    position_ms: int = 0
    duration_ms: int | None = None
    activity_at: datetime


def new_progress_id() -> UUID:
    return uuid.uuid4()


def continue_movie_item(
    *,
    file_id: UUID,
    movie_id: MovieId,
    title: str,
    position_ms: int,
    duration_ms: int,
    updated_at: datetime,
    year: int | None = None,
) -> ContinueWatchingItem:
    return ContinueWatchingItem(
        file_id=file_id,
        media_kind=MediaKind.movie,
        media_id=UUID(str(movie_id)),
        title=title,
        poster_media_id=UUID(str(movie_id)),
        position_ms=position_ms,
        duration_ms=duration_ms,
        updated_at=updated_at,
        year=year,
    )


def continue_episode_item(
    *,
    file_id: UUID,
    episode_id: EpisodeId,
    show_id: ShowId,
    title: str,
    position_ms: int,
    duration_ms: int,
    updated_at: datetime,
    season_number: int,
    episode_number: int,
) -> ContinueWatchingItem:
    return ContinueWatchingItem(
        file_id=file_id,
        media_kind=MediaKind.episode,
        media_id=UUID(str(episode_id)),
        show_id=show_id,
        title=title,
        poster_media_id=UUID(str(show_id)),
        position_ms=position_ms,
        duration_ms=duration_ms,
        updated_at=updated_at,
        season_number=season_number,
        episode_number=episode_number,
    )
