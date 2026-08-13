from __future__ import annotations

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette import status

from miramedia.auth.users import current_active_user, current_superuser
from miramedia.updates.dependencies import update_service_dep
from miramedia.updates.schemas import UpdateInfo, VersionInfo

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
        return await asyncio.to_thread(svc.get_update_info, force)
    except Exception as exc:
        log.exception("update check failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="update check failed",
        ) from exc


@router.post(
    "/updates/check",
    dependencies=[Depends(current_superuser)],
)
async def trigger_check(svc: update_service_dep) -> UpdateInfo:
    try:
        svc.invalidate_cache()
        return await asyncio.to_thread(svc.get_update_info, True)
    except Exception as exc:
        log.exception("manual update check failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="manual update check failed",
        ) from exc
