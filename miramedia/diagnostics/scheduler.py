"""Read-only scheduled-task snapshot for the diagnostics page."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.diagnostics.schemas import DiagnosticsScheduledTask, DiagnosticsScheduler
from miramedia.scheduler import (
    _STARTUP_SCHEDULES,
    background_broker,
    get_dynamic_schedule_targets,
    interactive_broker,
)

log = logging.getLogger(__name__)


def task_display_name(task_name: str) -> str:
    short = task_name.rsplit(":", 1)[-1].removesuffix("_task")
    return short.replace("_", " ")


def _cron_from_schedule_payload(payload: object) -> str | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    cron = payload.get("cron")
    return str(cron) if cron else None


async def _safe_rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        log.debug("diagnostics scheduler rollback failed", exc_info=True)


async def _try_rows(db: AsyncSession, sql: str) -> list[dict[str, object]] | None:
    """Return rows, or None when the query could not run (missing table, etc.)."""
    try:
        result = await db.execute(text(sql))
    except Exception:
        log.debug("diagnostics scheduler query failed", exc_info=True)
        await _safe_rollback(db)
        return None
    if result is None:
        return None
    mappings = getattr(result, "mappings", None)
    if callable(mappings):
        return [dict(row) for row in mappings().all()]
    rows = result.all() if hasattr(result, "all") else []
    out: list[dict[str, object]] = []
    for row in rows:
        mapping = getattr(row, "_mapping", None)
        out.append(dict(mapping) if mapping is not None else dict(row))
    return out


def _catalog_crons() -> dict[str, str]:
    crons: dict[str, str] = {}
    for task_name, entries in _STARTUP_SCHEDULES.items():
        if entries and "cron" in entries[0]:
            crons[task_name] = entries[0]["cron"]
    crons.update(get_dynamic_schedule_targets())
    return crons


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _as_int0(value: object) -> int:
    parsed = _as_int(value)
    return 0 if parsed is None else parsed


def _as_datetime(value: object) -> datetime | None:
    return value if isinstance(value, datetime) else None


def _broker_for(task_name: str) -> str:
    if task_name in interactive_broker.get_all_tasks():
        return "interactive"
    return "background"


async def get_scheduler_diagnostics(db: AsyncSession) -> DiagnosticsScheduler:
    catalog = _catalog_crons()
    schedule_rows = await _try_rows(
        db,
        "SELECT task_name, schedule, created_at, updated_at FROM taskiq_schedulers",
    )
    schedules_loaded = schedule_rows is not None
    overlay: dict[str, dict[str, object]] = {}
    for row in schedule_rows or []:
        name = str(row.get("task_name") or "")
        if not name:
            continue
        overlay[name] = row

    queued_by_task: dict[str, int] = {}
    queue_background: int | None = None
    queue_interactive: int | None = None
    queue_sql = {
        background_broker.table_name: (
            "SELECT task_name, count(*)::int AS queued "
            "FROM taskiq_messages_background GROUP BY task_name"
        ),
        interactive_broker.table_name: (
            "SELECT task_name, count(*)::int AS queued "
            "FROM taskiq_messages_interactive GROUP BY task_name"
        ),
    }
    for table, attr in (
        (background_broker.table_name, "background"),
        (interactive_broker.table_name, "interactive"),
    ):
        sql = queue_sql.get(table)
        if sql is None:
            continue
        rows = await _try_rows(db, sql)
        if rows is None:
            continue
        total = 0
        for row in rows:
            name = str(row.get("task_name") or "")
            count = _as_int0(row.get("queued"))
            total += count
            if name:
                queued_by_task[name] = queued_by_task.get(name, 0) + count
        if attr == "background":
            queue_background = total
        else:
            queue_interactive = total

    names = set(catalog) | set(overlay)
    tasks: list[DiagnosticsScheduledTask] = []
    for task_name in names:
        row = overlay.get(task_name, {})
        cron = _cron_from_schedule_payload(row.get("schedule")) or catalog.get(
            task_name
        )
        broker = _broker_for(task_name)
        queued = queued_by_task.get(task_name)
        if queued is None and (
            (broker == "background" and queue_background is not None)
            or (broker == "interactive" and queue_interactive is not None)
        ):
            queued = 0
        tasks.append(
            DiagnosticsScheduledTask(
                task_name=task_name,
                display_name=task_display_name(task_name),
                broker="interactive" if broker == "interactive" else "background",
                cron=cron,
                queued=queued,
                schedule_created_at=_as_datetime(row.get("created_at")),
                schedule_updated_at=_as_datetime(row.get("updated_at")),
            )
        )
    tasks.sort(key=lambda item: item.display_name)
    return DiagnosticsScheduler(
        generated_at=datetime.now(UTC),
        tasks=tasks,
        queue_background=queue_background,
        queue_interactive=queue_interactive,
        schedules_loaded=schedules_loaded,
    )
