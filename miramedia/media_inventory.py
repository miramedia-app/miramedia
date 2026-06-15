"""Small persisted file inventory used by streaming hot paths.

The import pipeline remains the source of truth for *expected* files. This
table is a best-effort read model for files we have already resolved on disk so
subsequent playback/subtitle requests can skip directory scans.

Re-keyed by the surrogate ``file_id`` of the owning ``EpisodeFile`` /
``MovieFile`` row (the only stable identity now that naming is split into
codec/hdr/source/variant/extra). The lookup tuple is ``(file_id, kind,
language)`` so the same row can cache its primary video plus any sidecar
subtitle paths.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, Index, UniqueConstraint, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base


class MediaFileInventory(Base):
    __tablename__ = "media_file_inventory"
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "kind",
            "language",
            name="uq_media_file_inventory_lookup",
        ),
        Index(
            "ix_media_file_inventory_lookup",
            "file_id",
            "kind",
            "language",
        ),
        Index("ix_media_file_inventory_path", "path", unique=True),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    file_id: Mapped[UUID]
    # Free-form context columns. Stored for diagnostics only — NOT part of the
    # unique key.
    media_type: Mapped[str] = mapped_column(default="")
    kind: Mapped[str] = mapped_column(default="video")
    language: Mapped[str] = mapped_column(default="")
    path: Mapped[str]
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime_ns: Mapped[int] = mapped_column(BigInteger, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


async def find_inventory_path(
    db: AsyncSession,
    *,
    file_id: UUID,
    kind: str = "video",
    language: str = "",
) -> Path | None:
    stmt = select(MediaFileInventory).where(
        MediaFileInventory.file_id == file_id,
        MediaFileInventory.kind == kind,
        MediaFileInventory.language == (language or ""),
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    path = Path(row.path)
    try:
        stat = path.stat()  # noqa: ASYNC240 — cheap stat, intentional
    except OSError:
        await db.execute(
            delete(MediaFileInventory).where(MediaFileInventory.id == row.id)
        )
        await db.flush()
        return None
    if stat.st_size != row.size_bytes or stat.st_mtime_ns != row.mtime_ns:
        row.size_bytes = stat.st_size
        row.mtime_ns = stat.st_mtime_ns
        row.last_seen_at = datetime.now(UTC)
    return path


async def upsert_inventory_path(
    db: AsyncSession,
    *,
    file_id: UUID,
    kind: str = "video",
    language: str = "",
    media_type: str = "",
    path: Path,
) -> None:
    try:
        stat = path.stat()  # noqa: ASYNC240 — cheap stat, intentional
    except OSError:
        return

    from sqlalchemy.dialects.postgresql import insert

    now = datetime.now(UTC)
    path_str = str(path)

    # The table carries two unique constraints: the lookup tuple
    # (uq_media_file_inventory_lookup) and a unique index on `path`
    # (ix_media_file_inventory_path). The lookup-tuple upsert below cannot see a
    # path conflict, so if this on-disk path is already recorded under a
    # different lookup tuple (e.g. a re-import produced a new file row, or two
    # rows resolving to one hardlinked file) a plain insert would violate the
    # path index. Reclaim that row by rewriting its lookup keys in place instead.
    existing = (
        await db.execute(
            select(MediaFileInventory).where(MediaFileInventory.path == path_str)
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Drop any other row already holding the target lookup tuple so
        # rewriting the path row's keys cannot trip uq_media_file_inventory_lookup.
        await db.execute(
            delete(MediaFileInventory).where(
                MediaFileInventory.file_id == file_id,
                MediaFileInventory.kind == kind,
                MediaFileInventory.language == (language or ""),
                MediaFileInventory.id != existing.id,
            )
        )
        existing.file_id = file_id
        existing.media_type = media_type
        existing.kind = kind
        existing.language = language or ""
        existing.size_bytes = stat.st_size
        existing.mtime_ns = stat.st_mtime_ns
        existing.last_seen_at = now
        await db.flush()
        return

    payload = {
        "id": uuid.uuid4(),
        "file_id": file_id,
        "media_type": media_type,
        "kind": kind,
        "language": language or "",
        "path": path_str,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "last_seen_at": now,
    }
    stmt = insert(MediaFileInventory.__table__).values(**payload)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_media_file_inventory_lookup",
        set_={
            "path": payload["path"],
            "media_type": payload["media_type"],
            "size_bytes": payload["size_bytes"],
            "mtime_ns": payload["mtime_ns"],
            "last_seen_at": payload["last_seen_at"],
        },
    )
    await db.execute(stmt)
    await db.flush()
