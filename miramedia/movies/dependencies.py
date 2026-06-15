from typing import Annotated

from fastapi import Depends, HTTPException, Path

from miramedia.database import DbSessionDependency
from miramedia.exceptions import NotFoundError
from miramedia.indexers.dependencies import indexer_service_dep
from miramedia.movies.repository import MovieRepository
from miramedia.movies.schemas import Movie, MovieId
from miramedia.movies.service import MovieService
from miramedia.notifications.dependencies import notification_service_dep
from miramedia.torrents.dependencies import torrent_service_dep


def get_movie_repository(db_session: DbSessionDependency) -> MovieRepository:
    return MovieRepository(db_session)


movie_repository_dep = Annotated[MovieRepository, Depends(get_movie_repository)]


def get_movie_service(
    movie_repository: movie_repository_dep,
    torrent_service: torrent_service_dep,
    indexer_service: indexer_service_dep,
    notification_service: notification_service_dep,
) -> MovieService:
    return MovieService(
        movie_repository=movie_repository,
        torrent_service=torrent_service,
        indexer_service=indexer_service,
        notification_service=notification_service,
    )


movie_service_dep = Annotated[MovieService, Depends(get_movie_service)]


async def get_movie_by_id(
    movie_service: movie_service_dep,
    movie_id: Annotated[MovieId, Path(description="The ID of the movie")],
) -> Movie:
    try:
        movie = await movie_service.get_movie_by_id(movie_id)
    except NotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Movie with ID {movie_id} not found.",
        ) from None
    return movie


movie_dep = Annotated[Movie, Depends(get_movie_by_id)]
