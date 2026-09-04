import logging
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.indexers.backends.native import invalidate_native_indexer
from miramedia.indexers.mirror_state import (
    apply_user_update,
    derive_available_urls,
    load_entries,
    mirrors_from_urls,
)
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
    MirrorEntry,
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
        # A user-created site's mirrors are all deletable (source="user"), with
        # the active URL first. ``available_urls`` is derived from them.
        mirrors = mirrors_from_urls(
            list(dump.get("available_urls") or []),
            dump["url"],
            source="user",
        )
        dump["available_urls"] = derive_available_urls(mirrors)
        dump["mirrors"] = [m.model_dump() for m in mirrors]
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

        # Snapshot the stored mirror list before applying scalar changes so the
        # seeded/user classification is read from storage, not the client.
        existing_mirrors = load_entries(site.mirrors, site.available_urls, site.url)

        update_data = data.model_dump(exclude_none=True)
        # Mirror fields are reconciled separately (see below), not blindly set.
        update_data.pop("mirrors", None)
        update_data.pop("available_urls", None)
        for key, value in update_data.items():
            setattr(site, key, value)

        new_url = data.url if data.url is not None else site.url
        incoming: list[MirrorEntry] | None = None
        if data.mirrors is not None:
            incoming = data.mirrors
        elif data.available_urls is not None:
            # Legacy flat list: full replace, all enabled, order as given. The
            # reconcile still enforces that seeded mirrors cannot be dropped.
            incoming = [
                MirrorEntry(url=u, enabled=True, source="user")
                for u in data.available_urls
            ]

        reconciled: list[MirrorEntry] | None = None
        if incoming is not None:
            reconciled = apply_user_update(existing_mirrors, incoming, new_url)
        elif data.url is not None and new_url not in derive_available_urls(
            existing_mirrors
        ):
            # Active URL changed to an endpoint that isn't an enabled mirror
            # (e.g. a Torznab URL edit): make it the active user mirror while
            # keeping any existing mirrors.
            reconciled = apply_user_update(
                existing_mirrors,
                [
                    MirrorEntry(url=new_url, enabled=True, source="user"),
                    *existing_mirrors,
                ],
                new_url,
            )

        if reconciled is not None:
            site.mirrors = [m.model_dump() for m in reconciled]
            site.available_urls = derive_available_urls(reconciled)

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
