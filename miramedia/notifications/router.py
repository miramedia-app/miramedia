from fastapi import APIRouter, Depends, status

from miramedia.auth.users import current_active_user
from miramedia.notifications.dependencies import notification_service_dep
from miramedia.notifications.schemas import Notification, NotificationId

router = APIRouter(
    prefix="/notifications",
    tags=["notifications"],
    dependencies=[Depends(current_active_user)],
)


# --------------------------------
# GET NOTIFICATIONS
# --------------------------------


@router.get("")
async def get_all_notifications(
    notification_service: notification_service_dep,
) -> list[Notification]:
    """
    Get all notifications.
    """
    return await notification_service.get_all_notifications()


@router.get("/unread")
async def get_unread_notifications(
    notification_service: notification_service_dep,
) -> list[Notification]:
    """
    Get all unread notifications.
    """
    return await notification_service.get_unread_notifications()


@router.get(
    "/{notification_id}",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Notification not found"},
    },
)
async def get_notification(
    notification_id: NotificationId, notification_service: notification_service_dep
) -> Notification:
    """
    Get a specific notification by ID.
    """
    return await notification_service.get_notification(nid=notification_id)


# --------------------------------
# MANAGE NOTIFICATIONS
# --------------------------------


@router.patch(
    "/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Notification not found"},
    },
)
async def mark_notification_as_read(
    notification_id: NotificationId, notification_service: notification_service_dep
) -> None:
    """
    Mark a notification as read.
    """
    await notification_service.mark_notification_as_read(nid=notification_id)


@router.patch(
    "/{notification_id}/unread",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Notification not found"},
    },
)
async def mark_notification_as_unread(
    notification_id: NotificationId, notification_service: notification_service_dep
) -> None:
    """
    Mark a notification as unread.
    """
    await notification_service.mark_notification_as_unread(nid=notification_id)


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Notification not found"},
    },
)
async def delete_notification(
    notification_id: NotificationId, notification_service: notification_service_dep
) -> None:
    """
    Delete a notification.
    """
    await notification_service.delete_notification(nid=notification_id)
