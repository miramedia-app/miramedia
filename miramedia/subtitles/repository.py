from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.subtitles.models import SubtitleRecord


class SubtitleRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def save_record(self, record: SubtitleRecord) -> SubtitleRecord:
        merged = await self.db.merge(record)
        await self.db.flush()
        return merged

    async def get_records_by_episode_id(self, episode_id: UUID) -> list[SubtitleRecord]:
        stmt = select(SubtitleRecord).where(SubtitleRecord.episode_id == episode_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_records_by_movie_id(self, movie_id: UUID) -> list[SubtitleRecord]:
        stmt = select(SubtitleRecord).where(SubtitleRecord.movie_id == movie_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def delete_record(self, record_id: UUID) -> None:
        record = await self.db.get(SubtitleRecord, record_id)
        if record:
            await self.db.delete(record)
            await self.db.flush()
