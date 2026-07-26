"""Bidirectional reconciliation between MiraMedia and Seerr.

``pull`` mirrors every Seerr request into the ``media_request`` table so the
existing read paths, scheduler and frontend treat Seerr-origin requests
exactly like native ones. ``push`` forwards native-origin requests up to
Seerr so the two stay in agreement.

The DB mirror is the single source of truth for reads; Seerr is the source
of truth for the status/availability of Seerr-origin rows.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx

from miramedia.requests.backends.seerr import (
    SEERR_MEDIA_AVAILABLE,
    SEERR_MEDIA_PARTIALLY_AVAILABLE,
    SEERR_REQ_APPROVED,
    SEERR_REQ_DECLINED,
    SeerrClient,
    SeerrRequest,
)
from miramedia.requests.repository import RequestRepository
from miramedia.requests.schemas import (
    MediaRequest,
    MediaRequestId,
    MediaType,
    RequestSource,
    RequestStatus,
)

log = logging.getLogger(__name__)


def map_seerr_status(req: SeerrRequest) -> RequestStatus:
    if req.media_status in (SEERR_MEDIA_PARTIALLY_AVAILABLE, SEERR_MEDIA_AVAILABLE):
        return RequestStatus.downloaded
    if req.request_status == SEERR_REQ_DECLINED:
        return RequestStatus.rejected
    if req.request_status == SEERR_REQ_APPROVED:
        return RequestStatus.approved
    return RequestStatus.pending


async def resolve_tmdb(
    repository: RequestRepository,
    client: SeerrClient,
    row: MediaRequest,
) -> int | None:
    """Best-effort TMDB id for a request, cached on the row.

    Order: cached ``tmdb_id`` -> native TMDB provider_id -> IMDb id resolved
    through Seerr's own TMDB proxy (no separate API key). On success the id
    is persisted so the lookup happens at most once per request.
    """
    from miramedia.database import release_session_before_external_io

    if row.tmdb_id is not None:
        return row.tmdb_id

    if row.metadata_provider == "tmdb" and row.external_id.isdigit():
        tmdb_id = int(row.external_id)
        await repository.update_request(row.id, tmdb_id=tmdb_id)
        return tmdb_id

    imdb_id = row.imdb_id or (
        row.external_id if row.external_id.startswith("tt") else None
    )
    if imdb_id:
        # Release session before async Seerr HTTP so the asyncpg conn
        # doesn't sit idle-in-TX through provider latency. (The HTTP
        # itself is non-blocking via httpx.AsyncClient.)
        await release_session_before_external_io(repository.db)
        found = await client.find_tmdb_by_imdb(imdb_id)
        if found is not None:
            tmdb_id, _media_type = found
            await repository.update_request(row.id, tmdb_id=tmdb_id)
            return tmdb_id
    return None


# Process-wide: SeerrSyncService is instantiated per task run, so the lock that
# serializes reconcile must live at module scope (bound to the broker worker
# loop both callers share).
_reconcile_lock = asyncio.Lock()


class SeerrSyncService:
    def __init__(self, repository: RequestRepository, client: SeerrClient) -> None:
        self.repository = repository
        self.client = client

    # ---- pull: Seerr -> mirror ----------------------------------------

    async def pull(self) -> int:
        from miramedia.database import release_session_before_external_io

        # Release session before the multi-page fetch so the asyncpg
        # conn isn't pinned idle-in-TX through it. The HTTP itself is
        # async (httpx.AsyncClient) and does not block the event loop.
        await release_session_before_external_io(self.repository.db)
        try:
            seerr_requests = await self.client.iter_requests()
        except httpx.HTTPError:
            log.warning("Seerr pull failed", exc_info=True)
            return 0

        synced = 0
        for sr in seerr_requests:
            try:
                await self._upsert_one(sr)
                synced += 1
            except Exception:
                log.exception("Failed to mirror Seerr request %s", sr.request_id)
        if synced:
            log.info("Mirrored %s Seerr request(s)", synced)
        return synced

    async def _upsert_one(self, sr: SeerrRequest) -> None:
        from miramedia.database import release_session_before_external_io

        existing = await self.repository.get_by_seerr_request_id(sr.request_id)

        imdb_id = sr.imdb_id
        title = None
        if (not imdb_id or not title) and sr.tmdb_id is not None:
            # Release the session before the async Seerr HTTP call so
            # the conn isn't held through provider latency.
            await release_session_before_external_io(self.repository.db)
            title, resolved_imdb = await self.client.resolve_title_imdb(
                sr.media_type, sr.tmdb_id
            )
            imdb_id = imdb_id or resolved_imdb
        if title is None:
            title = existing.title if existing else f"{sr.media_type} {sr.tmdb_id}"

        media_type = MediaType.movie if sr.media_type == "movie" else MediaType.show
        # Native indexer fulfillment is IMDb-keyed; carry the IMDb id as the
        # external_id so the scheduler can resolve a release.
        external_id = imdb_id or (existing.external_id if existing else "") or ""
        season_number = sr.seasons[0] if len(sr.seasons) == 1 else None

        row = MediaRequest(
            id=existing.id if existing else MediaRequestId(uuid.uuid4()),
            media_type=media_type,
            title=title,
            external_id=external_id,
            imdb_id=imdb_id,
            metadata_provider="native" if imdb_id else "",
            movie_id=existing.movie_id if existing else None,
            show_id=existing.show_id if existing else None,
            season_number=season_number,
            status=map_seerr_status(sr),
            source=RequestSource.seerr,
            tmdb_id=sr.tmdb_id,
            seerr_request_id=sr.request_id,
            seerr_media_id=sr.media_id,
        )
        await self.repository.upsert_seerr_request(row)

    # ---- push: native -> Seerr ----------------------------------------

    async def push(self) -> int:
        from miramedia.database import release_session_before_external_io

        pushed = 0
        for row in await self.repository.list_native_unsynced():
            tmdb_id = await resolve_tmdb(self.repository, self.client, row)
            if tmdb_id is None:
                log.debug(
                    "Skipping Seerr push for %s: TMDB id unresolvable "
                    "(external_id=%s metadata_provider=%s imdb=%s)",
                    row.title,
                    row.external_id,
                    row.metadata_provider,
                    row.imdb_id,
                )
                continue
            try:
                media_type = "movie" if row.media_type == MediaType.movie else "tv"
                seasons = [row.season_number] if row.season_number is not None else None
                # Release before create_request. Persist the Seerr link
                # immediately after a successful create so a failed
                # approve/decline cannot cause a duplicate create on the
                # next cycle; moderation state is reconciled by the
                # regular reconcile pass.
                await release_session_before_external_io(self.repository.db)
                created = await self.client.create_request(
                    media_type, tmdb_id, seasons=seasons
                )
                if created is None:
                    continue
                await self.repository.update_request(
                    row.id,
                    seerr_request_id=created.request_id,
                    seerr_media_id=created.media_id,
                )
                pushed += 1
                # update_request re-checked out the session; release again
                # before the optional approve/decline HTTP calls.
                await release_session_before_external_io(self.repository.db)
                if row.status in (
                    RequestStatus.approved,
                    RequestStatus.downloading,
                    RequestStatus.downloaded,
                ):
                    await self.client.approve(created.request_id)
                elif row.status == RequestStatus.rejected:
                    await self.client.decline(created.request_id)
            except httpx.HTTPError:
                log.warning(
                    "Seerr push failed for request %s",
                    row.title,
                    exc_info=True,
                )
        if pushed:
            log.info("Pushed %s native request(s) to Seerr", pushed)
        return pushed

    async def reconcile(self) -> None:
        # Serialize across overlapping invocations (the hourly fulfill cron and
        # a manual approve both enqueue the same task on the broker loop).
        # Without this, two reconciles read the same `seerr_request_id IS NULL`
        # rows and both POST to Seerr → duplicate requests created upstream.
        async with _reconcile_lock:
            await self.pull()
            await self.push()
