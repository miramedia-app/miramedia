"""Feed observation schemas (design 385 Slice A)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from miramedia.indexers.schemas import IndexerQueryResult


class FeedDecision(StrEnum):
    seen = "seen"
    unmatched = "unmatched"
    drop_score = "drop_score"
    not_wanted = "not_wanted"
    already_have = "already_have"
    active_download = "active_download"
    deny_listed = "deny_listed"
    would_grab = "would_grab"
    skipped_destination = "skipped_destination"
    error = "error"


class FeedEnvelope(BaseModel):
    """Wraps IndexerQueryResult with provider identity fields the mixin drops."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    result: IndexerQueryResult
    provider_guid: str | None = None
    pub_date: datetime | None = None
    info_hash: str | None = None
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None
    categories: list[str] = Field(default_factory=list)


FeedSourceId = UUID


class FeedSourceKey(BaseModel):
    backend: str
    indexer_key: str


class FeedItemRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid4)
    source_id: UUID
    provider_guid: str | None = None
    info_hash: str | None = None
    download_url_redacted: str | None = None
    title: str
    size: int
    indexer: str | None = None
    usenet: bool = False
    seeders: int | None = None
    age: int = 0
    imdb_id: str | None = None
    tmdb_id: str | None = None
    tvdb_id: str | None = None
    bound_media_type: str | None = None
    bound_media_id: UUID | None = None
    decision: FeedDecision
    score: int | None = None
    first_seen_at: datetime | None = None
    decided_at: datetime | None = None
    attempt_count: int = 1
