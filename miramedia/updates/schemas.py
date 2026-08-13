from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class UpdateChannel(StrEnum):
    stable = "stable"


class UpdateInfo(BaseModel):
    enabled: bool
    current_version: str | None
    latest_version: str | None
    update_available: bool
    release_url: str | None
    release_notes: str | None
    published_at: datetime | None
    last_checked_at: datetime | None
    repo: str


class VersionInfo(BaseModel):
    version: str | None
    image: str | None
    base_path: str | None
