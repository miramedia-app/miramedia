from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.sqltypes import BigInteger

from miramedia.database import Base
from miramedia.torrents.schemas import Quality


class IndexerQueryResult(Base):
    __tablename__ = "indexer_query_result"
    __table_args__ = (
        Index("ix_indexer_query_result_created_at", "created_at"),
        # Keep in sync with alembic c5d4e3f2a1b6 (DESC sort for native indexer UI).
        Index(
            "idx_indexer_query_result_score_seeders",
            "score",
            "seeders",
            postgresql_ops={"score": "DESC", "seeders": "DESC"},
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    title: Mapped[str]
    download_url: Mapped[str]
    seeders: Mapped[int]
    flags = mapped_column(ARRAY(String))
    quality: Mapped[Quality]
    season = mapped_column(ARRAY(Integer))
    episode = mapped_column(ARRAY(Integer))
    size = mapped_column(BigInteger)
    usenet: Mapped[bool]
    age: Mapped[int]
    score: Mapped[int] = mapped_column(default=0)
    indexer: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IndexerSite(Base):
    __tablename__ = "indexer_site"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str]
    site_type: Mapped[str] = mapped_column(String(20), default="torznab")
    url: Mapped[str]
    # Derived, backward-compat view: the enabled mirror URLs in order. Kept in
    # sync from ``mirrors`` on every write; consumed by the live search, the
    # connectivity probe, and older clients.
    available_urls: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    # Source of truth for the mirror list: an ordered list of
    # ``{"url", "enabled", "source"}`` dicts. ``source`` is "seeded" (code-
    # shipped, undeletable) or "user" (deletable). See indexers/mirror_state.py.
    mirrors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    api_key: Mapped[str] = mapped_column(default="")
    supports_tv: Mapped[bool] = mapped_column(default=True)
    supports_movies: Mapped[bool] = mapped_column(default=True)
    categories_tv: Mapped[str] = mapped_column(default="5000")
    categories_movies: Mapped[str] = mapped_column(default="2000")
    cloudflare_protected: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    is_preloaded: Mapped[bool] = mapped_column(default=False)
    # Lower priority searches first when results are merged. Default 100 keeps existing
    # rows ordered by name (no behavioural change until users start re-ranking).
    priority: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_test_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
