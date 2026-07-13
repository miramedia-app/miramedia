"""Validation helpers for user-triggered scan resolve requests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException, status

from miramedia.imports.repository import ImportsRepository
from miramedia.imports.schemas import ResolveRequest
from miramedia.media_paths import (
    PathNotDirectoryError,
    PathNotFoundError,
    PathOutsideRootsError,
    library_roots_for_media_type,
    resolve_path_within_roots,
)


async def validate_scan_resolve_request(
    repository: ImportsRepository, body: ResolveRequest
) -> str:
    """Validate the exact cache row exists and its path is library-contained.

    Eligibility (status + media type) is enforced only by the atomic claim so a
    prior read cannot authorize a row that changed before dispatch.
    """
    if body.media_type is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "media_type required for scan resolve"
        )

    cache_key = body.id
    if await repository.get_scan_cache_entry(cache_key) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scan entry not found")

    roots = library_roots_for_media_type(body.media_type)
    try:
        await asyncio.to_thread(
            resolve_path_within_roots,
            Path(cache_key),
            roots,
            require_directory=True,
        )
    except PathNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scan entry not found") from exc
    except PathNotDirectoryError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scan entry not found") from exc
    except PathOutsideRootsError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scan entry not found") from exc

    return cache_key
