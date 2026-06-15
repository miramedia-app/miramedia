from typing import Annotated

from fastapi import Depends

from miramedia.database import DbSessionDependency
from miramedia.indexers.repository import IndexerRepository
from miramedia.indexers.service import IndexerService


def get_indexer_repository(db_session: DbSessionDependency) -> IndexerRepository:
    return IndexerRepository(db_session)


indexer_repository_dep = Annotated[IndexerRepository, Depends(get_indexer_repository)]


def get_indexer_service(
    indexer_repository: indexer_repository_dep,
) -> IndexerService:
    return IndexerService(indexer_repository)


indexer_service_dep = Annotated[IndexerService, Depends(get_indexer_service)]
