"""UUID ↔ Sonarr/Radarr integer ID mapping for the Bazarr integration shim."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base


class ArrIdMap(Base):
    __tablename__ = "arr_id_map"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_uuid", name="uq_arr_id_map_entity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    entity_type: Mapped[str]  # series | episode | episode_file | movie | movie_file
    entity_uuid: Mapped[UUID]


def _uuids_missing_from_map(
    uuids: Sequence[UUID],
    existing: dict[UUID, int],
) -> list[UUID]:
    """Return UUIDs from *uuids* that are not yet present in *existing*."""
    return [uuid for uuid in uuids if uuid not in existing]


async def get_or_create_arr_ids(
    db: AsyncSession,
    entity_type: str,
    uuids: Sequence[UUID],
) -> dict[UUID, int]:
    """Batch-resolve or allocate integer arr IDs for the given UUIDs."""
    if not uuids:
        return {}

    unique_uuids = list(dict.fromkeys(uuids))

    result = await db.execute(
        select(ArrIdMap).where(
            ArrIdMap.entity_type == entity_type,
            ArrIdMap.entity_uuid.in_(unique_uuids),
        )
    )
    mapping = {row.entity_uuid: row.id for row in result.scalars()}

    missing = _uuids_missing_from_map(unique_uuids, mapping)
    if missing:
        stmt = insert(ArrIdMap.__table__).values(
            [{"entity_type": entity_type, "entity_uuid": uuid} for uuid in missing]
        )
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_arr_id_map_entity",
        )
        await db.execute(stmt)

        result = await db.execute(
            select(ArrIdMap).where(
                ArrIdMap.entity_type == entity_type,
                ArrIdMap.entity_uuid.in_(missing),
            )
        )
        for row in result.scalars():
            mapping[row.entity_uuid] = row.id

    return mapping


async def resolve_arr_id(
    db: AsyncSession,
    entity_type: str,
    arr_id: int,
) -> UUID | None:
    """Reverse lookup: arr integer ID → MiraMedia UUID."""
    return await db.scalar(
        select(ArrIdMap.entity_uuid).where(
            ArrIdMap.entity_type == entity_type,
            ArrIdMap.id == arr_id,
        )
    )
