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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from miramedia.database import Base
from miramedia.requests.schemas import MediaType, RequestSource, RequestStatus


class MediaRequest(Base):
    __tablename__ = "media_request"
    __table_args__ = (
        # A movie request must not carry a show_id and vice versa. Both may
        # still be NULL before the request is fulfilled and the library item
        # is created.
        CheckConstraint(
            "(media_type = 'movie' AND show_id IS NULL) "
            "OR (media_type = 'show' AND movie_id IS NULL)",
            name="media_request_type_matches_fk",
        ),
        # DB-side enum validation — Python enum alone is insufficient when
        # the column is plain VARCHAR and other clients can write directly.
        CheckConstraint(
            "status IN ('pending','approved','downloading','downloaded','rejected')",
            name="media_request_status_valid",
        ),
        CheckConstraint(
            "media_type IN ('movie','show')",
            name="media_request_media_type_valid",
        ),
        CheckConstraint(
            "source IN ('native','seerr')",
            name="media_request_source_valid",
        ),
        Index("ix_media_request_status", "status"),
        Index("ix_media_request_status_media_type", "status", "media_type"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    media_type: Mapped[MediaType] = mapped_column(String(10))
    title: Mapped[str]
    external_id: Mapped[str]
    imdb_id: Mapped[str | None] = mapped_column(default=None)
    metadata_provider: Mapped[str] = mapped_column(default="")

    movie_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("movie.id", ondelete="SET NULL"), default=None, index=True
    )
    show_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("show.id", ondelete="SET NULL"), default=None, index=True
    )
    season_number: Mapped[int | None] = mapped_column(Integer, default=None)

    status: Mapped[RequestStatus] = mapped_column(
        String(20), default=RequestStatus.pending
    )
    wanted_quality: Mapped[int | None] = mapped_column(Integer, default=None)

    requested_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), default=None, index=True
    )
    decided_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), default=None, index=True
    )

    requested_by = relationship(
        "User",
        foreign_keys=[requested_by_id],
        lazy="joined",
        viewonly=True,
    )

    note: Mapped[str | None] = mapped_column(default=None)

    source: Mapped[RequestSource] = mapped_column(
        String(10), default=RequestSource.native, server_default="native"
    )
    tmdb_id: Mapped[int | None] = mapped_column(Integer, default=None)
    # UNIQUE already creates an index; no separate ``index=True`` needed.
    seerr_request_id: Mapped[int | None] = mapped_column(
        Integer, default=None, unique=True
    )
    seerr_media_id: Mapped[int | None] = mapped_column(Integer, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
