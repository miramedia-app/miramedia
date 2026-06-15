"""HTTP routes for the unified imports view.

Mounted at ``/api/v1/imports``. Wraps existing torrent-import + library-scan
internals; does not duplicate their persistence logic.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from miramedia.auth.users import current_active_user, current_superuser
from miramedia.imports.dependencies import imports_repository_dep, imports_service_dep
from miramedia.imports.schemas import (
    IgnoreRequest,
    ImportCounts,
    ImportTab,
    PaginatedImports,
    ResolveRequest,
    ResolveResult,
    ScanRunStatus,
    ScanTriggerResult,
)

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get(
    "",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_active_user)],
)
async def list_imports(
    service: imports_service_dep,
    tab: Annotated[ImportTab, Query()] = ImportTab.review,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(gt=0, le=200)] = 50,
) -> PaginatedImports:
    return await service.list_imports(tab=tab, offset=offset, limit=limit)


@router.get(
    "/counts",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_active_user)],
)
async def get_counts(service: imports_service_dep) -> ImportCounts:
    return await service.get_counts()


@router.post(
    "/resolve",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(current_superuser)],
)
async def resolve_item(
    body: ResolveRequest,
    repository: imports_repository_dep,
) -> ResolveResult:
    """Queue an import resolution. Returns immediately; the worker performs
    the create + link work in the background so the user can keep
    interacting with the imports page while imports run."""
    from miramedia.imports.tasks import resolve_import_task

    if body.kind == "scan":
        await repository.mark_scan_cache_queued(body.id)
    await resolve_import_task.kiq(body.model_dump(mode="json"))
    return ResolveResult(ok=True, detail="queued")


@router.post(
    "/ignore",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def ignore_item(
    body: IgnoreRequest, service: imports_service_dep
) -> ResolveResult:
    return await service.ignore(body)


@router.post(
    "/scan",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(current_superuser)],
)
async def trigger_scan(service: imports_service_dep) -> ScanTriggerResult:
    return await service.trigger_scan()


@router.get(
    "/scan/status",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_active_user)],
)
async def get_scan_status(service: imports_service_dep) -> ScanRunStatus:
    return await service.get_scan_status()


@router.delete(
    "/scan/cache",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(current_superuser)],
)
async def clear_scan_cache(repository: imports_repository_dep) -> ResolveResult:
    """Wipe the persisted ``scan_result_cache`` table.

    Lets the user recover from a poisoned cache (e.g. terminal ``imported``
    rows left behind by a prior bad auto-import sweep) without dropping into
    psql. After clearing, the next library scan rebuilds the cache from
    scratch and any previously-stuck Pending / Imported entries re-evaluate
    against the current matching logic.
    """
    await repository.replace_scan_cache([])
    return ResolveResult(ok=True, detail="cleared")
