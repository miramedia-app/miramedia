"""Read-only storage-health API schemas (design 387 Slice A)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from miramedia.file_status import ImportOutcome
from miramedia.storage.states import DisplayedHealthState, ListFilterState
from miramedia.torrents.schemas import Quality

StorageMediaType = Literal["show", "movie"]

FRESHNESS_NOTE = (
    "Mismatch 'detected' timestamps are last import attempt, not audit time. "
    "SHA1 unknown until verify_imported_files_task baselines a row."
)

MISSING_SUMMARY_NOTE = (
    "Missing files are shown on title pages and in row detail when a directory "
    "is readable."
)


class StorageHealthLibraryProbe(BaseModel):
    name: str
    kind: StorageMediaType
    path: str
    ok: bool
    error: str | None = None


class StorageVolume(BaseModel):
    label: str
    path: str
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    error: str | None = None


class StorageHealthCounts(BaseModel):
    imported: int
    healthy: int
    unknown: int
    corrupt: int
    orphaned: int
    pending: int
    missing: None = None


class StorageHealthSummary(BaseModel):
    generated_at: datetime
    integrity_check_enabled: bool
    integrity_check_interval_hours: int
    freshness_note: str
    counts: StorageHealthCounts
    libraries: list[StorageHealthLibraryProbe]
    unconfigured_library_names: list[str]
    volumes: list[StorageVolume]


class StorageHealthFile(BaseModel):
    file_id: UUID
    media_type: StorageMediaType
    media_id: UUID
    media_title: str
    episode: str | None
    library: str
    quality: Quality
    variant_tag: str
    import_status: ImportOutcome
    import_error: str | None
    sha1: str | None
    imported_at: datetime | None
    last_attempt_at: datetime | None
    torrent_id: UUID | None
    state: DisplayedHealthState
    path: str | None


class PaginatedStorageHealthFiles(BaseModel):
    items: list[StorageHealthFile]
    total: int
    offset: int
    limit: int
    next_offset: int | None = None


class StorageHealthListQuery(BaseModel):
    offset: int = Field(ge=0, default=0)
    limit: int
    state: ListFilterState | None = None
    media_type: StorageMediaType | None = None
    q: str | None = None
