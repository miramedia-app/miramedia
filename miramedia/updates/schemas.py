from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class UpdateChannel(StrEnum):
    stable = "stable"


class UpdateStatusState(StrEnum):
    idle = "idle"
    checking = "checking"
    pulling = "pulling"
    restarting = "restarting"
    applied = "applied"
    failed = "failed"


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
    apply_supported: bool


class VersionInfo(BaseModel):
    version: str | None
    image: str | None
    base_path: str | None


class ApplyState(BaseModel):
    state: UpdateStatusState
    target_version: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    log: list[str] = []


class ApplyTriggerResponse(BaseModel):
    accepted: bool
    state: ApplyState
    detail: str | None = None


class ApplyRequest(BaseModel):
    target_tag: str | None = None  # default = config.updates.image_tag
    confirm: Literal[True]
