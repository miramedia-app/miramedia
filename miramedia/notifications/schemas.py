import typing
import uuid
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

NotificationId = typing.NewType("NotificationId", UUID)


class Notification(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: NotificationId = Field(
        default_factory=lambda: NotificationId(uuid.uuid4()),
        description="Unique identifier for the notification",
    )
    read: bool = Field(False, description="Whether the notification has been read")
    message: str = Field(description="The content of the notification")
    timestamp: datetime = Field(
        # tz-aware UTC: stored in a TIMESTAMPTZ column. A naive datetime.now()
        # (local wall clock) gets written as if it were UTC, skewing the value
        # by the host's offset and breaking the retention sweep that compares
        # against a tz-aware UTC cutoff.
        default_factory=lambda: datetime.now(UTC),
        description="The timestamp of the notification",
    )


class MessageNotification(BaseModel):
    """
    Notification type for messages.
    """

    message: str
    title: str
