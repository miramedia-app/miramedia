import logging

from sqlalchemy import delete, select, update
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import false

from miramedia.exceptions import ConflictError, NotFoundError
from miramedia.notifications.models import Notification
from miramedia.notifications.schemas import (
    Notification as NotificationSchema,
)
from miramedia.notifications.schemas import (
    NotificationId,
)

log = logging.getLogger(__name__)


class NotificationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_notification(self, nid: NotificationId) -> NotificationSchema:
        result = await self.db.get(Notification, nid)

        if not result:
            msg = f"Notification with id {nid} not found."
            raise NotFoundError(msg)

        return NotificationSchema.model_validate(result)

    async def get_unread_notifications(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[NotificationSchema]:
        try:
            stmt = (
                select(Notification)
                .where(Notification.read == false())
                .order_by(Notification.timestamp.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await self.db.execute(stmt)
            results = result.scalars().all()
            return [
                NotificationSchema.model_validate(notification)
                for notification in results
            ]
        except SQLAlchemyError:
            log.exception("Database error while retrieving unread notifications")
            raise

    async def get_all_notifications(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[NotificationSchema]:
        try:
            stmt = (
                select(Notification)
                .order_by(Notification.timestamp.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await self.db.execute(stmt)
            results = result.scalars().all()
            return [
                NotificationSchema.model_validate(notification)
                for notification in results
            ]
        except SQLAlchemyError:
            log.exception("Database error while retrieving notifications")
            raise

    async def save_notification(self, notification: NotificationSchema) -> None:
        try:
            self.db.add(
                Notification(
                    id=notification.id,
                    read=notification.read,
                    timestamp=notification.timestamp,
                    message=notification.message,
                )
            )
            await self.db.commit()
        except IntegrityError:
            log.exception("Could not save notification")
            msg = f"Notification with id {notification.id} already exists."
            raise ConflictError(msg) from None
        return

    async def mark_notification_as_read(self, nid: NotificationId) -> None:
        stmt = update(Notification).where(Notification.id == nid).values(read=True)
        result = await self.db.execute(stmt)
        if result.rowcount == 0:
            msg = f"Notification with id {nid} not found."
            raise NotFoundError(msg)
        return

    async def mark_notification_as_unread(self, nid: NotificationId) -> None:
        stmt = update(Notification).where(Notification.id == nid).values(read=False)
        result = await self.db.execute(stmt)
        if result.rowcount == 0:
            msg = f"Notification with id {nid} not found."
            raise NotFoundError(msg)
        return

    async def delete_notification(self, nid: NotificationId) -> None:
        stmt = delete(Notification).where(Notification.id == nid)
        result = await self.db.execute(stmt)
        if result.rowcount == 0:
            msg = f"Notification with id {nid} not found."
            raise NotFoundError(msg)
        await self.db.commit()
        return

    async def delete_read_older_than(self, cutoff) -> int:  # noqa: ANN001
        """Delete read notifications with timestamp older than ``cutoff``.

        Unread notifications are preserved regardless of age.
        Returns the number of rows deleted.
        """
        stmt = delete(Notification).where(
            Notification.read.is_(True), Notification.timestamp < cutoff
        )
        result = await self.db.execute(stmt)
        await self.db.commit()
        return int(result.rowcount or 0)
