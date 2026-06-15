"""ORM models for the imports feature.

* ``scan_result_cache`` — persisted output of the last library scan so the imports
  page does not re-walk the filesystem on every tab switch.
* ``scan_run`` — singleton row that tracks the state of the most recent
  scan invocation (idle / running / done / error).
* ``ignored_import_path`` — library-scan paths the user has chosen to suppress.
"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base


class ScanResultCache(Base):
    __tablename__ = "scan_result_cache"
    __table_args__ = (
        Index(
            "ix_scan_result_cache_payload_status",
            text("(payload->>'status')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    directory: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ImportQueueItem(Base):
    """Unified import tab index for SQL pagination (torrent + scan rows)."""

    __tablename__ = "import_queue_item"
    __table_args__ = (
        sa.UniqueConstraint("kind", "ref_id", "tab", name="uq_import_queue_item_ref"),
        Index("ix_import_queue_item_tab_sort", "tab", "sort_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[str]  # torrent | scan
    ref_id: Mapped[str]
    tab: Mapped[str]  # review | retry | done | all
    sort_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ScanRun(Base):
    """Singleton state row. Always has ``id == "current"``."""

    __tablename__ = "scan_run"
    __table_args__ = (CheckConstraint("id = 'current'", name="scan_run_singleton"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    items_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportBatch(Base):
    """Singleton counter for the live "Importing N/M" progress toast.

    ``total`` is the cumulative number of scan rows dispatched to a background
    import worker in the current batch (grows as more are queued). It is reset
    to 0 once no scan rows remain in the "queued" state. ``done`` is derived on
    read as ``total - <queued count>``, so only this one number is persisted —
    which keeps the M durable across a page refresh.
    """

    __tablename__ = "import_batch"
    __table_args__ = (CheckConstraint("id = 'current'", name="import_batch_singleton"),)

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IgnoredImportPath(Base):
    """Library-scan paths the user has chosen to never surface as imports."""

    __tablename__ = "ignored_import_path"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
