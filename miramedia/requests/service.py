import asyncio
import logging
from uuid import UUID

from miramedia.config import MiraMediaConfig
from miramedia.database import release_session_before_external_io
from miramedia.requests.backends.abstract_request_provider import (
    AbstractRequestProvider,
)
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


async def _resolve_imdb_id(
    media_type: MediaType, external_id: str, provider_name: str
) -> str | None:
    """Best-effort IMDb ID resolution via the named metadata provider.

    Returns None if the provider is unavailable, doesn't know the IMDb ID, or
    raises. Always called when the request was created without an IMDb ID so
    the native indexer/scheduler can later fulfill it.
    """
    if not provider_name or provider_name == "native":
        # Native external_ids ARE IMDb IDs (tt...). Nothing to resolve.
        return external_id if external_id.startswith("tt") else None

    from miramedia.metadata.dependencies import resolve_metadata_provider

    provider = resolve_metadata_provider(provider_name)
    if provider is None:
        return None
    try:
        if media_type == MediaType.movie:
            movie = await asyncio.to_thread(provider.get_movie_metadata, external_id)
            return movie.imdb_id
        show = await asyncio.to_thread(provider.get_show_metadata, external_id)
    except Exception:
        log.warning(
            "Failed to resolve IMDb ID for %s external_id=%s via provider=%s",
            media_type,
            external_id,
            provider_name,
            exc_info=True,
        )
        return None
    else:
        return show.imdb_id


async def _release_provider_session(provider: AbstractRequestProvider) -> None:
    db = getattr(getattr(provider, "repository", None), "db", None)
    if db is not None:
        # Blocking metadata-provider HTTP (timeout=60) must not hold the
        # request's connection idle-in-transaction.
        await release_session_before_external_io(db)


class RequestService:
    def __init__(self, provider: AbstractRequestProvider) -> None:
        self.provider = provider

    async def create_request(
        self,
        data: MediaRequestCreate,
        requested_by_id: UUID,
        is_superuser: bool,
    ) -> MediaRequest:
        config = MiraMediaConfig().requests
        auto_approve = is_superuser or config.auto_approve_users

        # Backfill IMDb ID server-side. TMDB/TVDB search results don't include
        # imdb_id, so requests would otherwise sit unfulfillable when the
        # native indexer (IMDb-only) is the configured download path.
        if not data.imdb_id:
            await _release_provider_session(self.provider)
            resolved = await _resolve_imdb_id(
                data.media_type, data.external_id, data.metadata_provider
            )
            if resolved:
                data = data.model_copy(update={"imdb_id": resolved})

        result = await self.provider.create_request(data, requested_by_id, auto_approve)
        log.info("New %s request created: %s", data.media_type, data.title)
        return result

    async def heal_missing_imdb_id(self, request: MediaRequest) -> MediaRequest:
        """Resolve and persist the IMDb ID on an existing request, if possible."""
        if request.imdb_id:
            return request
        await _release_provider_session(self.provider)
        resolved = await _resolve_imdb_id(
            request.media_type, request.external_id, request.metadata_provider
        )
        if not resolved:
            return request
        return await self.provider.set_imdb_id(request.id, resolved)

    async def list_requests(
        self,
        status: RequestStatus | None = None,
        media_type: MediaType | None = None,
        requested_by_id: UUID | None = None,
    ) -> list[MediaRequest]:
        return await self.provider.list_requests(
            status=status, media_type=media_type, requested_by_id=requested_by_id
        )

    async def get_request(self, request_id: MediaRequestId) -> MediaRequest:
        return await self.provider.get_request(request_id)

    async def update_request(
        self, request_id: MediaRequestId, data: MediaRequestUpdate, user_id: UUID
    ) -> MediaRequest:
        return await self.provider.update_request(request_id, data, user_id)

    async def approve_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        result = await self.provider.approve_request(request_id, decided_by_id)
        log.info("Request approved: %s (%s)", result.title, result.media_type)
        return result

    async def reject_request(
        self, request_id: MediaRequestId, decided_by_id: UUID
    ) -> MediaRequest:
        result = await self.provider.reject_request(request_id, decided_by_id)
        log.info("Request rejected: %s (%s)", result.title, result.media_type)
        return result

    async def delete_request(self, request_id: MediaRequestId) -> None:
        await self.provider.delete_request(request_id)
        log.info("Request deleted: %s", request_id)

    async def get_pending_count(self) -> MediaRequestCount:
        return await self.provider.get_pending_count()

    async def get_approved_not_downloaded(self) -> list[MediaRequest]:
        return await self.provider.get_approved_not_downloaded()

    async def mark_downloading(self, request_id: MediaRequestId) -> MediaRequest:
        return await self.provider.mark_downloading(request_id)

    async def mark_downloaded(self, request_id: MediaRequestId) -> MediaRequest:
        return await self.provider.mark_downloaded(request_id)
