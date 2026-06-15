from abc import ABC, abstractmethod
from uuid import UUID

from miramedia.requests.schemas import (
    MediaRequest,
    MediaRequestCount,
    MediaRequestCreate,
    MediaRequestId,
    MediaRequestUpdate,
    MediaType,
    RequestStatus,
)


class AbstractRequestProvider(ABC):
    @abstractmethod
    async def create_request(
        self, data: MediaRequestCreate, requested_by_id: UUID, auto_approve: bool
    ) -> MediaRequest:
        """Create a new media request."""

    @abstractmethod
    async def list_requests(
        self,
        status: RequestStatus | None = None,
        media_type: MediaType | None = None,
        requested_by_id: UUID | None = None,
    ) -> list[MediaRequest]:
        """List requests with optional filters."""

    @abstractmethod
    async def get_request(self, request_id: MediaRequestId) -> MediaRequest:
        """Get a single request by ID."""

    @abstractmethod
    async def update_request(
        self, request_id: MediaRequestId, data: MediaRequestUpdate, user_id: UUID
    ) -> MediaRequest:
        """Update a request's quality or note."""

    @abstractmethod
    async def approve_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        """Approve a pending request."""

    @abstractmethod
    async def reject_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        """Reject a pending request."""

    @abstractmethod
    async def delete_request(self, request_id: MediaRequestId) -> None:
        """Delete a request."""

    @abstractmethod
    async def get_pending_count(self) -> MediaRequestCount:
        """Get the count of pending requests."""

    @abstractmethod
    async def get_approved_not_downloaded(self) -> list[MediaRequest]:
        """Get all approved requests that haven't been downloaded yet."""

    @abstractmethod
    async def mark_downloading(self, request_id: MediaRequestId) -> MediaRequest:
        """Mark a request as currently downloading."""

    @abstractmethod
    async def mark_downloaded(self, request_id: MediaRequestId) -> MediaRequest:
        """Mark a request as downloaded."""

    @abstractmethod
    async def set_imdb_id(
        self, request_id: MediaRequestId, imdb_id: str
    ) -> MediaRequest:
        """Backfill the IMDb ID on an existing request."""
