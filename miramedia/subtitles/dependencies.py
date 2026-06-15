from typing import Annotated

from fastapi import Depends

from miramedia.database import DbSessionDependency
from miramedia.movies.dependencies import movie_service_dep
from miramedia.shows.dependencies import show_service_dep
from miramedia.subtitles.repository import SubtitleRepository
from miramedia.subtitles.service import SubtitleService


def get_subtitle_repository(db_session: DbSessionDependency) -> SubtitleRepository:
    return SubtitleRepository(db_session)


subtitle_repository_dep = Annotated[
    SubtitleRepository, Depends(get_subtitle_repository)
]


def get_subtitle_service(
    subtitle_repository: subtitle_repository_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> SubtitleService:
    return SubtitleService(
        subtitle_repository=subtitle_repository,
        show_service=show_service,
        movie_service=movie_service,
    )


subtitle_service_dep = Annotated[SubtitleService, Depends(get_subtitle_service)]
