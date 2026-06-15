import typing
import uuid
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MediaRequestId = typing.NewType("MediaRequestId", UUID)


class MediaType(StrEnum):
    movie = "movie"
    show = "show"


class RequestStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    downloading = "downloading"
    downloaded = "downloaded"
    rejected = "rejected"


class RequestSource(StrEnum):
    native = "native"
    seerr = "seerr"


class MediaRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: MediaRequestId = Field(
        default_factory=lambda: MediaRequestId(uuid.uuid4()),
    )
    media_type: MediaType
    title: str
    external_id: str
    imdb_id: str | None = None
    metadata_provider: str = ""
    movie_id: UUID | None = None
    show_id: UUID | None = None
    season_number: int | None = None
    status: RequestStatus = RequestStatus.pending
    wanted_quality: int | None = None
    requested_by_id: UUID | None = None
    requested_by_username: str | None = None
    decided_by_id: UUID | None = None
    note: str | None = None
    source: RequestSource = RequestSource.native
    tmdb_id: int | None = None
    seerr_request_id: int | None = None
    seerr_media_id: int | None = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class MediaRequestCreate(BaseModel):
    media_type: MediaType
    title: str
    external_id: str
    imdb_id: str | None = None
    metadata_provider: str = ""
    movie_id: UUID | None = None
    show_id: UUID | None = None
    season_number: int | None = None
    wanted_quality: int | None = None
    note: str | None = None


class MediaRequestUpdate(BaseModel):
    wanted_quality: int | None = None
    note: str | None = None


class MediaRequestCount(BaseModel):
    pending: int = 0
