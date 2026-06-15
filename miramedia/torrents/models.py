from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from miramedia.database import Base
from miramedia.torrents.schemas import Quality, TorrentStatus


class Torrent(Base):
    __tablename__ = "torrent"
    __table_args__ = (
        CheckConstraint("length(hash) = 40", name="torrent_hash_length"),
        Index("ix_torrent_status", "status"),
        Index(
            "ix_torrent_status_active",
            "status",
            postgresql_where=text("status <> 'finished'"),
        ),
        Index(
            "ix_torrent_created_at_desc",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        Index(
            "ix_torrent_created_at_id_desc",
            "created_at",
            "id",
            postgresql_ops={"created_at": "DESC", "id": "DESC"},
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    status: Mapped[TorrentStatus]
    title: Mapped[str]
    quality: Mapped[Quality]
    hash: Mapped[str] = mapped_column(String(40), unique=True)
    usenet: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    episode_files = relationship("EpisodeFile", back_populates="torrent")
    movie_files = relationship("MovieFile", back_populates="torrent")


class ManualParseToken(Base):
    __tablename__ = "manual_parse_token"
    __table_args__ = (Index("ix_manual_parse_token_created_at", "created_at"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TorrentBlock(Base):
    __tablename__ = "torrent_block"
    info_hash: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="no_video_files")
    blocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TorrentHistory(Base):
    """Durable record of every torrent we downloaded — survives cleanup.

    The live ``torrent`` table is active downloads only; ``cleanup_after_import``
    deletes those rows on success. This table is the persistent log: one row per
    downloaded torrent (keyed by ``info_hash`` while known), carrying the import
    outcome + a denormalised file/media snapshot so it outlives both the torrent
    and the media it linked to. Powers the Imports "Done" tab.
    """

    __tablename__ = "torrent_history"
    __table_args__ = (
        # One row per hash; backfilled / hash-less rows are allowed and not
        # deduped (partial unique).
        Index(
            "uq_torrent_history_info_hash",
            "info_hash",
            unique=True,
            postgresql_where=text("info_hash IS NOT NULL"),
        ),
        Index("ix_torrent_history_outcome", "outcome"),
        Index(
            "ix_torrent_history_imported_at_desc",
            "imported_at",
            postgresql_ops={"imported_at": "DESC"},
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # Soft correlation to the live torrent while it exists; not an FK so the
    # row survives the torrent being deleted.
    torrent_id: Mapped[UUID | None] = mapped_column(default=None, nullable=True)
    info_hash: Mapped[str | None] = mapped_column(
        String(40), default=None, nullable=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    quality: Mapped[Quality]
    usenet: Mapped[bool] = mapped_column(default=False)

    media_type: Mapped[str | None] = mapped_column(default=None, nullable=True)
    media_id: Mapped[UUID | None] = mapped_column(default=None, nullable=True)
    media_name: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)
    media_year: Mapped[int | None] = mapped_column(default=None, nullable=True)

    # downloaded | imported | failed | removed
    outcome: Mapped[str] = mapped_column(default="downloaded")
    files: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    files_total: Mapped[int] = mapped_column(Integer, default=0)
    files_imported: Mapped[int] = mapped_column(Integer, default=0)
    import_error: Mapped[str | None] = mapped_column(Text, default=None, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )
