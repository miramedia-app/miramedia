import typing
import uuid
from datetime import datetime
from enum import Enum, IntEnum, StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from miramedia.file_status import ImportOutcome

TorrentId = typing.NewType("TorrentId", uuid.UUID)

T = TypeVar("T")


class Quality(IntEnum):
    # IntEnum so Pydantic v2 accepts string query params like "?quality=3"
    # (Enum would reject the string form and only accept the int 3).
    uhd = 1
    fullhd = 2
    hd = 3
    sd = 4
    unknown = 5


class QualityStrings(Enum):
    uhd = "4K"
    fullhd = "1080p"
    hd = "720p"
    sd = "400p"
    unknown = "unknown"


class TorrentStatus(Enum):
    finished = 1
    downloading = 2
    paused = 3
    error = 4
    unknown = 5


class ImportProgress(BaseModel):
    """Aggregate import status across files linked to a torrent."""

    total: int = 0
    imported: int = 0
    failed: int = 0
    ambiguous: int = 0
    pending: int = 0
    last_error: str | None = None
    last_attempt_at: datetime | None = None

    @property
    def all_imported(self) -> bool:
        return self.total > 0 and self.imported == self.total


class Torrent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: TorrentId = Field(default_factory=lambda: TorrentId(uuid.uuid4()))
    status: TorrentStatus
    progress: float = 0.0
    num_peers: int = 0
    num_seeds: int = 0
    download_speed: int = 0
    title: str
    quality: Quality
    hash: str
    usenet: bool = False


class TorrentMediaContext(BaseModel):
    """Media context for a torrent — either a show or a movie."""

    media_type: str  # "show" or "movie"
    media_id: uuid.UUID
    media_name: str
    media_year: int | None = None
    metadata_provider: str = ""
    # TV-specific
    seasons: list[int] | None = None
    episodes: list[int] | None = None


class RichTorrent(BaseModel):
    """A torrent with live download status and associated media context."""

    model_config = ConfigDict(from_attributes=True)

    id: TorrentId = Field(default_factory=lambda: TorrentId(uuid.uuid4()))
    status: TorrentStatus
    progress: float = 0.0
    num_peers: int = 0
    num_seeds: int = 0
    download_speed: int = 0
    title: str
    quality: Quality
    hash: str
    usenet: bool = False
    variant: str = ""
    media: TorrentMediaContext | None = None
    import_progress: ImportProgress = Field(default_factory=ImportProgress)


# --- Unified download types ---


class MediaType(StrEnum):
    show = "show"
    movie = "movie"


class UnifiedSearchRequest(BaseModel):
    """Query parameters for the unified torrent search endpoint."""

    media_type: MediaType
    media_id: uuid.UUID
    season_number: int | None = None
    episode_number: int | None = None
    query_override: str | None = None


class UnifiedDownloadRequest(BaseModel):
    """Body for the unified torrent download endpoint."""

    indexer_result_id: uuid.UUID
    media_type: MediaType
    media_id: uuid.UUID
    variant: str = ""
    quality_override: Quality | None = None
    # If set, the linked show/movie's library will be reassigned before linking
    # so the resulting files land under the chosen library path.
    library: str | None = None


# --- Manual add types ---


class ManualDownloadRequest(BaseModel):
    """Body for downloading a manually-parsed torrent."""

    download_token: uuid.UUID
    media_type: MediaType
    media_id: uuid.UUID
    variant: str = ""
    quality_override: Quality | None = None
    library: str | None = None


# --- Torrent history ---


class TorrentHistoryOutcome(StrEnum):
    """Lifecycle state of a downloaded torrent in ``torrent_history``."""

    downloaded = "downloaded"  # grabbed, not yet imported
    imported = "imported"  # successfully imported into the library
    failed = "failed"  # import attempted but did not fully succeed
    removed = "removed"  # torrent removed before a successful import


# --- Import status views ---


class ImportFileDetail(BaseModel):
    """Per-file detail inside an import-status entry."""

    media_label: str
    variant: str = ""
    quality: Quality
    import_status: ImportOutcome
    import_error: str | None = None
    last_attempt_at: datetime | None = None
    imported_at: datetime | None = None
    attempt_count: int = 0


class ImportStatusEntry(BaseModel):
    """One torrent + its aggregate + per-file breakdown."""

    torrent_id: TorrentId
    torrent_title: str
    torrent_status: TorrentStatus
    source_dir: str = ""
    media: TorrentMediaContext | None = None
    progress: ImportProgress
    files: list[ImportFileDetail]


# --- Manual file mapping ---


class TorrentSourceFile(BaseModel):
    """A file present on disk inside a torrent's download directory."""

    relative_path: str
    size: int
    is_video: bool
    is_subtitle: bool
    seasons: list[int] = []
    episodes: list[int] = []
    quality: Quality = Quality.unknown
    suggested_episode_id: uuid.UUID | None = None
    suggested_movie_id: uuid.UUID | None = None


class TorrentFilesResponse(BaseModel):
    """List of source files plus the torrent's media context."""

    torrent_id: TorrentId
    torrent_title: str
    media: TorrentMediaContext | None = None
    files: list[TorrentSourceFile]


class ManualMapTargetType(StrEnum):
    episode = "episode"
    movie = "movie"
    skip = "skip"


class ManualMapItem(BaseModel):
    relative_path: str
    target_type: ManualMapTargetType
    episode_id: uuid.UUID | None = None
    movie_id: uuid.UUID | None = None
    variant: str = ""
    quality_override: Quality | None = None


class ManualMapRequest(BaseModel):
    items: list[ManualMapItem]


class ManualMapResult(BaseModel):
    mapped: int
    skipped: int
    failed: int
    errors: list[str] = []


# --- Bulk / single retry-import + dry-run plan ---


class BulkRetryImportRequest(BaseModel):
    """IDs of torrents whose per-file import status should be reset + re-run."""

    torrent_ids: list[uuid.UUID] = Field(min_length=1)


class BulkRetryImportFailure(BaseModel):
    torrent_id: str
    error: str


class BulkRetryImportResult(BaseModel):
    succeeded: int = 0
    failed: list[BulkRetryImportFailure] = []


class RetryImportResult(BaseModel):
    reset: int
    progress: ImportProgress


class DryRunImportPlanItem(BaseModel):
    relative_path: str
    size: int
    is_video: bool
    is_subtitle: bool
    suggested_episode_id: str | None = None
    suggested_movie_id: str | None = None
    target_path: str | None = None
    quality: int | str | None = None


class DryRunImportResult(BaseModel):
    dry_run: bool
    torrent_id: TorrentId
    torrent_title: str
    plan: list[DryRunImportPlanItem]


# --- Integrity mismatch (SHA1 audit) ---


class IntegrityMismatch(BaseModel):
    """An imported file whose on-disk SHA1 no longer matches the stored hash."""

    file_id: uuid.UUID
    media_type: typing.Literal["show", "movie"]
    media_title: str
    episode: str | None  # "S03E07" for shows, None for movies
    path: str | None
    quality: Quality
    variant_tag: str
    import_error: str
    detected_at: datetime | None  # last_attempt_at or None


class PaginatedIntegrityMismatches(BaseModel):
    """Bounded page of integrity-mismatch rows (shows first, then movies)."""

    items: list[IntegrityMismatch]
    total: int
    offset: int
    limit: int
    next_offset: int | None = None


class IntegrityActionResult(BaseModel):
    ok: bool = True
