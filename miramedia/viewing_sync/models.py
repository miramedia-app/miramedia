"""Persistent dry-run proposals, quarantine rows, and poll cursor."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base


class ViewingSyncCursor(Base):
    __tablename__ = "viewing_sync_cursor"

    connector: Mapped[str] = mapped_column(String(32), primary_key=True)
    connector_user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    min_last_played_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ViewingSyncRun(Base):
    __tablename__ = "viewing_sync_run"
    __table_args__ = (Index("ix_viewing_sync_run_started_at", "started_at"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    connector: Mapped[str] = mapped_column(String(32), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_redacted: Mapped[str | None] = mapped_column(Text, nullable=True)


class ViewingSyncProposal(Base):
    __tablename__ = "viewing_sync_proposal"
    __table_args__ = (
        Index("ix_viewing_sync_proposal_run_id", "run_id"),
        Index(
            "ix_viewing_sync_proposal_connector_item",
            "connector",
            "connector_user_id",
            "connector_item_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("viewing_sync_run.id", ondelete="CASCADE"), nullable=False
    )
    connector: Mapped[str] = mapped_column(String(32), nullable=False)
    connector_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    miramedia_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    media_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    media_id: Mapped[UUID | None] = mapped_column(nullable=True)
    file_id: Mapped[UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    match_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    conflict_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    position_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed: Mapped[bool | None] = mapped_column(nullable=True)
    remote_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ViewingSyncQuarantine(Base):
    __tablename__ = "viewing_sync_quarantine"
    __table_args__ = (
        Index("ix_viewing_sync_quarantine_run_id", "run_id"),
        Index("ix_viewing_sync_quarantine_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("viewing_sync_run.id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    connector_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    connector_item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_ids: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    candidate_mira_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    series_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
