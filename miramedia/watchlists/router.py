from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from miramedia.auth.users import CurrentUserDep, current_active_user
from miramedia.upcoming.dependencies import upcoming_service_dep
from miramedia.upcoming.schemas import UpcomingResponse
from miramedia.watchlists.dependencies import (
    require_custom_lists_enabled,
    require_upcoming_enabled,
    require_watchlists_enabled,
    watchlist_service_dep,
)
from miramedia.watchlists.schemas import (
    WatchlistCreate,
    WatchlistDetail,
    WatchlistItemCreate,
    WatchlistItemView,
    WatchlistReorder,
    WatchlistSummary,
    WatchlistUpdate,
)

router = APIRouter(
    prefix="/watchlists",
    tags=["watchlists"],
    dependencies=[
        Depends(current_active_user),
        Depends(require_watchlists_enabled),
    ],
)


def _user_id(user: CurrentUserDep) -> UUID:
    return UUID(str(user.id))


_CUSTOM_LISTS = [Depends(require_custom_lists_enabled)]


@router.get("", dependencies=_CUSTOM_LISTS)
async def list_watchlists(
    watchlist_service: watchlist_service_dep,
    user: CurrentUserDep,
) -> list[WatchlistSummary]:
    return await watchlist_service.list_watchlists(user_id=_user_id(user))


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=_CUSTOM_LISTS)
async def create_watchlist(
    data: WatchlistCreate,
    watchlist_service: watchlist_service_dep,
    user: CurrentUserDep,
) -> WatchlistDetail:
    return await watchlist_service.create_watchlist(
        user_id=_user_id(user),
        data=data,
    )


@router.get("/upcoming", dependencies=[Depends(require_upcoming_enabled)])
async def get_watchlists_upcoming(
    upcoming_service: upcoming_service_dep,
    start: Annotated[
        date | None, Query(description="Window start (inclusive).")
    ] = None,
    end: Annotated[date | None, Query(description="Window end (inclusive).")] = None,
) -> UpcomingResponse:
    """Tracked library air/release dates for the Watchlists Upcoming view.

    Defaults to today / next-30; `start`/`end` override either bound and the
    span is clamped server-side to MAX_WINDOW_DAYS.
    """
    try:
        return await upcoming_service.get_upcoming(start=start, end=end)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/{watchlist_id}", dependencies=_CUSTOM_LISTS)
async def get_watchlist(
    watchlist_id: UUID,
    watchlist_service: watchlist_service_dep,
    user: CurrentUserDep,
) -> WatchlistDetail:
    detail = await watchlist_service.get_watchlist(
        user_id=_user_id(user),
        watchlist_id=watchlist_id,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watchlist not found",
        )
    return detail


@router.patch("/{watchlist_id}", dependencies=_CUSTOM_LISTS)
async def update_watchlist(
    watchlist_id: UUID,
    data: WatchlistUpdate,
    watchlist_service: watchlist_service_dep,
    user: CurrentUserDep,
) -> WatchlistDetail:
    detail = await watchlist_service.update_watchlist(
        user_id=_user_id(user),
        watchlist_id=watchlist_id,
        data=data,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watchlist not found",
        )
    return detail


@router.delete(
    "/{watchlist_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CUSTOM_LISTS,
)
async def delete_watchlist(
    watchlist_id: UUID,
    watchlist_service: watchlist_service_dep,
    user: CurrentUserDep,
) -> Response:
    deleted = await watchlist_service.delete_watchlist(
        user_id=_user_id(user),
        watchlist_id=watchlist_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watchlist not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{watchlist_id}/items",
    response_model=WatchlistItemView,
    dependencies=_CUSTOM_LISTS,
    responses={
        status.HTTP_200_OK: {"model": WatchlistItemView},
        status.HTTP_201_CREATED: {"model": WatchlistItemView},
    },
)
async def add_watchlist_item(
    watchlist_id: UUID,
    data: WatchlistItemCreate,
    response: Response,
    watchlist_service: watchlist_service_dep,
    user: CurrentUserDep,
) -> WatchlistItemView:
    item, created = await watchlist_service.add_item(
        user_id=_user_id(user),
        watchlist_id=watchlist_id,
        data=data,
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return item


@router.put("/{watchlist_id}/items/order", dependencies=_CUSTOM_LISTS)
async def reorder_watchlist_items(
    watchlist_id: UUID,
    data: WatchlistReorder,
    watchlist_service: watchlist_service_dep,
    user: CurrentUserDep,
) -> WatchlistDetail:
    return await watchlist_service.reorder_items(
        user_id=_user_id(user),
        watchlist_id=watchlist_id,
        data=data,
    )


@router.delete(
    "/{watchlist_id}/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_CUSTOM_LISTS,
)
async def remove_watchlist_item(
    watchlist_id: UUID,
    item_id: UUID,
    watchlist_service: watchlist_service_dep,
    user: CurrentUserDep,
) -> Response:
    removed = await watchlist_service.remove_item(
        user_id=_user_id(user),
        watchlist_id=watchlist_id,
        item_id=item_id,
    )
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Watchlist item not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
