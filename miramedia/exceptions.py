from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError


class RenameError(Exception):
    """Error when renaming something"""

    def __init__(self, message: str = "Failed to rename source directory") -> None:
        super().__init__(message)


class MiraMediaError(Exception):
    """Base exception for MiraMedia errors."""

    def __init__(self, message: str = "An error occurred.") -> None:
        super().__init__(message)
        self.message = message


class MediaAlreadyExistsError(MiraMediaError):
    """Raised when a media entity already exists (HTTP 409)."""

    def __init__(
        self, message: str = "Entity with this ID or other identifier already exists"
    ) -> None:
        super().__init__(message)


class MediaSkippedError(MiraMediaError):
    """Raised when a download is attempted against a media item that is
    currently marked skipped. Callers should treat this as a no-op, not an
    error — the user explicitly opted out."""

    def __init__(self, message: str = "Destination media is marked skipped") -> None:
        super().__init__(message)


class NoVideoFilesError(MiraMediaError):
    """Raised when a .torrent's file list contains no video files — e.g. an
    .exe-only release masquerading as a movie. Caught upstream so the bad
    torrent never reaches the download client."""

    def __init__(self, message: str = "Torrent contains no video files") -> None:
        super().__init__(message)


class UnsafeTorrentTitleError(MiraMediaError):
    """Raised when a torrent title cannot be used as a safe filesystem path
    component. Callers must fail closed — no writes or deletes outside the
    configured torrent root."""

    def __init__(
        self, message: str = "Torrent title is not a safe filesystem path component"
    ) -> None:
        super().__init__(message)


UNSAFE_TORRENT_TITLE_API_DETAIL = (
    "Torrent title is not a safe filesystem path component"
)


class NotFoundError(MiraMediaError):
    """Raised when an entity is not found (HTTP 404)."""

    def __init__(self, message: str = "The requested entity was not found.") -> None:
        super().__init__(message)


class InvalidConfigError(MiraMediaError):
    """Raised when the server is improperly configured (HTTP 500)."""

    def __init__(self, message: str = "The server is improperly configured.") -> None:
        super().__init__(message)


class BadRequestError(MiraMediaError):
    """Raised for invalid client requests (HTTP 400)."""

    def __init__(self, message: str = "Bad request.") -> None:
        super().__init__(message)


class UnauthorizedError(MiraMediaError):
    """Raised for authentication failures (HTTP 401)."""

    def __init__(self, message: str = "Unauthorized.") -> None:
        super().__init__(message)


class ForbiddenError(MiraMediaError):
    """Raised for forbidden actions (HTTP 403)."""

    def __init__(self, message: str = "Forbidden.") -> None:
        super().__init__(message)


class ConflictError(MiraMediaError):
    """Raised for resource conflicts (HTTP 409)."""

    def __init__(self, message: str = "Conflict.") -> None:
        super().__init__(message)


class UnprocessableEntityError(MiraMediaError):
    """Raised for validation errors (HTTP 422)."""

    def __init__(self, message: str = "Unprocessable entity.") -> None:
        super().__init__(message)


class MetadataProviderUnavailableError(MiraMediaError):
    """Raised when an external metadata provider can't be reached — DNS
    failure, connection refused, timeout (HTTP 503).

    Distinct from "no results": surfacing it as 503 lets the frontend show a
    retry affordance instead of collapsing the skeleton into an empty grid."""

    def __init__(
        self, message: str = "The metadata provider is currently unreachable."
    ) -> None:
        super().__init__(message)


# Exception handlers
async def media_already_exists_exception_handler(
    _request: Request, _exc: Exception
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(_exc)})


async def not_found_error_exception_handler(
    _request: Request, _exc: Exception
) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(_exc)})


async def invalid_config_error_exception_handler(
    _request: Request, _exc: Exception
) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(_exc)})


async def bad_request_error_handler(
    _request: Request, exc: BadRequestError
) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": exc.message})


async def unauthorized_error_handler(
    _request: Request, exc: UnauthorizedError
) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": exc.message})


async def forbidden_error_handler(
    _request: Request, exc: ForbiddenError
) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": exc.message})


async def conflict_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(_exc)})


async def unprocessable_entity_error_handler(
    _request: Request, exc: UnprocessableEntityError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.message})


async def sqlalchemy_integrity_error_handler(
    _request: Request, _exc: Exception
) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": "The entity to create already exists or is in a conflict with others."
        },
    )


async def media_skipped_error_handler(
    _request: Request, exc: MediaSkippedError
) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": exc.message})


async def no_video_files_error_handler(
    _request: Request, exc: NoVideoFilesError
) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.message})


async def unsafe_torrent_title_error_handler(
    _request: Request, _exc: UnsafeTorrentTitleError
) -> JSONResponse:
    return JSONResponse(
        status_code=422, content={"detail": UNSAFE_TORRENT_TITLE_API_DETAIL}
    )


async def metadata_provider_unavailable_handler(
    _request: Request, exc: MetadataProviderUnavailableError
) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": exc.message})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(NotFoundError, not_found_error_exception_handler)
    app.add_exception_handler(
        MediaAlreadyExistsError, media_already_exists_exception_handler
    )
    app.add_exception_handler(MediaSkippedError, media_skipped_error_handler)
    app.add_exception_handler(NoVideoFilesError, no_video_files_error_handler)
    app.add_exception_handler(
        UnsafeTorrentTitleError, unsafe_torrent_title_error_handler
    )
    app.add_exception_handler(
        InvalidConfigError, invalid_config_error_exception_handler
    )
    app.add_exception_handler(BadRequestError, bad_request_error_handler)
    app.add_exception_handler(UnauthorizedError, unauthorized_error_handler)
    app.add_exception_handler(ForbiddenError, forbidden_error_handler)
    app.add_exception_handler(ConflictError, conflict_error_handler)
    app.add_exception_handler(
        UnprocessableEntityError, unprocessable_entity_error_handler
    )
    app.add_exception_handler(
        MetadataProviderUnavailableError, metadata_provider_unavailable_handler
    )
    app.add_exception_handler(IntegrityError, sqlalchemy_integrity_error_handler)
    app.add_exception_handler(UniqueViolation, sqlalchemy_integrity_error_handler)
