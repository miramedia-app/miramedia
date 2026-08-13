import uuid
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base


class WatchStateSource(StrEnum):
    derived = "derived"
    manual = "manual"


class PlaybackProgress(Base):
    __tablename__ = "playback_progress"
    __table_args__ = (
        CheckConstraint(
            "(movie_file_id IS NOT NULL AND episode_file_id IS NULL) "
            "OR (movie_file_id IS NULL AND episode_file_id IS NOT NULL)",
            name="playback_progress_file_xor",
        ),
        Index(
            "uq_playback_progress_user_movie_file",
            "user_id",
            "movie_file_id",
            unique=True,
            postgresql_where=text("movie_file_id IS NOT NULL"),
        ),
        Index(
            "uq_playback_progress_user_episode_file",
            "user_id",
            "episode_file_id",
            unique=True,
            postgresql_where=text("episode_file_id IS NOT NULL"),
        ),
        Index(
            "ix_playback_progress_user_updated_at",
            "user_id",
            "updated_at",
            postgresql_ops={"updated_at": "DESC"},
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    movie_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("movie_file.id", ondelete="CASCADE"), nullable=True
    )
    episode_file_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("episode_file.id", ondelete="CASCADE"), nullable=True
    )
    position_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MediaWatchState(Base):
    __tablename__ = "media_watch_state"
    __table_args__ = (
        CheckConstraint(
            "(movie_id IS NOT NULL AND episode_id IS NULL) "
            "OR (movie_id IS NULL AND episode_id IS NOT NULL)",
            name="media_watch_state_media_xor",
        ),
        CheckConstraint(
            "source IN ('derived', 'manual')",
            name="media_watch_state_source_valid",
        ),
        Index(
            "uq_media_watch_state_user_movie",
            "user_id",
            "movie_id",
            unique=True,
            postgresql_where=text("movie_id IS NOT NULL"),
        ),
        Index(
            "uq_media_watch_state_user_episode",
            "user_id",
            "episode_id",
            unique=True,
            postgresql_where=text("episode_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    movie_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("movie.id", ondelete="CASCADE"), nullable=True
    )
    episode_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), nullable=True
    )
    watched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[WatchStateSource] = mapped_column(String(10), nullable=False)
    watched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
