import logging

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.settings.models import SystemConfigOverride
from miramedia.settings.normalize import normalize_stored_overrides
from miramedia.settings.validation import sanitize_persisted_overrides

log = logging.getLogger(__name__)

SINGLETON_ID = 1


class SettingsRevisionConflictError(Exception):
    """Optimistic concurrency check failed for settings persistence."""

    def __init__(self, expected_revision: int, actual_revision: int) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            f"Settings revision conflict: expected {expected_revision}, "
            f"found {actual_revision}"
        )


class SettingsRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def _fetch_row(self) -> SystemConfigOverride | None:
        return await self.db.get(SystemConfigOverride, SINGLETON_ID)

    async def get_overrides(self) -> dict:
        row = await self._fetch_row()
        if row is None:
            return {}
        return normalize_stored_overrides(row.overrides or {})

    async def get_overrides_with_revision(self) -> tuple[dict, int]:
        row = await self._fetch_row()
        if row is None:
            return {}, 0
        return normalize_stored_overrides(row.overrides or {}), row.revision

    async def save_overrides_cas(
        self,
        overrides: dict,
        expected_revision: int,
    ) -> tuple[dict, int]:
        sanitized = sanitize_persisted_overrides(overrides)

        if expected_revision == 0:
            stmt = (
                insert(SystemConfigOverride)
                .values(id=SINGLETON_ID, overrides=sanitized, revision=1)
                .on_conflict_do_nothing(index_elements=["id"])
                .returning(
                    SystemConfigOverride.overrides,
                    SystemConfigOverride.revision,
                )
            )
            result = await self.db.execute(stmt)
            inserted = result.one_or_none()
            if inserted is not None:
                await self.db.commit()
                return inserted.overrides, inserted.revision

            await self.db.rollback()
            fresh = await self._fetch_row()
            actual = fresh.revision if fresh is not None else 0
            raise SettingsRevisionConflictError(0, actual)

        stmt = (
            update(SystemConfigOverride)
            .where(SystemConfigOverride.id == SINGLETON_ID)
            .where(SystemConfigOverride.revision == expected_revision)
            .values(
                overrides=sanitized,
                revision=SystemConfigOverride.revision + 1,
            )
            .returning(SystemConfigOverride.overrides, SystemConfigOverride.revision)
        )
        result = await self.db.execute(stmt)
        updated = result.one_or_none()
        if updated is None:
            await self.db.rollback()
            fresh = await self._fetch_row()
            actual = fresh.revision if fresh is not None else 0
            raise SettingsRevisionConflictError(expected_revision, actual)
        await self.db.commit()
        return updated.overrides, updated.revision

    async def fetch_overrides_with_revision(self) -> tuple[dict, int]:
        """Load the current committed DB snapshot (alias for reload paths)."""
        return await self.get_overrides_with_revision()
