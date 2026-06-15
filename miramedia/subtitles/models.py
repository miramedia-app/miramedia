from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base


class SubtitleRecord(Base):
    __tablename__ = "subtitle_record"
    __table_args__ = (
        CheckConstraint(
            "(episode_id IS NULL) <> (movie_id IS NULL)",
            name="subtitle_record_episode_xor_movie",
        ),
        Index("ix_subtitle_record_episode_id", "episode_id"),
        Index("ix_subtitle_record_movie_id", "movie_id"),
        Index("ix_subtitle_record_episode_language", "episode_id", "language"),
        Index("ix_subtitle_record_movie_language", "movie_id", "language"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    media_type: Mapped[str]  # "movie" or "episode"
    episode_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), nullable=True
    )
    movie_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("movie.id", ondelete="CASCADE"), nullable=True
    )
    language: Mapped[str]  # ISO 639-1
    source: Mapped[str]  # "native", "bazarr", "torrent_import", "manual"
    provider: Mapped[str | None] = mapped_column(nullable=True)
    downloaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
