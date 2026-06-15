from typing import Annotated

from fastapi import Depends

from miramedia.database import DbSessionDependency
from miramedia.imports.repository import ImportsRepository
from miramedia.imports.service import ImportsService
from miramedia.movies.dependencies import movie_service_dep
from miramedia.shows.dependencies import show_service_dep
from miramedia.torrents.dependencies import torrent_service_dep


def get_imports_repository(db: DbSessionDependency) -> ImportsRepository:
    return ImportsRepository(db=db)


imports_repository_dep = Annotated[ImportsRepository, Depends(get_imports_repository)]


def get_imports_service(
    repository: imports_repository_dep,
    torrent_service: torrent_service_dep,
    show_service: show_service_dep,
    movie_service: movie_service_dep,
) -> ImportsService:
    return ImportsService(
        repository=repository,
        torrent_service=torrent_service,
        show_service=show_service,
        movie_service=movie_service,
    )


imports_service_dep = Annotated[ImportsService, Depends(get_imports_service)]
