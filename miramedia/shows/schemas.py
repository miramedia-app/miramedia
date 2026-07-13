import typing
import uuid
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from miramedia.file_status import FileStatus, ImportOutcome
from miramedia.media_state import ProgressStatus
from miramedia.media_status import MediaStatus
from miramedia.torrents.models import Quality
from miramedia.torrents.schemas import TorrentId

ShowId = typing.NewType("ShowId", UUID)
SeasonId = typing.NewType("SeasonId", UUID)
EpisodeId = typing.NewType("EpisodeId", UUID)

SeasonNumber = typing.NewType("SeasonNumber", int)
EpisodeNumber = typing.NewType("EpisodeNumber", int)


class Episode(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: EpisodeId = Field(default_factory=lambda: EpisodeId(uuid.uuid4()))
    number: EpisodeNumber
    title: str
    overview: str | None = None
    skipped: bool = False
    air_date: date | None = None
    # Eager-loaded from ORM via Show repository so downstream services don't
    # have to round-trip per episode. Not exposed in PublicEpisode.
    episode_files: list["EpisodeFile"] = Field(default_factory=list)


class Season(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: SeasonId = Field(default_factory=lambda: SeasonId(uuid.uuid4()))
    show_id: ShowId | None = None
    number: SeasonNumber

    skipped: bool = False

    episodes: list[Episode]


class Show(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: ShowId = Field(default_factory=lambda: ShowId(uuid.uuid4()))

    name: str
    overview: str
    year: int | None

    ended: bool = False
    external_id: str
    metadata_provider: str

    continuous_download: bool | None = None
    skipped: bool = False
    library: str = "Default"
    original_language: str | None = None

    imdb_id: str | None = None
    vote_average: float | None = None
    content_rating: str | None = None
    genres: list[str] | None = None
    cast: list[str] | None = None

    preferred_quality: list[str] | None = None
    preferred_codec: list[str] | None = None
    subtitle_languages: list[str] | None = None
    last_metadata_check: datetime | None = None
    metadata_failure_backoff_until: datetime | None = None
    auto_download_backoff_until: datetime | None = None
    wanted_episode_count: int = 0
    downloaded_episode_count: int = 0
    list_progress_status: ProgressStatus = ProgressStatus.none

    seasons: list[Season] = []


class EpisodeFile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(default_factory=uuid.uuid4)
    episode_id: EpisodeId
    quality: Quality
    torrent_id: TorrentId | None
    codec: str = ""
    hdr: bool = False
    source: str = ""
    variant: str = ""  # user-entered
    extra: str = ""  # collision discriminator
    import_status: ImportOutcome = ImportOutcome.pending
    import_error: str | None = None
    imported_at: datetime | None = None
    last_attempt_at: datetime | None = None
    attempt_count: int = 0
    sha1: str | None = None


class PublicEpisodeFile(EpisodeFile):
    downloaded: bool = False
    status: MediaStatus = MediaStatus.wanted
    file_status: FileStatus = FileStatus.queued
    file_name: str | None = None


class PublicEpisode(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: EpisodeId
    number: EpisodeNumber

    downloaded: bool = False
    skipped: bool = False
    status: MediaStatus = MediaStatus.wanted
    title: str
    overview: str | None = None
    air_date: date | None = None


class PublicSeason(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: SeasonId
    number: SeasonNumber

    downloaded: bool = False
    skipped: bool = False
    status: MediaStatus = MediaStatus.wanted

    episodes: list[PublicEpisode]


class PublicShow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: ShowId

    name: str
    overview: str
    year: int | None

    external_id: str
    metadata_provider: str

    ended: bool = False
    continuous_download: bool | None = None
    skipped: bool = False
    status: MediaStatus = MediaStatus.wanted
    library: str

    preferred_quality: list[str] | None = None
    preferred_codec: list[str] | None = None
    subtitle_languages: list[str] | None = None
    original_language: str | None = None
    vote_average: float | None = None
    content_rating: str | None = None
    genres: list[str] | None = None
    cast: list[str] | None = None

    # Aggregate download progress over wanted (non-skipped) episodes. Always
    # populated. The list endpoint returns these + an EMPTY ``seasons`` so the
    # grid renders the progress badge without shipping the whole season/episode
    # tree; the detail endpoint returns the full tree.
    wanted_episode_count: int = 0
    downloaded_episode_count: int = 0

    seasons: list[PublicSeason] = []


# List endpoints return the same shape with ``seasons=[]``; alias for OpenAPI clarity.
PublicShowSummary = PublicShow


# Resolve forward references after EpisodeFile is defined.
Episode.model_rebuild()
