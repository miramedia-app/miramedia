import logging
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, status

from miramedia.config import MiraMediaConfig
from miramedia.database import DbSessionDependency
from miramedia.requests.backends.composite import CompositeRequestProvider
from miramedia.requests.backends.native import NativeRequestProvider
from miramedia.requests.backends.seerr import SeerrClient, SeerrError
from miramedia.requests.repository import RequestRepository
from miramedia.requests.service import RequestService

log = logging.getLogger(__name__)


def require_requests_enabled() -> None:
    """Gate runtime endpoints on the ``requests.enabled`` config flag.

    The router is mounted unconditionally so its schemas always appear in
    the generated OpenAPI spec; this dependency enforces that the feature
    is actually active before any request hits a handler.
    """
    if not MiraMediaConfig().requests.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Requests feature is disabled",
        )


def build_seerr_client() -> SeerrClient | None:
    """Construct a Seerr client when the integration is configured.

    Returns None (rather than raising) on misconfiguration so the native
    path keeps working even if Seerr settings are incomplete.
    """
    seerr = MiraMediaConfig().requests.seerr
    if not seerr.enabled:
        return None
    try:
        return SeerrClient(seerr.url, seerr.api_key)
    except SeerrError:
        log.warning("Seerr enabled but misconfigured; running native-only")
        return None


def get_request_repository(
    db_session: DbSessionDependency,
) -> RequestRepository:
    return RequestRepository(db_session)


request_repository_dep = Annotated[RequestRepository, Depends(get_request_repository)]


async def get_request_service(
    request_repository: request_repository_dep,
) -> AsyncGenerator[RequestService]:
    """Yield-style dep so the Seerr ``AsyncClient`` is closed at request end.

    Previously this returned the service and leaked the ``httpx.Client``
    once per request. Async migration of ``SeerrClient`` made the leak
    visible (``aclose`` must be awaited), so we now own teardown via
    FastAPI's generator-dependency lifecycle.
    """
    native = NativeRequestProvider(request_repository)
    client = build_seerr_client()
    provider = CompositeRequestProvider(native, request_repository, client)
    try:
        yield RequestService(provider)
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                log.exception("Failed to close Seerr client in request dep")


request_service_dep = Annotated[RequestService, Depends(get_request_service)]
