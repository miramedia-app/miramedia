from typing import Annotated

from fastapi import Depends

from miramedia.database import DbSessionDependency
from miramedia.notifications.repository import NotificationRepository
from miramedia.notifications.service import NotificationService


def get_notification_repository(
    db_session: DbSessionDependency,
) -> NotificationRepository:
    return NotificationRepository(db_session)


notification_repository_dep = Annotated[
    NotificationRepository, Depends(get_notification_repository)
]


def get_notification_service(
    notification_repository: notification_repository_dep,
) -> NotificationService:
    return NotificationService(notification_repository)


notification_service_dep = Annotated[
    NotificationService, Depends(get_notification_service)
]
