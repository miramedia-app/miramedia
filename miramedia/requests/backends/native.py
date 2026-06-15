from uuid import UUID

from miramedia.requests.backends.abstract_request_provider import (
    AbstractRequestProvider,
)
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


class NativeRequestProvider(AbstractRequestProvider):
    def __init__(self, repository: RequestRepository) -> None:
        self.repository = repository

    async def create_request(
        self, data: MediaRequestCreate, requested_by_id: UUID, auto_approve: bool
    ) -> MediaRequest:
        request = MediaRequest(
            media_type=data.media_type,
            title=data.title,
            external_id=data.external_id,
            imdb_id=data.imdb_id,
            metadata_provider=data.metadata_provider,
            movie_id=data.movie_id,
            show_id=data.show_id,
            season_number=data.season_number,
            wanted_quality=data.wanted_quality,
            note=data.note,
            requested_by_id=requested_by_id,
            status=RequestStatus.approved if auto_approve else RequestStatus.pending,
        )
        return await self.repository.save_request(request)

    async def list_requests(
        self,
        status: RequestStatus | None = None,
        media_type: MediaType | None = None,
        requested_by_id: UUID | None = None,
    ) -> list[MediaRequest]:
        return await self.repository.get_requests(
            status=status, media_type=media_type, requested_by_id=requested_by_id
        )

    async def get_request(self, request_id: MediaRequestId) -> MediaRequest:
        return await self.repository.get_request(request_id)

    async def update_request(
        self,
        request_id: MediaRequestId,
        data: MediaRequestUpdate,
        user_id: UUID,  # noqa: ARG002 — required by AbstractRequestProvider interface
    ) -> MediaRequest:
        kwargs: dict = {}
        if data.wanted_quality is not None:
            kwargs["wanted_quality"] = data.wanted_quality
        if data.note is not None:
            kwargs["note"] = data.note
        return await self.repository.update_request(request_id, **kwargs)

    async def approve_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        return await self.repository.update_request(
            request_id,
            status=RequestStatus.approved,
            decided_by_id=decided_by_id,
        )

    async def reject_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        return await self.repository.update_request(
            request_id,
            status=RequestStatus.rejected,
            decided_by_id=decided_by_id,
        )

    async def delete_request(self, request_id: MediaRequestId) -> None:
        await self.repository.delete_request(request_id)

    async def get_pending_count(self) -> MediaRequestCount:
        return MediaRequestCount(pending=await self.repository.get_pending_count())

    async def get_approved_not_downloaded(self) -> list[MediaRequest]:
        return await self.repository.get_approved_not_downloaded()

    async def mark_downloading(self, request_id: MediaRequestId) -> MediaRequest:
        return await self.repository.update_request(
            request_id, status=RequestStatus.downloading
        )

    async def mark_downloaded(self, request_id: MediaRequestId) -> MediaRequest:
        return await self.repository.update_request(
            request_id, status=RequestStatus.downloaded
        )

    async def set_imdb_id(
        self, request_id: MediaRequestId, imdb_id: str
    ) -> MediaRequest:
        return await self.repository.set_imdb_id(request_id, imdb_id)
