"""Validation helpers for user-triggered scan resolve requests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from miramedia.exceptions import BadRequestError, NotFoundError
from miramedia.imports.repository import ImportsRepository
from miramedia.imports.schemas import ResolveRequest
from miramedia.media_paths import (
    PathNotDirectoryError,
    PathNotFoundError,
    PathOutsideRootsError,
    library_roots_for_media_type,
    resolve_path_within_roots,
)

_SCAN_ENTRY_NOT_FOUND = "scan entry not found"


def _nonempty(value: str | None) -> bool:
    return bool(value and value.strip())


def validate_scan_resolve_target(body: ResolveRequest) -> None:
    """Require exactly one usable scan import target before claim/dispatch."""
    has_media_id = body.media_id is not None
    has_external = _nonempty(body.external_id)
    has_provider = _nonempty(body.metadata_provider)
    has_provider_pair = has_external and has_provider
    has_partial_pair = has_external != has_provider

    if has_media_id and has_provider_pair:
        msg = "scan resolve accepts either media_id or external_id + metadata_provider, not both"
        raise BadRequestError(msg)
    if has_partial_pair:
        msg = "scan resolve requires both external_id and metadata_provider"
        raise BadRequestError(msg)
    if not has_media_id and not has_provider_pair:
        msg = "scan resolve needs either media_id or external_id + metadata_provider"
        raise BadRequestError(msg)


async def validate_scan_resolve_request(
    repository: ImportsRepository, body: ResolveRequest
) -> str:
    """Validate the exact cache row exists and its path is library-contained.

    Eligibility (status + media type) is enforced only by the atomic claim so a
    prior read cannot authorize a row that changed before dispatch.
    """
    if body.media_type is None:
        msg = "media_type required for scan resolve"
        raise BadRequestError(msg)

    cache_key = body.id
    if await repository.get_scan_cache_entry(cache_key) is None:
        raise NotFoundError(_SCAN_ENTRY_NOT_FOUND)

    roots = library_roots_for_media_type(body.media_type)
    try:
        await asyncio.to_thread(
            resolve_path_within_roots,
            Path(cache_key),
            roots,
            require_directory=True,
        )
    except PathNotFoundError as exc:
        raise NotFoundError(_SCAN_ENTRY_NOT_FOUND) from exc
    except PathNotDirectoryError as exc:
        raise NotFoundError(_SCAN_ENTRY_NOT_FOUND) from exc
    except PathOutsideRootsError as exc:
        raise NotFoundError(_SCAN_ENTRY_NOT_FOUND) from exc

    return cache_key
