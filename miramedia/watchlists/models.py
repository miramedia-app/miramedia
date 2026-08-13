import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
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


class Watchlist(Base):
    __tablename__ = "watchlist"
    __table_args__ = (
        Index(
            "uq_watchlist_user_name_lower",
            "user_id",
            text("lower(name)"),
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_item"
    __table_args__ = (
        CheckConstraint(
            "(movie_id IS NOT NULL AND show_id IS NULL AND episode_id IS NULL) "
            "OR (movie_id IS NULL AND show_id IS NOT NULL AND episode_id IS NULL) "
            "OR (movie_id IS NULL AND show_id IS NULL AND episode_id IS NOT NULL)",
            name="watchlist_item_media_xor",
        ),
        Index(
            "uq_watchlist_item_list_position",
            "watchlist_id",
            "position",
            unique=True,
        ),
        Index(
            "uq_watchlist_item_list_movie",
            "watchlist_id",
            "movie_id",
            unique=True,
            postgresql_where=text("movie_id IS NOT NULL"),
        ),
        Index(
            "uq_watchlist_item_list_show",
            "watchlist_id",
            "show_id",
            unique=True,
            postgresql_where=text("show_id IS NOT NULL"),
        ),
        Index(
            "uq_watchlist_item_list_episode",
            "watchlist_id",
            "episode_id",
            unique=True,
            postgresql_where=text("episode_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    watchlist_id: Mapped[UUID] = mapped_column(
        ForeignKey("watchlist.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    movie_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("movie.id", ondelete="CASCADE"), nullable=True
    )
    show_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("show.id", ondelete="CASCADE"), nullable=True
    )
    episode_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
