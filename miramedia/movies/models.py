import uuid
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from miramedia.database import Base
from miramedia.file_status import ImportOutcome
from miramedia.torrents.models import Quality


class Movie(Base):
    __tablename__ = "movie"
    __table_args__ = (
        UniqueConstraint("external_id", "metadata_provider"),
        Index(
            "ix_movie_auto_download_backoff",
            "auto_download_backoff_until",
            postgresql_where=text("auto_download_backoff_until IS NOT NULL"),
        ),
        Index(
            "ix_movie_metadata_failure_backoff",
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
    release_date: Mapped[date | None] = mapped_column(Date, default=None, nullable=True)
    library: Mapped[str] = mapped_column(default="")
    original_language: Mapped[str | None] = mapped_column(default=None)
    imdb_id: Mapped[str | None] = mapped_column(default=None)
    continuous_download: Mapped[bool | None] = mapped_column(
        default=None, nullable=True
    )
    skipped: Mapped[bool] = mapped_column(default=False, index=True)
    vote_average: Mapped[float | None] = mapped_column(default=None)
    content_rating: Mapped[str | None] = mapped_column(default=None)
    runtime: Mapped[int | None] = mapped_column(default=None)
    genres: Mapped[list[str] | None] = mapped_column(JSONB, default=None, nullable=True)
    cast: Mapped[list[str] | None] = mapped_column(JSONB, default=None, nullable=True)

    preferred_quality: Mapped[list[str] | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    preferred_codec: Mapped[list[str] | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    subtitle_languages: Mapped[list[str] | None] = mapped_column(
        JSONB, default=None, nullable=True
    )
    last_metadata_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    metadata_failure_backoff_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    # Set when an auto-download sweep found candidates but every one was
    # deny-listed. Subsequent sweeps skip the movie until this time has
    # passed, so we stop burning indexer hits + CF bypass on releases we
    # already know are bad. Cleared the moment a non-blocked candidate
    # shows up (e.g. a real release lands).
    auto_download_backoff_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    # Denormalized: any imported movie_file row (refreshed on import/delete).
    downloaded: Mapped[bool] = mapped_column(default=False, index=True)


class MovieFile(Base):
    __tablename__ = "movie_file"
    __table_args__ = (
        # Surrogate PK; natural identity (and on-disk filename uniqueness) is
        # enforced by the unique constraint on the filename-determining tuple.
        UniqueConstraint(
            "movie_id",
            "quality",
            "codec",
            "variant",
            "extra",
            name="uq_movie_file_naming",
        ),
        Index(
            "ix_movie_file_sha1_pending",
            "movie_id",
            postgresql_where=text("sha1 IS NULL AND import_status = 'imported'"),
        ),
        Index(
            "ix_movie_file_import_status_pending",
            "import_status",
            postgresql_where=text("import_status <> 'imported'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    movie_id: Mapped[UUID] = mapped_column(
        ForeignKey(column="movie.id", ondelete="CASCADE"),
        index=True,
    )

    quality: Mapped[Quality]
    # Distinguishing components (see miramedia/torrents/quality_naming.py).
    codec: Mapped[str] = mapped_column(default="")
    hdr: Mapped[bool] = mapped_column(default=False)
    source: Mapped[str] = mapped_column(default="")
    variant: Mapped[str] = mapped_column(default="")  # user-entered
    extra: Mapped[str] = mapped_column(default="")  # collision discriminator
    torrent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(column="torrent.id", ondelete="SET NULL"),
        index=True,
    )
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

    torrent = relationship("Torrent", back_populates="movie_files", uselist=False)
