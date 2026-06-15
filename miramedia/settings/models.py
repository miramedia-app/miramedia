from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from miramedia.database import Base


class SystemConfigOverride(Base):
    __tablename__ = "system_config_override"
    __table_args__ = (
        CheckConstraint("id = 1", name="system_config_override_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    overrides: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
