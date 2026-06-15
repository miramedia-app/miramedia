from typing import Annotated

from fastapi import Depends

from miramedia.database import DbSessionDependency
from miramedia.settings.repository import SettingsRepository


def get_settings_repository(db_session: DbSessionDependency) -> SettingsRepository:
    return SettingsRepository(db_session)


settings_repository_dep = Annotated[
    SettingsRepository, Depends(get_settings_repository)
]
