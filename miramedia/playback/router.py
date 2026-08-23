from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from miramedia.auth.users import CurrentUserDep, current_active_user
from miramedia.config import MiraMediaConfig
from miramedia.playback.dependencies import (
    playback_service_dep,
    require_continue_watching_enabled,
)
from miramedia.playback.schemas import (
    ContinueWatchingItem,
    MediaKind,
    PlaybackProgress,
    PlaybackProgressUpsert,
    SeasonWatchStateUpdate,
    ShowWatchStateUpdate,
    UpNextItem,
    WatchState,
    WatchStateUpdate,
)
from miramedia.watchlists.dependencies import require_watch_next_enabled

router = APIRouter(
    prefix="/playback",
    tags=["playback"],
    dependencies=[Depends(current_active_user)],
)


def _user_id(user: CurrentUserDep) -> UUID:
    return UUID(str(user.id))


@router.get("/progress")
async def get_progress(
    file_id: Annotated[UUID, Query()],
    playback_service: playback_service_dep,
    user: CurrentUserDep,
    media_kind: Annotated[MediaKind | None, Query()] = None,
) -> PlaybackProgress | None:
    return await playback_service.get_progress(
        user_id=_user_id(user),
        file_id=file_id,
        media_kind=media_kind,
    )


@router.put("/progress")
async def upsert_progress(
    data: PlaybackProgressUpsert,
    playback_service: playback_service_dep,
    user: CurrentUserDep,
) -> PlaybackProgress | None:
    return await playback_service.upsert_progress(
        user_id=_user_id(user),
        data=data,
    )


@router.delete("/progress/all", status_code=status.HTTP_204_NO_CONTENT)
async def delete_all_progress(
    playback_service: playback_service_dep,
    user: CurrentUserDep,
) -> None:
    await playback_service.delete_all_progress(user_id=_user_id(user))


@router.delete("/progress", status_code=status.HTTP_204_NO_CONTENT)
async def delete_progress(
    file_id: Annotated[UUID, Query()],
    playback_service: playback_service_dep,
    user: CurrentUserDep,
) -> None:
    await playback_service.delete_progress(user_id=_user_id(user), file_id=file_id)


@router.get("/continue", dependencies=[Depends(require_continue_watching_enabled)])
async def list_continue(
    playback_service: playback_service_dep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ContinueWatchingItem]:
    return await playback_service.list_continue(user_id=_user_id(user), limit=limit)


@router.get("/watch-next", dependencies=[Depends(require_watch_next_enabled)])
async def list_watch_next(
    playback_service: playback_service_dep,
    user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    include_specials: Annotated[bool | None, Query()] = None,
) -> list[UpNextItem]:
    effective_specials = (
        include_specials
        if include_specials is not None
        else MiraMediaConfig().watchlists.native.watch_next_include_specials
    )
    return await playback_service.list_up_next(
        user_id=_user_id(user),
        limit=limit,
        include_specials=effective_specials,
    )


@router.get("/watched")
async def get_watched(
    media_kind: Annotated[MediaKind, Query()],
    media_id: Annotated[UUID, Query()],
    playback_service: playback_service_dep,
    user: CurrentUserDep,
) -> WatchState:
    return await playback_service.get_watched(
        user_id=_user_id(user),
        media_kind=media_kind,
        media_id=media_id,
    )


@router.put("/watched")
async def set_watched(
    data: WatchStateUpdate,
    playback_service: playback_service_dep,
    user: CurrentUserDep,
) -> WatchState:
    return await playback_service.set_watched(user_id=_user_id(user), data=data)


@router.delete("/watched")
async def clear_watched_override(
    media_kind: Annotated[MediaKind, Query()],
    media_id: Annotated[UUID, Query()],
    playback_service: playback_service_dep,
    user: CurrentUserDep,
) -> WatchState:
    return await playback_service.clear_watched_override(
        user_id=_user_id(user),
        media_kind=media_kind,
        media_id=media_id,
    )


@router.put("/watched/season", status_code=status.HTTP_204_NO_CONTENT)
async def set_season_watched(
    data: SeasonWatchStateUpdate,
    playback_service: playback_service_dep,
    user: CurrentUserDep,
) -> None:
    await playback_service.set_season_watched(user_id=_user_id(user), data=data)


@router.put("/watched/show", status_code=status.HTTP_204_NO_CONTENT)
async def set_show_watched(
    data: ShowWatchStateUpdate,
    playback_service: playback_service_dep,
    user: CurrentUserDep,
) -> None:
    await playback_service.set_show_watched(user_id=_user_id(user), data=data)


@router.delete("/viewing-state", status_code=status.HTTP_204_NO_CONTENT)
async def delete_viewing_state(
    playback_service: playback_service_dep,
    user: CurrentUserDep,
) -> None:
    await playback_service.delete_all_viewing_state(user_id=_user_id(user))
