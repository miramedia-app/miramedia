from __future__ import annotations

import uuid
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SubtitleFile(BaseModel):
    language: str
    file_name: str


class SubtitleStatus(BaseModel):
    media_type: str  # "movie" or "episode"
    media_id: UUID
    desired_languages: list[str]
    available_languages: list[str]
    missing_languages: list[str]


class EpisodeSubtitleStatus(BaseModel):
    episode_id: UUID
    season_id: UUID
    season_number: int
    episode_number: int
    title: str
    downloaded: bool
    status: SubtitleStatus


class ShowSubtitleStatus(BaseModel):
    show_id: UUID
    desired_languages: list[str]
    episodes: list[EpisodeSubtitleStatus]


class SubtitleSearchResult(BaseModel):
    provider: str
    language: str
    subtitle_id: str
    release_name: str | None = None
    score: int = 0


class SubtitleSearchResponse(BaseModel):
    downloaded: list[str]
    count: int


class SubtitleRecord(BaseModel):
    id: UUID = Field(default_factory=uuid.uuid4)
    media_type: str
    episode_id: UUID | None = None
    movie_id: UUID | None = None
    language: str
    source: str
    provider: str | None = None
    downloaded_at: datetime | None = None
