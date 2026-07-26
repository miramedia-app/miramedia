"""Request provider that keeps the local mirror and Seerr in lock-step.

Reads always come from the DB mirror (which the sync service keeps current
for both native- and Seerr-origin rows). Writes apply locally first, then
best-effort propagate to Seerr for rows that are linked to a Seerr request.
A Seerr outage never blocks a local operation — the next ``reconcile`` heals
any drift.
"""

from __future__ import annotations

import logging
from uuid import UUID

import httpx

from miramedia.requests.backends.abstract_request_provider import (
    AbstractRequestProvider,
)
from miramedia.requests.backends.native import NativeRequestProvider
from miramedia.requests.backends.seerr import SeerrClient
from miramedia.requests.repository import RequestRepository
from miramedia.requests.schemas import (
    MediaRequest,
    MediaRequestCount,
    MediaRequestCreate,
    MediaRequestId,
    MediaRequestUpdate,
    MediaType,
    RequestStatus,
)

log = logging.getLogger(__name__)


class CompositeRequestProvider(AbstractRequestProvider):
    def __init__(
        self,
        native: NativeRequestProvider,
        repository: RequestRepository,
        client: SeerrClient | None,
    ) -> None:
        self.native = native
        self.repository = repository
        self.client = client

    # ---- reads (DB mirror) --------------------------------------------

    async def list_requests(
        self,
        status: RequestStatus | None = None,
        media_type: MediaType | None = None,
        requested_by_id: UUID | None = None,
    ) -> list[MediaRequest]:
        return await self.native.list_requests(
            status=status,
            media_type=media_type,
            requested_by_id=requested_by_id,
        )

    async def get_request(self, request_id: MediaRequestId) -> MediaRequest:
        return await self.native.get_request(request_id)

    async def get_pending_count(self) -> MediaRequestCount:
        return await self.native.get_pending_count()

    async def get_approved_not_downloaded(self) -> list[MediaRequest]:
        return await self.native.get_approved_not_downloaded()

    # ---- writes (local first, Seerr write-through) --------------------

    async def create_request(
        self, data: MediaRequestCreate, requested_by_id: UUID, auto_approve: bool
    ) -> MediaRequest:
        request = await self.native.create_request(data, requested_by_id, auto_approve)
        if self.client is not None:
            await self._push_new(request)
        return request

    async def update_request(
        self, request_id: MediaRequestId, data: MediaRequestUpdate, user_id: UUID
    ) -> MediaRequest:
        return await self.native.update_request(request_id, data, user_id)

    async def approve_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        result = await self.native.approve_request(request_id, decided_by_id)
        if self.client is not None and result.seerr_request_id is not None:
            await self._safe_seerr(
                "approve", self.client.approve(result.seerr_request_id)
            )
        return result

    async def reject_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        result = await self.native.reject_request(request_id, decided_by_id)
        if self.client is not None and result.seerr_request_id is not None:
            await self._safe_seerr(
                "decline", self.client.decline(result.seerr_request_id)
            )
        return result

    async def delete_request(self, request_id: MediaRequestId) -> None:
        row = await self.repository.get_request(request_id)
        seerr_request_id = row.seerr_request_id
        await self.native.delete_request(request_id)
        if self.client is not None and seerr_request_id is not None:
            await self._safe_seerr(
                "delete",
                self.client.delete_request(seerr_request_id),
            )

    async def mark_downloading(self, request_id: MediaRequestId) -> MediaRequest:
        return await self.native.mark_downloading(request_id)

    async def mark_downloaded(self, request_id: MediaRequestId) -> MediaRequest:
        result = await self.native.mark_downloaded(request_id)
        if self.client is not None and result.seerr_media_id is not None:
            await self._safe_seerr(
                "mark_available",
                self.client.mark_media_available(result.seerr_media_id),
            )
        return result

    async def set_imdb_id(
        self, request_id: MediaRequestId, imdb_id: str
    ) -> MediaRequest:
        return await self.native.set_imdb_id(request_id, imdb_id)

    # ---- helpers ------------------------------------------------------

    async def _push_new(self, request: MediaRequest) -> None:
        """Forward an MM-originated request to Seerr (best effort)."""
        from miramedia.requests.sync import resolve_tmdb

        try:
            tmdb_id = await resolve_tmdb(self.repository, self.client, request)
        except httpx.HTTPError:
            tmdb_id = None
        if tmdb_id is None:
            return
        media_type = "movie" if request.media_type == MediaType.movie else "tv"
        seasons = [request.season_number] if request.season_number is not None else None
        try:
            created = await self.client.create_request(
                media_type, tmdb_id, seasons=seasons
            )
            if created is None:
                return
            if request.status in (
                RequestStatus.approved,
                RequestStatus.downloading,
                RequestStatus.downloaded,
            ):
                await self.client.approve(created.request_id)
            await self.repository.update_request(
                request.id,
                seerr_request_id=created.request_id,
                seerr_media_id=created.media_id,
            )
        except httpx.HTTPError:
            log.warning(
                "Seerr push failed for new request %s",
                request.title,
                exc_info=True,
            )

    @staticmethod
    async def _safe_seerr(action: str, coro) -> None:  # noqa: ANN001
        """Await a Seerr client coroutine, swallowing HTTPError.

        Takes the coroutine itself (not a callable) so the caller's call
        site reads naturally as ``self._safe_seerr("approve",
        self.client.approve(req_id))``. The coroutine is consumed exactly
        once — pass a freshly-created coroutine each invocation.
        """
        try:
            await coro
        except httpx.HTTPError:
            log.warning(
                "Seerr %s call failed; local state kept, will reconcile",
                action,
                exc_info=True,
            )
