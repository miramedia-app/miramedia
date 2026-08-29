"""Superuser-only read-only diagnostics routes."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette import status

from miramedia.auth.users import current_superuser
from miramedia.database import DbSessionDependency
from miramedia.diagnostics.database import get_database_diagnostics
from miramedia.diagnostics.scheduler import get_scheduler_diagnostics
from miramedia.diagnostics.schemas import DiagnosticsDatabase, DiagnosticsScheduler
from miramedia.exceptions import NotFoundError
from miramedia.storage.dependencies import storage_health_service_dep
from miramedia.storage.schemas import (
    PaginatedStorageHealthFiles,
    StorageHealthFile,
    StorageHealthSummary,
)
from miramedia.storage.states import ListFilterState
from miramedia.torrents.integrity import (
    INTEGRITY_MISMATCH_DEFAULT_LIMIT,
    INTEGRITY_MISMATCH_MAX_LIMIT,
)

router = APIRouter(
    prefix="/diagnostics",
    tags=["diagnostics"],
    dependencies=[Depends(current_superuser)],
)


@router.get("/storage", status_code=status.HTTP_200_OK)
async def get_diagnostics_storage(
    service: storage_health_service_dep,
) -> StorageHealthSummary:
    return await service.get_summary()


@router.get("/storage/files", status_code=status.HTTP_200_OK)
async def list_diagnostics_storage_files(
    service: storage_health_service_dep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[
        int, Query(gt=0, le=INTEGRITY_MISMATCH_MAX_LIMIT)
    ] = INTEGRITY_MISMATCH_DEFAULT_LIMIT,
    state: Annotated[ListFilterState | None, Query()] = None,
    media_type: Annotated[Literal["show", "movie"] | None, Query()] = None,
    q: Annotated[str | None, Query()] = None,
) -> PaginatedStorageHealthFiles:
    return await service.list_files(
        offset=offset,
        limit=limit,
        state=state,
        media_type=media_type,
        q=q,
    )


@router.get(
    "/storage/files/{media_type}/{file_id}",
    status_code=status.HTTP_200_OK,
)
async def get_diagnostics_storage_file(
    media_type: Literal["show", "movie"],
    file_id: UUID,
    service: storage_health_service_dep,
) -> StorageHealthFile:
    try:
        return await service.get_file(media_type=media_type, file_id=file_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.get("/database", status_code=status.HTTP_200_OK)
async def get_diagnostics_database(
    db: DbSessionDependency,
) -> DiagnosticsDatabase:
    return await get_database_diagnostics(db)


@router.get("/scheduler", status_code=status.HTTP_200_OK)
async def get_diagnostics_scheduler(
    db: DbSessionDependency,
) -> DiagnosticsScheduler:
    return await get_scheduler_diagnostics(db)
