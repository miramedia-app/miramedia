from typing import Annotated

from fastapi import Depends

from miramedia.database import DbSessionDependency
from miramedia.movies.dependencies import movie_repository_dep
from miramedia.playback.repository import PlaybackRepository
from miramedia.playback.service import PlaybackService
from miramedia.shows.dependencies import show_repository_dep


def get_playback_repository(db_session: DbSessionDependency) -> PlaybackRepository:
    return PlaybackRepository(db_session)


playback_repository_dep = Annotated[
    PlaybackRepository, Depends(get_playback_repository)
]


def get_playback_service(
    playback_repository: playback_repository_dep,
    movie_repository: movie_repository_dep,
    show_repository: show_repository_dep,
) -> PlaybackService:
    return PlaybackService(
        repository=playback_repository,
        movie_repository=movie_repository,
        show_repository=show_repository,
    )


playback_service_dep = Annotated[PlaybackService, Depends(get_playback_service)]
