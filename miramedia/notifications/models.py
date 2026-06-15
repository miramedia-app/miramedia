from uuid import UUID

from sqlalchemy import DateTime, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    message: Mapped[str]
    read: Mapped[bool]
    timestamp = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_notification_timestamp", text("timestamp DESC")),
        Index(
            "ix_notification_unread",
            "timestamp",
            postgresql_where=text("read = false"),
        ),
    )
