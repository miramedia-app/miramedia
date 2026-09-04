import typing
import uuid
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from miramedia.file_status import FileStatus, ImportOutcome
from miramedia.media_status import MediaStatus
from miramedia.torrents.models import Quality
from miramedia.torrents.schemas import RichTorrent, TorrentId

MovieId = typing.NewType("MovieId", UUID)


class Movie(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: MovieId = Field(default_factory=lambda: MovieId(uuid.uuid4()))
    name: str
    overview: str
    year: int | None
    release_date: date | None = None

    external_id: str
    metadata_provider: str
    continuous_download: bool | None = None
    quality_upgrades: bool | None = None
    upgrade_until_quality: str | None = None
    skipped: bool = False
    library: str = "Default"
    original_language: str | None = None
    imdb_id: str | None = None
    vote_average: float | None = None
    content_rating: str | None = None
    runtime: int | None = None
    genres: list[str] | None = None
    cast: list[str] | None = None

    preferred_quality: list[str] | None = None
    preferred_codec: list[str] | None = None
    subtitle_languages: list[str] | None = None
    last_metadata_check: datetime | None = None
    metadata_failure_backoff_until: datetime | None = None
    auto_download_backoff_until: datetime | None = None
    downloaded: bool = False


class MovieFile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid.uuid4)
    movie_id: MovieId
    quality: Quality
    codec: str = ""
    hdr: bool = False
    source: str = ""
    variant: str = ""  # user-entered
    extra: str = ""  # collision discriminator
    torrent_id: TorrentId | None = None
    source_info_hash: str | None = None
    import_status: ImportOutcome = ImportOutcome.pending
    import_error: str | None = None
    imported_at: datetime | None = None
    last_attempt_at: datetime | None = None
    attempt_count: int = 0
    sha1: str | None = None


class PublicMovieFile(MovieFile):
    imported: bool = False
    status: MediaStatus = MediaStatus.wanted
    file_status: FileStatus = FileStatus.queued
    file_name: str | None = None


class PublicMovie(Movie):
    downloaded: bool = False
    status: MediaStatus = MediaStatus.wanted
    skipped: bool = False
    torrents: list[RichTorrent] = []
