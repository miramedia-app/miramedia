from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.logs.models import ActivityLog


class LogRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_paginated(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        level: str | None = None,
        module: str | None = None,
        search: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[list[ActivityLog], int]:
        stmt = select(ActivityLog)
        count_stmt = select(func.count()).select_from(ActivityLog)

        if level:
            stmt = stmt.where(ActivityLog.level == level.upper())
            count_stmt = count_stmt.where(ActivityLog.level == level.upper())
        if module:
            stmt = stmt.where(ActivityLog.module.ilike(f"%{module}%"))
            count_stmt = count_stmt.where(ActivityLog.module.ilike(f"%{module}%"))
        if search:
            stmt = stmt.where(ActivityLog.message.ilike(f"%{search}%"))
            count_stmt = count_stmt.where(ActivityLog.message.ilike(f"%{search}%"))
        if start:
            stmt = stmt.where(ActivityLog.timestamp >= start)
            count_stmt = count_stmt.where(ActivityLog.timestamp >= start)
        if end:
            stmt = stmt.where(ActivityLog.timestamp <= end)
            count_stmt = count_stmt.where(ActivityLog.timestamp <= end)

        total = await self.db.scalar(count_stmt) or 0
        result = await self.db.scalars(
            stmt.order_by(ActivityLog.timestamp.desc()).offset(offset).limit(limit)
        )
        return list(result.all()), total

    async def delete_older_than(self, cutoff: datetime) -> int:
        stmt = delete(ActivityLog).where(ActivityLog.timestamp < cutoff)
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount

    async def delete_all(self) -> int:
        stmt = delete(ActivityLog)
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount

    async def iter_filtered(
        self,
        *,
        level: str | None = None,
        module: str | None = None,
        search: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        max_rows: int = 100_000,
    ) -> AsyncIterator[ActivityLog]:
        """Stream filtered logs newest-first for export. Capped at ``max_rows`` to avoid
        accidental multi-GB downloads."""
        stmt = select(ActivityLog)
        if level:
            stmt = stmt.where(ActivityLog.level == level.upper())
        if module:
            stmt = stmt.where(ActivityLog.module.ilike(f"%{module}%"))
        if search:
            stmt = stmt.where(ActivityLog.message.ilike(f"%{search}%"))
        if start:
            stmt = stmt.where(ActivityLog.timestamp >= start)
        if end:
            stmt = stmt.where(ActivityLog.timestamp <= end)
        stmt = stmt.order_by(ActivityLog.timestamp.desc()).limit(max_rows)
        result = await self.db.stream_scalars(stmt)
        async for row in result:
            yield row
