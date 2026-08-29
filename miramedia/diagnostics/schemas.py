"""Read-only diagnostics API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class DiagnosticsDatabasePool(BaseModel):
    name: str
    status: str = ""
    size: int | None = None
    checked_out: int | None = None
    overflow: int | None = None


class DiagnosticsDatabaseTable(BaseModel):
    name: str
    total_bytes: int
    table_bytes: int
    index_bytes: int
    estimated_rows: int | None = None


class DiagnosticsDatabaseConnection(BaseModel):
    state: str
    count: int


class DiagnosticsDatabase(BaseModel):
    generated_at: datetime
    host: str
    port: int
    name: str
    user: str
    server_version: str | None = None
    size_bytes: int | None = None
    max_connections: int | None = None
    started_at: datetime | None = None
    connections: list[DiagnosticsDatabaseConnection] = []
    pools: list[DiagnosticsDatabasePool] = []
    largest_tables: list[DiagnosticsDatabaseTable] = []


class DiagnosticsScheduledTask(BaseModel):
    task_name: str
    display_name: str
    broker: Literal["background", "interactive"]
    cron: str | None = None
    queued: int | None = None
    schedule_created_at: datetime | None = None
    schedule_updated_at: datetime | None = None


class DiagnosticsScheduler(BaseModel):
    generated_at: datetime
    tasks: list[DiagnosticsScheduledTask]
    queue_background: int | None = None
    queue_interactive: int | None = None
    schedules_loaded: bool
