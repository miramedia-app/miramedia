"""Feed source cursor and observation persistence."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.feeds.models import FeedItem, FeedSource
from miramedia.feeds.schemas import FeedDecision, FeedEnvelope, FeedSourceKey

log = logging.getLogger(__name__)

_FEED_ITEM_RETENTION_DAYS = 14
_FEED_ITEM_RETENTION_MIN_ROWS = 5000
_POLL_LEASE_SECONDS = 120
_IDENTITY_LOOKUP_CHUNK_SIZE = 500

IdentityKind = Literal["guid", "info_hash", "url"]


@dataclass(frozen=True, slots=True)
class FeedItemIdentity:
    kind: IdentityKind
    value: str


@dataclass(frozen=True, slots=True)
class FeedObservationInsert:
    envelope: FeedEnvelope
    download_url_redacted: str
    bound_media_type: str | None
    bound_media_id: UUID | None
    decision: FeedDecision
    score: int | None


def feed_item_identity(
    envelope: FeedEnvelope, download_url_redacted: str
) -> FeedItemIdentity:
    if envelope.provider_guid:
        return FeedItemIdentity("guid", envelope.provider_guid)
    if envelope.info_hash:
        return FeedItemIdentity("info_hash", envelope.info_hash)
    return FeedItemIdentity("url", download_url_redacted)


def _identity_from_row(
    provider_guid: str | None,
    info_hash: str | None,
    download_url_redacted: str | None,
) -> FeedItemIdentity | None:
    if provider_guid:
        return FeedItemIdentity("guid", provider_guid)
    if info_hash:
        return FeedItemIdentity("info_hash", info_hash)
    if download_url_redacted:
        return FeedItemIdentity("url", download_url_redacted)
    return None


def _chunked(values: Sequence[str], chunk_size: int) -> Iterable[Sequence[str]]:
    for offset in range(0, len(values), chunk_size):
        yield values[offset : offset + chunk_size]


@dataclass(frozen=True, slots=True)
class FeedSourceClaim:
    """Immutable claim snapshot usable after the lease transaction commits."""

    id: UUID
    backend: str
    indexer_key: str
    protocol: str
    watermark_pub_date: datetime | None
    watermark_guid: str | None
    lease_owner: str


class FeedRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert_source(
        self,
        *,
        backend: str,
        indexer_key: str,
        protocol: str,
        enabled: bool = True,
    ) -> FeedSource:
        stmt = (
            insert(FeedSource)
            .values(
                id=uuid.uuid4(),
                backend=backend,
                indexer_key=indexer_key,
                protocol=protocol,
                enabled=enabled,
            )
            .on_conflict_do_update(
                index_elements=["backend", "indexer_key"],
                set_={
                    "protocol": protocol,
                    "enabled": enabled,
                    "updated_at": func.now(),
                },
            )
            .returning(FeedSource)
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def disable_sources_not_in(
        self, backend: str, indexer_keys: set[str]
    ) -> None:
        if not indexer_keys:
            stmt = (
                update(FeedSource)
                .where(FeedSource.backend == backend)
                .values(enabled=False, updated_at=func.now())
            )
        else:
            stmt = (
                update(FeedSource)
                .where(
                    FeedSource.backend == backend,
                    FeedSource.indexer_key.not_in(indexer_keys),
                )
                .values(enabled=False, updated_at=func.now())
            )
        await self.db.execute(stmt)

    async def claim_source(self, lease_owner: str) -> FeedSourceClaim | None:
        lease_until = datetime.now(UTC) + timedelta(seconds=_POLL_LEASE_SECONDS)
        stmt = text(
            """
            SELECT id FROM feed_source
            WHERE enabled = true
              AND (lease_until IS NULL OR lease_until < NOW())
            ORDER BY last_success_at NULLS FIRST
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        row = (await self.db.execute(stmt)).first()
        if row is None:
            return None
        source_id = row[0]
        await self.db.execute(
            update(FeedSource)
            .where(FeedSource.id == source_id)
            .values(
                lease_owner=lease_owner,
                lease_until=lease_until,
                updated_at=func.now(),
            )
        )
        source = await self.db.get(FeedSource, source_id)
        if source is None:
            return None
        return FeedSourceClaim(
            id=source.id,
            backend=source.backend,
            indexer_key=source.indexer_key,
            protocol=source.protocol,
            watermark_pub_date=source.watermark_pub_date,
            watermark_guid=source.watermark_guid,
            lease_owner=lease_owner,
        )

    async def renew_lease(self, source_id: UUID, *, lease_owner: str) -> bool:
        lease_until = datetime.now(UTC) + timedelta(seconds=_POLL_LEASE_SECONDS)
        result = cast(
            CursorResult,
            await self.db.execute(
                update(FeedSource)
                .where(
                    FeedSource.id == source_id,
                    FeedSource.lease_owner == lease_owner,
                )
                .values(lease_until=lease_until, updated_at=func.now())
            ),
        )
        return (result.rowcount or 0) > 0

    async def release_lease(self, source_id: UUID, *, lease_owner: str) -> bool:
        result = cast(
            CursorResult,
            await self.db.execute(
                update(FeedSource)
                .where(
                    FeedSource.id == source_id,
                    FeedSource.lease_owner == lease_owner,
                )
                .values(lease_owner=None, lease_until=None, updated_at=func.now())
            ),
        )
        return (result.rowcount or 0) > 0

    async def record_poll_success(
        self,
        source_id: UUID,
        *,
        lease_owner: str,
        watermark_pub_date: datetime | None,
        watermark_guid: str | None,
    ) -> bool:
        result = cast(
            CursorResult,
            await self.db.execute(
                update(FeedSource)
                .where(
                    FeedSource.id == source_id,
                    FeedSource.lease_owner == lease_owner,
                )
                .values(
                    watermark_pub_date=watermark_pub_date,
                    watermark_guid=watermark_guid,
                    consecutive_failures=0,
                    last_success_at=func.now(),
                    last_error=None,
                    lease_owner=None,
                    lease_until=None,
                    updated_at=func.now(),
                )
            ),
        )
        return (result.rowcount or 0) > 0

    async def record_poll_hold(
        self,
        source_id: UUID,
        *,
        lease_owner: str,
        reason: str,
        increment_failures: bool = True,
    ) -> bool:
        values = {
            "last_error": reason[:500],
            "lease_owner": None,
            "lease_until": None,
            "updated_at": func.now(),
        }
        if increment_failures:
            values["consecutive_failures"] = FeedSource.consecutive_failures + 1
        result = cast(
            CursorResult,
            await self.db.execute(
                update(FeedSource)
                .where(
                    FeedSource.id == source_id,
                    FeedSource.lease_owner == lease_owner,
                )
                .values(**values)
            ),
        )
        return (result.rowcount or 0) > 0

    async def item_exists(
        self,
        source_id: UUID,
        envelope: FeedEnvelope,
        download_url_redacted: str,
    ) -> bool:
        if envelope.provider_guid:
            stmt = select(FeedItem.id).where(
                FeedItem.source_id == source_id,
                FeedItem.provider_guid == envelope.provider_guid,
            )
            return (await self.db.execute(stmt)).first() is not None
        if envelope.info_hash:
            stmt = select(FeedItem.id).where(
                FeedItem.source_id == source_id,
                FeedItem.info_hash == envelope.info_hash,
            )
            return (await self.db.execute(stmt)).first() is not None
        stmt = select(FeedItem.id).where(
            FeedItem.source_id == source_id,
            FeedItem.download_url_redacted == download_url_redacted,
        )
        return (await self.db.execute(stmt)).first() is not None

    async def lookup_existing_identities(
        self,
        source_id: UUID,
        identities: Sequence[FeedItemIdentity],
    ) -> set[FeedItemIdentity]:
        guids = sorted({i.value for i in identities if i.kind == "guid"})
        info_hashes = sorted({i.value for i in identities if i.kind == "info_hash"})
        urls = sorted({i.value for i in identities if i.kind == "url"})

        existing: set[FeedItemIdentity] = set()
        for chunk in _chunked(guids, _IDENTITY_LOOKUP_CHUNK_SIZE):
            stmt = select(FeedItem.provider_guid).where(
                FeedItem.source_id == source_id,
                FeedItem.provider_guid.in_(chunk),
            )
            for guid in (await self.db.execute(stmt)).scalars():
                if guid is not None:
                    existing.add(FeedItemIdentity("guid", guid))
        for chunk in _chunked(info_hashes, _IDENTITY_LOOKUP_CHUNK_SIZE):
            stmt = select(FeedItem.info_hash).where(
                FeedItem.source_id == source_id,
                FeedItem.info_hash.in_(chunk),
            )
            for info_hash in (await self.db.execute(stmt)).scalars():
                if info_hash is not None:
                    existing.add(FeedItemIdentity("info_hash", info_hash))
        for chunk in _chunked(urls, _IDENTITY_LOOKUP_CHUNK_SIZE):
            stmt = select(FeedItem.download_url_redacted).where(
                FeedItem.source_id == source_id,
                FeedItem.download_url_redacted.in_(chunk),
            )
            for url in (await self.db.execute(stmt)).scalars():
                if url is not None:
                    existing.add(FeedItemIdentity("url", url))
        return existing

    async def bulk_insert_observations(
        self,
        source_id: UUID,
        observations: Sequence[FeedObservationInsert],
    ) -> set[FeedItemIdentity]:
        if not observations:
            return set()

        values = []
        for observation in observations:
            envelope = observation.envelope
            result = envelope.result
            values.append(
                {
                    "id": uuid.uuid4(),
                    "source_id": source_id,
                    "provider_guid": envelope.provider_guid,
                    "info_hash": envelope.info_hash,
                    "download_url_redacted": observation.download_url_redacted,
                    "title": result.title,
                    "size": result.size,
                    "indexer": result.indexer,
                    "usenet": result.usenet,
                    "seeders": result.seeders,
                    "age": result.age,
                    "imdb_id": envelope.imdb_id,
                    "tmdb_id": envelope.tmdb_id,
                    "tvdb_id": envelope.tvdb_id,
                    "bound_media_type": observation.bound_media_type,
                    "bound_media_id": observation.bound_media_id,
                    "decision": observation.decision.value,
                    "score": observation.score,
                    "first_seen_at": func.now(),
                    "decided_at": func.now(),
                    "attempt_count": 1,
                }
            )

        inserted: set[FeedItemIdentity] = set()
        for offset in range(0, len(values), _IDENTITY_LOOKUP_CHUNK_SIZE):
            chunk_values = values[offset : offset + _IDENTITY_LOOKUP_CHUNK_SIZE]
            stmt = (
                insert(FeedItem)
                .values(chunk_values)
                .on_conflict_do_nothing()
                .returning(
                    FeedItem.provider_guid,
                    FeedItem.info_hash,
                    FeedItem.download_url_redacted,
                )
            )
            try:
                rows = (await self.db.execute(stmt)).all()
            except IntegrityError:
                await self.db.rollback()
                continue
            for row in rows:
                identity = _identity_from_row(row[0], row[1], row[2])
                if identity is not None:
                    inserted.add(identity)
        return inserted

    async def insert_item(
        self,
        source_id: UUID,
        envelope: FeedEnvelope,
        *,
        download_url_redacted: str,
        bound_media_type: str | None,
        bound_media_id: UUID | None,
        decision: FeedDecision,
        score: int | None,
    ) -> bool:
        """Insert observation row; return False if duplicate."""
        result = envelope.result
        values = {
            "id": uuid.uuid4(),
            "source_id": source_id,
            "provider_guid": envelope.provider_guid,
            "info_hash": envelope.info_hash,
            "download_url_redacted": download_url_redacted,
            "title": result.title,
            "size": result.size,
            "indexer": result.indexer,
            "usenet": result.usenet,
            "seeders": result.seeders,
            "age": result.age,
            "imdb_id": envelope.imdb_id,
            "tmdb_id": envelope.tmdb_id,
            "tvdb_id": envelope.tvdb_id,
            "bound_media_type": bound_media_type,
            "bound_media_id": bound_media_id,
            "decision": decision.value,
            "score": score,
            "first_seen_at": func.now(),
            "decided_at": func.now(),
            "attempt_count": 1,
        }
        stmt = insert(FeedItem).values(**values).on_conflict_do_nothing()
        try:
            result_proxy = cast(CursorResult, await self.db.execute(stmt))
        except IntegrityError:
            await self.db.rollback()
            return False
        else:
            return (result_proxy.rowcount or 0) > 0

    async def purge_stale_items(self, source_id: UUID | None = None) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=_FEED_ITEM_RETENTION_DAYS)
        deleted = 0
        source_ids: list[UUID]
        if source_id is not None:
            source_ids = [source_id]
        else:
            rows = (await self.db.execute(select(FeedSource.id))).all()
            source_ids = [row[0] for row in rows]

        for sid in source_ids:
            count_stmt = (
                select(func.count())
                .select_from(FeedItem)
                .where(FeedItem.source_id == sid)
            )
            total = (await self.db.execute(count_stmt)).scalar_one()
            if total <= _FEED_ITEM_RETENTION_MIN_ROWS:
                age_stmt = delete(FeedItem).where(
                    FeedItem.source_id == sid,
                    FeedItem.first_seen_at < cutoff,
                )
                res = cast(CursorResult, await self.db.execute(age_stmt))
                deleted += int(res.rowcount or 0)
                continue
            # Keep newest 5k; delete older than cutoff among remainder.
            age_stmt = delete(FeedItem).where(
                FeedItem.source_id == sid,
                FeedItem.first_seen_at < cutoff,
            )
            res = cast(CursorResult, await self.db.execute(age_stmt))
            deleted += int(res.rowcount or 0)
            count_after = (await self.db.execute(count_stmt)).scalar_one()
            if count_after > _FEED_ITEM_RETENTION_MIN_ROWS:
                excess = count_after - _FEED_ITEM_RETENTION_MIN_ROWS
                oldest_ids = (
                    await self.db.execute(
                        select(FeedItem.id)
                        .where(FeedItem.source_id == sid)
                        .order_by(FeedItem.first_seen_at.asc())
                        .limit(excess)
                    )
                ).all()
                if oldest_ids:
                    res = cast(
                        CursorResult,
                        await self.db.execute(
                            delete(FeedItem).where(
                                FeedItem.id.in_([r[0] for r in oldest_ids])
                            )
                        ),
                    )
                    deleted += int(res.rowcount or 0)
        return deleted

    async def get_source_by_key(self, key: FeedSourceKey) -> FeedSource | None:
        stmt = select(FeedSource).where(
            FeedSource.backend == key.backend,
            FeedSource.indexer_key == key.indexer_key,
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def lease_owner_id() -> str:
        return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
