import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from miramedia.exceptions import NotFoundError
from miramedia.requests.models import MediaRequest
from miramedia.requests.schemas import (
    MediaRequest as MediaRequestSchema,
)
from miramedia.requests.schemas import (
    MediaRequestId,
    MediaType,
    RequestSource,
    RequestStatus,
)

log = logging.getLogger(__name__)


class RequestRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    @staticmethod
    def _to_schema(r: MediaRequest) -> MediaRequestSchema:
        schema = MediaRequestSchema.model_validate(r)
        schema.requested_by_username = r.requested_by.email if r.requested_by else None
        return schema

    async def get_request(self, request_id: MediaRequestId) -> MediaRequestSchema:
        stmt = (
            select(MediaRequest)
            .where(MediaRequest.id == request_id)
            .options(joinedload(MediaRequest.requested_by))
        )
        result = (await self.db.execute(stmt)).scalars().unique().first()
        if not result:
            msg = f"Request with id {request_id} not found."
            raise NotFoundError(msg)
        return self._to_schema(result)

    async def get_requests(
        self,
        status: RequestStatus | None = None,
        media_type: MediaType | None = None,
        requested_by_id: UUID | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[MediaRequestSchema]:
        stmt = (
            select(MediaRequest)
            .options(joinedload(MediaRequest.requested_by))
            .order_by(MediaRequest.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if status is not None:
            stmt = stmt.where(MediaRequest.status == status)
        if media_type is not None:
            stmt = stmt.where(MediaRequest.media_type == media_type)
        if requested_by_id is not None:
            stmt = stmt.where(MediaRequest.requested_by_id == requested_by_id)
        results = (await self.db.execute(stmt)).scalars().unique().all()
        return [self._to_schema(r) for r in results]

    async def get_pending_count(self) -> int:
        stmt = select(func.count(MediaRequest.id)).where(
            MediaRequest.status == RequestStatus.pending
        )
        return (await self.db.execute(stmt)).scalar() or 0

    async def get_approved_not_downloaded(self) -> list[MediaRequestSchema]:
        # Include ``downloading`` so the fulfillment task keeps polling
        # in-flight requests to completion. Fresh ``approved`` ones get
        # dispatched (add + indexer fan-out); ``downloading`` ones are only
        # re-checked for completion, not re-fanned-out — that's what stops the
        # task re-running a 7-site search for the same request every cycle.
        stmt = (
            select(MediaRequest)
            .where(
                MediaRequest.status.in_(
                    [RequestStatus.approved, RequestStatus.downloading]
                )
            )
            .order_by(MediaRequest.created_at.asc())
        )
        results = (await self.db.execute(stmt)).scalars().unique().all()
        return [MediaRequestSchema.model_validate(r) for r in results]

    async def save_request(self, request: MediaRequestSchema) -> MediaRequestSchema:
        db_request = MediaRequest(
            id=request.id,
            media_type=request.media_type,
            title=request.title,
            external_id=request.external_id,
            imdb_id=request.imdb_id,
            metadata_provider=request.metadata_provider,
            movie_id=request.movie_id,
            show_id=request.show_id,
            season_number=request.season_number,
            status=request.status,
            wanted_quality=request.wanted_quality,
            requested_by_id=request.requested_by_id,
            decided_by_id=request.decided_by_id,
            note=request.note,
            source=request.source,
            tmdb_id=request.tmdb_id,
            seerr_request_id=request.seerr_request_id,
            seerr_media_id=request.seerr_media_id,
        )
        self.db.add(db_request)
        await self.db.commit()
        return MediaRequestSchema.model_validate(db_request)

    async def get_by_seerr_request_id(
        self, seerr_request_id: int
    ) -> MediaRequestSchema | None:
        stmt = select(MediaRequest).where(
            MediaRequest.seerr_request_id == seerr_request_id
        )
        result = (await self.db.execute(stmt)).scalars().first()
        return MediaRequestSchema.model_validate(result) if result else None

    async def list_native_unsynced(self) -> list[MediaRequestSchema]:
        """Native-origin rows not yet pushed to Seerr (no seerr_request_id)."""
        stmt = select(MediaRequest).where(
            MediaRequest.source == RequestSource.native,
            MediaRequest.seerr_request_id.is_(None),
            MediaRequest.status.in_([RequestStatus.pending, RequestStatus.approved]),
        )
        results = (await self.db.execute(stmt)).scalars().unique().all()
        return [MediaRequestSchema.model_validate(r) for r in results]

    async def upsert_seerr_request(
        self, request: MediaRequestSchema
    ) -> MediaRequestSchema:
        """Insert or update a mirror row keyed on seerr_request_id."""
        existing = (
            (
                await self.db.execute(
                    select(MediaRequest).where(
                        MediaRequest.seerr_request_id == request.seerr_request_id
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            db_request = MediaRequest(
                id=request.id,
                media_type=request.media_type,
                title=request.title,
                external_id=request.external_id,
                imdb_id=request.imdb_id,
                metadata_provider=request.metadata_provider,
                movie_id=request.movie_id,
                show_id=request.show_id,
                season_number=request.season_number,
                status=request.status,
                wanted_quality=request.wanted_quality,
                requested_by_id=request.requested_by_id,
                decided_by_id=request.decided_by_id,
                note=request.note,
                source=request.source,
                tmdb_id=request.tmdb_id,
                seerr_request_id=request.seerr_request_id,
                seerr_media_id=request.seerr_media_id,
            )
            self.db.add(db_request)
            await self.db.commit()
            return MediaRequestSchema.model_validate(db_request)

        # Seerr is authoritative for status/availability on existing mirror
        # rows; preserve MM-only fields (movie_id/show_id linkage).
        existing.title = request.title
        existing.imdb_id = request.imdb_id or existing.imdb_id
        existing.external_id = request.external_id or existing.external_id
        existing.status = request.status
        existing.tmdb_id = request.tmdb_id or existing.tmdb_id
        existing.seerr_media_id = request.seerr_media_id
        existing.source = request.source
        existing.updated_at = datetime.now(UTC)
        await self.db.commit()
        return MediaRequestSchema.model_validate(existing)

    async def set_imdb_id(
        self, request_id: MediaRequestId, imdb_id: str
    ) -> MediaRequestSchema:
        stmt = (
            update(MediaRequest)
            .where(MediaRequest.id == request_id)
            .values(imdb_id=imdb_id, updated_at=datetime.now(UTC))
        )
        result = await self.db.execute(stmt)
        if result.rowcount == 0:
            msg = f"Request with id {request_id} not found."
            raise NotFoundError(msg)
        await self.db.commit()
        return await self.get_request(request_id)

    async def update_request(
        self,
        request_id: MediaRequestId,
        *,
        status: RequestStatus | None = None,
        wanted_quality: int | None = ...,
        note: str | None = ...,
        decided_by_id: UUID | None = ...,
        movie_id: UUID | None = ...,
        show_id: UUID | None = ...,
        tmdb_id: int | None = ...,
        seerr_request_id: int | None = ...,
        seerr_media_id: int | None = ...,
        source: RequestSource | None = None,
    ) -> MediaRequestSchema:
        values: dict = {"updated_at": datetime.now(UTC)}
        if status is not None:
            values["status"] = status
        if wanted_quality is not ...:
            values["wanted_quality"] = wanted_quality
        if note is not ...:
            values["note"] = note
        if decided_by_id is not ...:
            values["decided_by_id"] = decided_by_id
        if movie_id is not ...:
            values["movie_id"] = movie_id
        if show_id is not ...:
            values["show_id"] = show_id
        if tmdb_id is not ...:
            values["tmdb_id"] = tmdb_id
        if seerr_request_id is not ...:
            values["seerr_request_id"] = seerr_request_id
        if seerr_media_id is not ...:
            values["seerr_media_id"] = seerr_media_id
        if source is not None:
            values["source"] = source

        stmt = (
            update(MediaRequest).where(MediaRequest.id == request_id).values(**values)
        )
        result = await self.db.execute(stmt)
        if result.rowcount == 0:
            msg = f"Request with id {request_id} not found."
            raise NotFoundError(msg)
        await self.db.commit()
        return await self.get_request(request_id)

    async def delete_request(self, request_id: MediaRequestId) -> None:
        db_request = await self.db.get(MediaRequest, request_id)
        if not db_request:
            msg = f"Request with id {request_id} not found."
            raise NotFoundError(msg)
        await self.db.delete(db_request)
        await self.db.commit()
