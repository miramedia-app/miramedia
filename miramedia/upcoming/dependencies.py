from typing import Annotated

from fastapi import Depends

from miramedia.database import DbSessionDependency
from miramedia.upcoming.repository import UpcomingRepository
from miramedia.upcoming.service import UpcomingService


def get_upcoming_repository(db_session: DbSessionDependency) -> UpcomingRepository:
    return UpcomingRepository(db_session)


upcoming_repository_dep = Annotated[
    UpcomingRepository, Depends(get_upcoming_repository)
]


def get_upcoming_service(repository: upcoming_repository_dep) -> UpcomingService:
    return UpcomingService(repository)


upcoming_service_dep = Annotated[UpcomingService, Depends(get_upcoming_service)]
