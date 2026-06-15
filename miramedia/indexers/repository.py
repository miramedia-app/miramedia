import logging
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.indexers.backends.native import invalidate_native_indexer
from miramedia.indexers.models import IndexerQueryResult, IndexerSite
from miramedia.indexers.schemas import (
    IndexerQueryResult as IndexerQueryResultSchema,
)
from miramedia.indexers.schemas import (
    IndexerQueryResultId,
    IndexerSiteCreate,
    IndexerSiteId,
    IndexerSiteRead,
    IndexerSiteUpdate,
)

log = logging.getLogger(__name__)


class IndexerRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_result(
        self, result_id: IndexerQueryResultId
    ) -> IndexerQueryResultSchema:
        row = await self.db.get(IndexerQueryResult, result_id)
        return IndexerQueryResultSchema.model_validate(row)

    async def save_result(
        self, result: IndexerQueryResultSchema
    ) -> IndexerQueryResultSchema:
        """Persist one indexer result (idempotent)."""
        saved = await self.save_results([result])
        return saved[0] if saved else result

    async def save_results(
        self, results: list[IndexerQueryResultSchema]
    ) -> list[IndexerQueryResultSchema]:
        """Idempotent batch persist — one commit per call.

        Streaming search persists each chunk as it arrives so the unified
        /download endpoint can resolve result ids while the end-of-search
        save in :class:`IndexerService` is still pending. Rows already in
        the table are skipped so a second save is a no-op.
        """
        if not results:
            return []

        ids: list[UUID] = [r.id for r in results]
        existing_ids = set(
            (
                await self.db.execute(
                    select(IndexerQueryResult.id).where(IndexerQueryResult.id.in_(ids))
                )
            )
            .scalars()
            .all()
        )
        seen_keys: set[tuple[str, int, str, str]] = set()
        for result in results:
            if result.id in existing_ids:
                continue
            # Include the download_url (magnet/.torrent link) in the dedup key so
            # genuinely distinct releases that happen to share title+size+indexer
            # (cross-seeds / re-uploads with a different hash) are each persisted.
            # The search stream sends every scored result to the client, so the
            # persisted set must stay in parity or /download fails to resolve the id.
            key = (
                result.title.strip().lower(),
                int(result.size or 0),
                (result.indexer or ""),
                str(result.download_url),
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            result_data = result.model_dump()
            result_data["download_url"] = str(result.download_url)
            self.db.add(IndexerQueryResult(**result_data))

        await self.db.commit()
        return results

    # --- IndexerSite CRUD ---

    async def get_all_sites(self) -> list[IndexerSiteRead]:
        stmt = select(IndexerSite).order_by(
            IndexerSite.priority.asc(), IndexerSite.name.asc()
        )
        result = await self.db.execute(stmt)
        sites = result.scalars().all()
        return [IndexerSiteRead.model_validate(s) for s in sites]

    async def record_site_test(self, site_id: IndexerSiteId, status_value: str) -> None:
        """Persist the result of the most recent connectivity test on the site row."""
        from datetime import UTC, datetime

        site = await self.db.get(IndexerSite, site_id)
        if site is None:
            return
        site.last_test_status = status_value
        site.last_test_at = datetime.now(UTC)
        await self.db.commit()

    async def record_site_success(self, site_id: IndexerSiteId) -> None:
        """Stamp ``last_success_at`` so users can see which sites are actually returning."""
        from datetime import UTC, datetime

        site = await self.db.get(IndexerSite, site_id)
        if site is None:
            return
        site.last_success_at = datetime.now(UTC)
        await self.db.commit()

    async def get_site(self, site_id: IndexerSiteId) -> IndexerSiteRead:
        site = await self.db.get(IndexerSite, site_id)
        if not site:
            msg = f"Indexer site {site_id} not found"
            raise ValueError(msg)
        return IndexerSiteRead.model_validate(site)

    async def create_site(self, data: IndexerSiteCreate) -> IndexerSiteRead:
        dump = data.model_dump()
        # Ensure the active URL is always in available_urls
        if not dump.get("available_urls"):
            dump["available_urls"] = [dump["url"]] if dump.get("url") else []
        elif dump["url"] not in dump["available_urls"]:
            dump["available_urls"].insert(0, dump["url"])
        site = IndexerSite(
            id=uuid4(),
            **dump,
        )
        self.db.add(site)
        await self.db.commit()
        await self.db.refresh(site)
        invalidate_native_indexer()
        return IndexerSiteRead.model_validate(site)

    async def update_site(
        self, site_id: IndexerSiteId, data: IndexerSiteUpdate
    ) -> IndexerSiteRead:
        site = await self.db.get(IndexerSite, site_id)
        if not site:
            msg = f"Indexer site {site_id} not found"
            raise ValueError(msg)

        update_data = data.model_dump(exclude_none=True)
        for key, value in update_data.items():
            setattr(site, key, value)

        await self.db.commit()
        await self.db.refresh(site)
        invalidate_native_indexer()
        return IndexerSiteRead.model_validate(site)

    async def delete_site(self, site_id: IndexerSiteId) -> None:
        site = await self.db.get(IndexerSite, site_id)
        if not site:
            msg = f"Indexer site {site_id} not found"
            raise ValueError(msg)
        await self.db.delete(site)
        await self.db.commit()
        invalidate_native_indexer()
