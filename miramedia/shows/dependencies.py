from typing import Annotated

from fastapi import Depends, HTTPException, Path

from miramedia.database import DbSessionDependency
from miramedia.exceptions import NotFoundError
from miramedia.indexers.dependencies import indexer_service_dep
from miramedia.notifications.dependencies import notification_service_dep
from miramedia.shows.repository import ShowRepository
from miramedia.shows.schemas import Season, SeasonId, Show, ShowId
from miramedia.shows.service import ShowService
from miramedia.torrents.dependencies import torrent_service_dep


def get_show_repository(db_session: DbSessionDependency) -> ShowRepository:
    return ShowRepository(db_session)


show_repository_dep = Annotated[ShowRepository, Depends(get_show_repository)]


def get_show_service(
    show_repository: show_repository_dep,
    torrent_service: torrent_service_dep,
    indexer_service: indexer_service_dep,
    notification_service: notification_service_dep,
) -> ShowService:
    return ShowService(
        show_repository=show_repository,
        torrent_service=torrent_service,
        indexer_service=indexer_service,
        notification_service=notification_service,
    )


show_service_dep = Annotated[ShowService, Depends(get_show_service)]


async def get_show_by_id(
    show_service: show_service_dep,
    show_id: Annotated[ShowId, Path(description="The ID of the show")],
) -> Show:
    try:
        show = await show_service.get_show_by_id(show_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Show with ID {show_id} not found.",
        ) from None
    return show


show_dep = Annotated[Show, Depends(get_show_by_id)]


async def get_season_by_id(
    show_service: show_service_dep,
    season_id: Annotated[SeasonId, Path(description="The ID of the season")],
) -> Season:
    try:
        season = await show_service.get_season(season_id=season_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Season with ID {season_id} not found.",
        ) from None
    return season


season_dep = Annotated[Season, Depends(get_season_by_id)]
