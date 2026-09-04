import uuid
from datetime import date, datetime, time
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from miramedia.database import Base
from miramedia.file_status import ImportOutcome
from miramedia.torrents.models import Quality


def _default_list_progress_status() -> str:
    from miramedia.media_state import ProgressStatus

    return ProgressStatus.none


class Show(Base):
    __tablename__ = "show"
    __table_args__ = (
        UniqueConstraint("external_id", "metadata_provider"),
        Index("ix_show_imdb_id", "imdb_id"),
        Index(
            "ix_show_auto_download_backoff",
            "auto_download_backoff_until",
            postgresql_where=text("auto_download_backoff_until IS NOT NULL"),
        ),
        Index(
            "ix_show_metadata_failure_backoff",
            "metadata_failure_backoff_until",
            postgresql_where=text("metadata_failure_backoff_until IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    external_id: Mapped[str]
    metadata_provider: Mapped[str]
    name: Mapped[str]
    overview: Mapped[str]
    year: Mapped[int | None]
    ended: Mapped[bool] = mapped_column(default=False)
    continuous_download: Mapped[bool | None] = mapped_column(
        default=None, nullable=True
    )
    library: Mapped[str] = mapped_column(default="")
    original_language: Mapped[str | None] = mapped_column(default=None)

    imdb_id: Mapped[str | None] = mapped_column(default=None)

    preferred_quality: Mapped[list[str] | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    preferred_codec: Mapped[list[str] | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    subtitle_languages: Mapped[list[str] | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    skipped: Mapped[bool] = mapped_column(default=False, index=True)
    vote_average: Mapped[float | None] = mapped_column(default=None)
    content_rating: Mapped[str | None] = mapped_column(default=None)
    genres: Mapped[list[str] | None] = mapped_column(JSONB, default=None, nullable=True)
    cast: Mapped[list[str] | None] = mapped_column(JSONB, default=None, nullable=True)
    last_metadata_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    metadata_failure_backoff_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    # See miramedia.movies.models.Movie.auto_download_backoff_until.
    auto_download_backoff_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    wanted_episode_count: Mapped[int] = mapped_column(default=0)
    downloaded_episode_count: Mapped[int] = mapped_column(default=0)
    # complete | partial | none — mirrors frontend grid status filters.
    list_progress_status: Mapped[str] = mapped_column(
        default=_default_list_progress_status, index=True
    )

    seasons: Mapped[list["Season"]] = relationship(
        back_populates="show", cascade="all, delete", order_by="Season.number"
    )


class Season(Base):
    __tablename__ = "season"
    __table_args__ = (UniqueConstraint("show_id", "number"),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    show_id: Mapped[UUID] = mapped_column(
        ForeignKey(column="show.id", ondelete="CASCADE"),
        index=True,
    )
    number: Mapped[int]
    skipped: Mapped[bool] = mapped_column(default=False, index=True)

    show: Mapped["Show"] = relationship(back_populates="seasons")
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="season", cascade="all, delete", order_by="Episode.number"
    )


class Episode(Base):
    __tablename__ = "episode"
    __table_args__ = (UniqueConstraint("season_id", "number"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    season_id: Mapped[UUID] = mapped_column(
        ForeignKey("season.id", ondelete="CASCADE"),
        index=True,
    )
    number: Mapped[int]
    title: Mapped[str]
    overview: Mapped[str | None] = mapped_column(nullable=True)
    skipped: Mapped[bool] = mapped_column(default=False, index=True)
    air_date: Mapped[date | None] = mapped_column(Date, default=None, nullable=True)
    # Local air time-of-day (same configured zone as air_date), when a provider
    # supplies a datetime (Cinemeta 'released', TVMaze 'airstamp'). NULL when the
    # provider gives only a date. Display-only — the scheduler keys off air_date.
    air_time: Mapped[time | None] = mapped_column(Time, default=None, nullable=True)
    downloaded: Mapped[bool] = mapped_column(default=False, index=True)

    season: Mapped["Season"] = relationship(back_populates="episodes")
    episode_files = relationship(
        "EpisodeFile", back_populates="episode", cascade="all, delete"
    )


class EpisodeFile(Base):
    __tablename__ = "episode_file"
    __table_args__ = (
        # Surrogate PK; natural identity (and on-disk filename uniqueness) is
        # enforced by the unique constraint on the filename-determining tuple.
        UniqueConstraint(
            "episode_id",
            "quality",
            "codec",
            "variant",
            "extra",
            name="uq_episode_file_naming",
        ),
        Index(
            "ix_episode_file_sha1_pending",
            "episode_id",
            postgresql_where=text("sha1 IS NULL AND import_status = 'imported'"),
        ),
        Index(
            "ix_episode_file_import_status_pending",
            "import_status",
            postgresql_where=text("import_status <> 'imported'"),
        ),
        Index(
            "ix_episode_file_orphaned_failed",
            "import_status",
            postgresql_where=text(
                "torrent_id IS NULL AND import_status IN "
                "('failed_io', 'failed_no_match')"
            ),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    episode_id: Mapped[UUID] = mapped_column(
        ForeignKey(column="episode.id", ondelete="CASCADE"),
        index=True,
    )
    torrent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(column="torrent.id", ondelete="SET NULL"),
        index=True,
    )
    source_info_hash: Mapped[str | None] = mapped_column(
        String(40), default=None, nullable=True, index=True
    )
    quality: Mapped[Quality]
    # Distinguishing components (see miramedia/torrents/quality_naming.py).
    codec: Mapped[str] = mapped_column(default="")
    hdr: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(default="")
    variant: Mapped[str] = mapped_column(default="")  # user-entered
    extra: Mapped[str] = mapped_column(default="")  # collision discriminator
    import_status: Mapped[ImportOutcome] = mapped_column(
        default=ImportOutcome.pending, index=True
    )
    import_error: Mapped[str | None] = mapped_column(default=None, nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    sha1: Mapped[str | None] = mapped_column(default=None, nullable=True)

    torrent = relationship("Torrent", back_populates="episode_files", uselist=False)
    episode = relationship("Episode", back_populates="episode_files", uselist=False)
