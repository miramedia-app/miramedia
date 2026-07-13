"""Imports schemas.

Unifies torrent-derived import status and library-scan candidates into a
single import-review view. ``ImportItem`` is a discriminated union:
``kind == "torrent"`` wraps a per-torrent import-status entry;
``kind == "scan"`` wraps a directory discovered by the library scanner.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from miramedia.torrents.schemas import (
    ImportFileDetail,
    ImportProgress,
    ImportStatusEntry,
    MediaType,
    Quality,
)

# --- Fuzzy-match breakdown (shared by scan + manual-parse candidates) ---------


class MatchBreakdown(BaseModel):
    """Why a fuzzy-match candidate scored what it did.

    Surfaced in the UI as a "why this match?" tooltip. Mirrors the dict
    returned by ``find_candidate_media_matches``.
    """

    overlap_words: list[str] = []
    media_word_count: int = 0
    title_word_count: int = 0
    base_score: float = 0.0
    year_boost: float = 0.0


# --- Library scan candidates / results ----------------------------------------


class ScanSourceFile(BaseModel):
    """A file on disk inside a scanned directory."""

    relative_path: str
    size: int = 0
    is_video: bool = False


class ScanCandidate(BaseModel):
    """A possible existing-library media match for a scanned directory."""

    media_type: MediaType
    media_id: uuid.UUID
    media_name: str
    media_year: int | None = None
    confidence: float = 0.0
    breakdown: MatchBreakdown | None = None


class ScanProviderCandidate(BaseModel):
    """A metadata-provider search hit for a scanned directory with no (strong)
    existing-library match. Picking one creates the media in the DB, then
    imports the directory into it."""

    media_type: MediaType
    external_id: str
    metadata_provider: str
    name: str
    year: int | None = None
    overview: str | None = None
    poster_path: str | None = None
    imdb_id: str | None = None
    confidence: float = 0.0
    breakdown: MatchBreakdown | None = None


class ScanResult(BaseModel):
    """One unimported directory found during a library scan."""

    directory: str
    detected_name: str
    detected_year: int | None = None
    media_type_hint: MediaType | None = None
    library_name: str
    size_bytes: int = 0
    file_count: int = 0
    candidates: list[ScanCandidate] = []
    provider_candidates: list[ScanProviderCandidate] = []
    files: list[ScanSourceFile] = []
    # "pending" until imported; "imported" once it finished successfully;
    # "failed" if the import was attempted but did not succeed. The row stays
    # in the imports list either way (finished vs needs-attention).
    status: str = "pending"
    imported_name: str | None = None
    imported_media_id: str | None = None
    imported_media_type: MediaType | None = None
    import_error: str | None = None


class ScanResponse(BaseModel):
    items: list[ScanResult]
    ignored: list[str] = []


# --- Manual torrent-add parse candidates --------------------------------------


class ManualParseCandidate(BaseModel):
    """A possible media match for a manually-added torrent."""

    media_type: MediaType
    media_id: uuid.UUID
    media_name: str
    media_year: int | None = None
    confidence: float = 0.0
    breakdown: MatchBreakdown | None = None


class ManualParseResponse(BaseModel):
    """Result of parsing a magnet link or .torrent file."""

    download_token: uuid.UUID
    title: str
    quality: Quality
    seasons: list[int]
    episodes: list[int]
    candidates: list[ManualParseCandidate]


class ImportTab(StrEnum):
    review = "review"
    retry = "retry"
    done = "done"
    all = "all"


class TorrentImportItem(BaseModel):
    """Import row backed by a torrent + its per-file import progress."""

    kind: Literal["torrent"] = "torrent"
    id: str
    entry: ImportStatusEntry
    backoff_seconds: int | None = None


class ScanImportItem(BaseModel):
    """Import row backed by a directory found in a library root."""

    kind: Literal["scan"] = "scan"
    id: str
    result: ScanResult


class MediaImportItem(BaseModel):
    """A finished import whose torrent has been cleaned up.

    With ``cleanup_after_import`` the torrent row is deleted on success, which
    nulls ``EpisodeFile.torrent_id`` / ``MovieFile.torrent_id``. Those imported
    files would otherwise vanish from the imports page. This item rebuilds a
    torrent-independent ``Done`` row straight from the imported media files so
    the confirmation log survives cleanup. Read-only — no retry/map/ignore.
    """

    kind: Literal["media"] = "media"
    id: str  # media id (show or movie uuid)
    media_type: MediaType
    media_name: str
    media_year: int | None = None
    # Original torrent release name, preserved from ``torrent_history`` so it
    # survives ``cleanup_after_import`` deleting the live torrent row.
    torrent_title: str = ""
    source_dir: str = ""
    imported_at: datetime | None = None
    progress: ImportProgress
    files: list[ImportFileDetail]


ImportItem = Annotated[
    TorrentImportItem | ScanImportItem | MediaImportItem,
    Field(discriminator="kind"),
]


class PaginatedImports(BaseModel):
    items: list[ImportItem]
    total: int
    offset: int
    limit: int


class ImportCounts(BaseModel):
    review: int = 0
    retry: int = 0
    done: int = 0
    all: int = 0
    # Scan rows currently dispatched to a background import worker (status
    # "queued"). Drives the live "Importing N/M" progress toast on the imports
    # page so the count survives a page refresh.
    importing: int = 0
    # Cumulative rows queued in the current batch (the M in "N/M"). Grows as
    # more imports are added and resets to 0 once the batch drains. Server-side
    # + durable, so the denominator survives a refresh. Done (N) = total -
    # importing.
    import_total: int = 0


# --- Resolve / ignore ---


class TorrentResolveAction(StrEnum):
    retry = "retry"
    map = "map"


class ResolveRequest(BaseModel):
    kind: Literal["torrent", "scan"]
    id: str
    # torrent kind
    action: TorrentResolveAction | None = None
    # scan kind — either pick an existing library item (media_id) ...
    media_type: MediaType | None = None
    media_id: uuid.UUID | None = None
    # ... or create new media from a metadata-provider hit, then import
    external_id: str | None = None
    metadata_provider: str | None = None


class ResolveResult(BaseModel):
    ok: bool
    detail: str = ""


class IgnoreRequest(BaseModel):
    kind: Literal["torrent", "scan"]
    id: str
    delete_files: bool = False  # torrent kind only


# --- Scan run ---


class ScanRunState(StrEnum):
    idle = "idle"
    running = "running"
    done = "done"
    error = "error"


class ScanRunStatus(BaseModel):
    state: ScanRunState = ScanRunState.idle
    started_at: datetime | None = None
    finished_at: datetime | None = None
    items_found: int = 0
    last_error: str | None = None


class ScanTriggerResult(BaseModel):
    state: ScanRunState
    detail: str = ""
