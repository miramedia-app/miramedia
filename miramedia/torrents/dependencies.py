from typing import Annotated

from fastapi import Depends
from fastapi.exceptions import HTTPException

from miramedia.database import DbSessionDependency
from miramedia.exceptions import NotFoundError
from miramedia.torrents.repository import TorrentRepository
from miramedia.torrents.schemas import Torrent, TorrentId
from miramedia.torrents.service import TorrentService


def get_torrent_repository(db: DbSessionDependency) -> TorrentRepository:
    return TorrentRepository(db=db)


torrent_repository_dep = Annotated[TorrentRepository, Depends(get_torrent_repository)]


def get_torrent_service(torrent_repository: torrent_repository_dep) -> TorrentService:
    return TorrentService(torrent_repository=torrent_repository)


torrent_service_dep = Annotated[TorrentService, Depends(get_torrent_service)]


async def get_torrent_by_id(
    torrent_service: torrent_service_dep, torrent_id: TorrentId
) -> Torrent:
    """
    Retrieves a torrent by its ID.

    :param torrent_service: The TorrentService instance.
    :param torrent_id: The ID of the torrent to retrieve.
    :return: The TorrentService instance with the specified torrent.
    """
    try:
        torrent = await torrent_service.get_torrent_by_id(torrent_id=torrent_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Torrent with ID {torrent_id} not found"
        ) from None
    return torrent


torrent_dep = Annotated[Torrent, Depends(get_torrent_by_id)]
