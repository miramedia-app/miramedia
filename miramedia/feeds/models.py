"""Persistent feed cursor and observation rows (design 385 §4)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base


class FeedSource(Base):
    __tablename__ = "feed_source"
    __table_args__ = (
        UniqueConstraint(
            "backend", "indexer_key", name="uq_feed_source_backend_indexer"
        ),
        Index("ix_feed_source_lease_until", "lease_until"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    backend: Mapped[str] = mapped_column(String(32), nullable=False)
    indexer_key: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False, default="torznab")
    enabled: Mapped[bool] = mapped_column(default=True, server_default="true")
    watermark_pub_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    watermark_guid: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class FeedItem(Base):
    __tablename__ = "feed_item"
    __table_args__ = (
        Index("ix_feed_item_source_first_seen", "source_id", "first_seen_at"),
        Index(
            "uq_feed_item_source_guid",
            "source_id",
            "provider_guid",
            unique=True,
            postgresql_where=text("provider_guid IS NOT NULL"),
        ),
        Index(
            "uq_feed_item_source_info_hash",
            "source_id",
            "info_hash",
            unique=True,
            postgresql_where=text("info_hash IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("feed_source.id", ondelete="CASCADE"), nullable=False
    )
    provider_guid: Mapped[str | None] = mapped_column(String(512), nullable=True)
    info_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    download_url_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    indexer: Mapped[str | None] = mapped_column(String(128), nullable=True)
    usenet: Mapped[bool] = mapped_column(default=False, server_default="false")
    seeders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    age: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    imdb_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tmdb_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tvdb_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    bound_media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bound_media_id: Mapped[UUID | None] = mapped_column(nullable=True)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
