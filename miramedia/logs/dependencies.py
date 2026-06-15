from typing import Annotated

from fastapi import Depends

from miramedia.database import DbSessionDependency
from miramedia.logs.repository import LogRepository


def get_log_repository(db_session: DbSessionDependency) -> LogRepository:
    return LogRepository(db_session)


log_repository_dep = Annotated[LogRepository, Depends(get_log_repository)]
