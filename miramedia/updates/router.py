from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette import status

from miramedia.auth.users import current_active_user, current_superuser
from miramedia.updates.dependencies import update_service_dep
from miramedia.updates.schemas import (
    ApplyRequest,
    ApplyState,
    ApplyTriggerResponse,
    UpdateInfo,
    VersionInfo,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


@router.get(
    "/version",
    dependencies=[Depends(current_active_user)],
)
async def get_version(svc: update_service_dep) -> VersionInfo:
    """Lightweight version probe — any logged-in user can read."""
    return svc.get_version_info()


@router.get(
    "/updates",
    dependencies=[Depends(current_superuser)],
)
async def get_updates(
    svc: update_service_dep,
    force: Annotated[bool, Query()] = False,
) -> UpdateInfo:
    try:
        return svc.get_update_info(force=force)
    except Exception as exc:
        log.exception("update check failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc


@router.post(
    "/updates/check",
    dependencies=[Depends(current_superuser)],
)
async def trigger_check(svc: update_service_dep) -> UpdateInfo:
    try:
        svc.invalidate_cache()
        return svc.get_update_info(force=True)
    except Exception as exc:
        log.exception("manual update check failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc


@router.get(
    "/updates/status",
    dependencies=[Depends(current_superuser)],
)
async def get_apply_status(svc: update_service_dep) -> ApplyState:
    return svc.get_apply_state()


@router.post(
    "/updates/apply",
    dependencies=[Depends(current_superuser)],
)
async def trigger_apply(
    body: ApplyRequest,
    svc: update_service_dep,
) -> ApplyTriggerResponse:
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm must be true",
        )
    accepted, detail = svc.trigger_apply(target_tag=body.target_tag)
    if not accepted and detail and "not supported" in detail:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )
    state = svc.get_apply_state()
    if not accepted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail or "apply not accepted",
        )
    log.info(f"update apply triggered (target={body.target_tag or 'default'})")
    return ApplyTriggerResponse(accepted=True, state=state, detail=detail)
