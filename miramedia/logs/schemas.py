from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ActivityLogRead(BaseModel):
    id: UUID
    timestamp: datetime
    level: str
    module: str
    message: str
    correlation_id: str | None = None
    extra: dict | None = None

    model_config = {"from_attributes": True}


class PaginatedResponse[T](BaseModel):
    items: list[T]
    total: int
    offset: int
    limit: int
