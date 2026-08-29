from typing import Annotated

from fastapi import Depends

from miramedia.database import DbSessionDependency
from miramedia.movies.dependencies import movie_repository_dep
from miramedia.shows.dependencies import show_repository_dep
from miramedia.storage.repository import StorageHealthRepository
from miramedia.storage.service import StorageHealthService


def get_storage_health_service(
    db_session: DbSessionDependency,
    show_repository: show_repository_dep,
    movie_repository: movie_repository_dep,
) -> StorageHealthService:
    return StorageHealthService(
        db_session,
        show_repository=show_repository,
        movie_repository=movie_repository,
        repository=StorageHealthRepository(db_session),
    )


storage_health_service_dep = Annotated[
    StorageHealthService, Depends(get_storage_health_service)
]
