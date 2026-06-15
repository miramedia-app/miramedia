from typing import Annotated

from fastapi import Depends

from miramedia.updates.service import UpdateService


def get_update_service() -> UpdateService:
    return UpdateService()


update_service_dep = Annotated[UpdateService, Depends(get_update_service)]
