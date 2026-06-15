"""Operational endpoints (metrics-adjacent, superuser-only)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text

from miramedia.auth.users import current_superuser
from miramedia.database import DbSessionDependency

log = logging.getLogger(__name__)
router = APIRouter(prefix="/ops", tags=["ops"])


class OpsSummary(BaseModel):
    slow_queries: list[dict]
    pool: dict


@router.get(
    "/summary",
    dependencies=[Depends(current_superuser)],
)
async def ops_summary(db: DbSessionDependency) -> OpsSummary:
    """Lightweight snapshot from pg_stat_statements + pool stats."""
    slow: list[dict] = []
    try:
        rows = await db.execute(
            text(
                """
                SELECT query, calls, mean_exec_time::float AS mean_ms,
                       total_exec_time::float AS total_ms
                FROM pg_stat_statements
                ORDER BY mean_exec_time DESC
                LIMIT 10
                """
            )
        )
        slow = [dict(r._mapping) for r in rows]
    except Exception:
        log.debug("pg_stat_statements unavailable", exc_info=True)

    from miramedia.database import background_engine, get_engine

    pool: dict = {}
    for name, eng in (
        ("request", get_engine()),
        ("background", background_engine),
    ):
        if eng is None:
            continue
        p = eng.pool.status()  # type: ignore[union-attr]
        pool[name] = {"status": p}
    return OpsSummary(slow_queries=slow, pool=pool)
