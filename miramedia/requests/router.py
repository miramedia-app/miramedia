import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from miramedia.auth.users import (
    CurrentUserDep,
    SuperuserDep,
    User,
    current_active_user,
)
from miramedia.requests.dependencies import (
    request_service_dep,
    require_requests_enabled,
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
from miramedia.requests.service import RequestService

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/requests",
    tags=["requests"],
    dependencies=[
        Depends(require_requests_enabled),
        Depends(current_active_user),
    ],
)


# --------------------------------
# LIST / COUNT
# --------------------------------


@router.get("")
async def list_requests(
    request_service: request_service_dep,
    user: CurrentUserDep,
    request_status: Annotated[RequestStatus | None, Query(alias="status")] = None,
    media_type: Annotated[MediaType | None, Query()] = None,
    mine: Annotated[bool, Query()] = False,
) -> list[MediaRequest]:
    requested_by = UUID(str(user.id)) if mine else None
    return await request_service.list_requests(
        status=request_status,
        media_type=media_type,
        requested_by_id=requested_by,
    )


@router.get("/count")
async def get_pending_count(
    request_service: request_service_dep,
) -> MediaRequestCount:
    return await request_service.get_pending_count()


# --------------------------------
# CRUD
# --------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_request(
    data: MediaRequestCreate,
    request_service: request_service_dep,
    user: CurrentUserDep,
) -> MediaRequest:
    return await request_service.create_request(
        data=data,
        requested_by_id=UUID(str(user.id)),
        is_superuser=user.is_superuser,
    )


async def _authorize_owner(
    request_service: RequestService, request_id: MediaRequestId, user: User
) -> MediaRequest:
    """Load a request and enforce owner-or-superuser access.

    Non-owners get 404 (not 403) so they can't enumerate other users'
    request IDs. Without this, any authenticated user could read, edit, or
    delete another user's request by guessing its UUID — the router only
    gated on ``current_active_user``, and the native provider ignored the
    ``user_id`` threaded into update.
    """
    req = await request_service.get_request(request_id)
    if not user.is_superuser and req.requested_by_id != UUID(str(user.id)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Request not found"
        )
    return req


@router.get("/{request_id}")
async def get_request(
    request_id: MediaRequestId,
    request_service: request_service_dep,
    user: CurrentUserDep,
) -> MediaRequest:
    return await _authorize_owner(request_service, request_id, user)


@router.put("/{request_id}")
async def update_request(
    request_id: MediaRequestId,
    data: MediaRequestUpdate,
    request_service: request_service_dep,
    user: CurrentUserDep,
) -> MediaRequest:
    await _authorize_owner(request_service, request_id, user)
    return await request_service.update_request(request_id, data, UUID(str(user.id)))


@router.delete(
    "/{request_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_request(
    request_id: MediaRequestId,
    request_service: request_service_dep,
    user: CurrentUserDep,
) -> None:
    await _authorize_owner(request_service, request_id, user)
    await request_service.delete_request(request_id)


# --------------------------------
# APPROVE / REJECT (superuser only)
# --------------------------------


@router.patch("/{request_id}/approve")
async def approve_request(
    request_id: MediaRequestId,
    request_service: request_service_dep,
    user: SuperuserDep,
) -> MediaRequest:
    result = await request_service.approve_request(request_id, UUID(str(user.id)))
    try:
        from miramedia.scheduler import fulfill_approved_requests_task

        await fulfill_approved_requests_task.kiq()
    except Exception:
        log.warning("Could not trigger immediate request fulfillment task")
    return result


@router.patch("/{request_id}/reject")
async def reject_request(
    request_id: MediaRequestId,
    request_service: request_service_dep,
    user: SuperuserDep,
) -> MediaRequest:
    return await request_service.reject_request(request_id, UUID(str(user.id)))
