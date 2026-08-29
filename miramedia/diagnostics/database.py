"""Read-only PostgreSQL + pool snapshot for the diagnostics page."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from miramedia.config import MiraMediaConfig
from miramedia.database import background_engine, get_engine
from miramedia.database.config import DbConfig
from miramedia.diagnostics.schemas import (
    DiagnosticsDatabase,
    DiagnosticsDatabaseConnection,
    DiagnosticsDatabasePool,
    DiagnosticsDatabaseTable,
)

log = logging.getLogger(__name__)

_IDENTITY_SQL = """
SELECT
    current_setting('server_version') AS server_version,
    pg_database_size(current_database())::bigint AS size_bytes,
    current_setting('max_connections')::int AS max_connections,
    pg_postmaster_start_time() AS started_at
"""

_CONNECTIONS_SQL = """
SELECT coalesce(state, 'unknown') AS state, count(*)::int AS count
FROM pg_stat_activity
WHERE datname = current_database()
GROUP BY state
ORDER BY count DESC
"""

_TABLES_SQL = """
SELECT
    c.relname AS name,
    pg_total_relation_size(c.oid)::bigint AS total_bytes,
    pg_relation_size(c.oid)::bigint AS table_bytes,
    pg_indexes_size(c.oid)::bigint AS index_bytes,
    COALESCE(s.n_live_tup, 0)::bigint AS estimated_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY pg_total_relation_size(c.oid) DESC
LIMIT 15
"""


async def _safe_rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        log.debug("diagnostics database rollback failed", exc_info=True)


async def _try_rows(db: AsyncSession, sql: str) -> list[dict[str, object]]:
    try:
        result = await db.execute(text(sql))
    except Exception:
        log.debug("diagnostics database query failed", exc_info=True)
        await _safe_rollback(db)
        return []
    if result is None:
        return []
    mappings = getattr(result, "mappings", None)
    if callable(mappings):
        return [dict(row) for row in mappings().all()]
    rows = result.all() if hasattr(result, "all") else []
    out: list[dict[str, object]] = []
    for row in rows:
        mapping = getattr(row, "_mapping", None)
        out.append(dict(mapping) if mapping is not None else dict(row))
    return out


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


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


def _pool_int(pool: object, method: str) -> int | None:
    fn = getattr(pool, method, None)
    if not callable(fn):
        return None
    try:
        return _as_int(fn())
    except Exception:
        return None


def _pool_snapshot(
    name: str, eng: AsyncEngine | None
) -> DiagnosticsDatabasePool | None:
    if eng is None:
        return None
    pool = eng.pool
    status = ""
    try:
        status = str(pool.status())
    except Exception:
        log.debug("pool.status() failed for %s", name, exc_info=True)
    return DiagnosticsDatabasePool(
        name=name,
        status=status,
        size=_pool_int(pool, "size"),
        checked_out=_pool_int(pool, "checkedout"),
        overflow=_pool_int(pool, "overflow"),
    )


def _request_engine() -> AsyncEngine | None:
    try:
        return get_engine()
    except RuntimeError:
        return None


def collect_pool_snapshots() -> list[DiagnosticsDatabasePool]:
    pools: list[DiagnosticsDatabasePool] = []
    for name, eng in (
        ("request", _request_engine()),
        ("background", background_engine),
    ):
        snap = _pool_snapshot(name, eng)
        if snap is not None:
            pools.append(snap)
    return pools


async def get_database_diagnostics(
    db: AsyncSession,
    *,
    config: MiraMediaConfig | None = None,
) -> DiagnosticsDatabase:
    db_config: DbConfig = (config or MiraMediaConfig()).database
    identity_rows = await _try_rows(db, _IDENTITY_SQL)
    identity = identity_rows[0] if identity_rows else {}
    connection_rows = await _try_rows(db, _CONNECTIONS_SQL)
    table_rows = await _try_rows(db, _TABLES_SQL)
    return DiagnosticsDatabase(
        generated_at=datetime.now(UTC),
        host=db_config.host,
        port=int(db_config.port),
        name=db_config.dbname,
        user=db_config.user,
        server_version=_as_str(identity.get("server_version")),
        size_bytes=_as_int(identity.get("size_bytes")),
        max_connections=_as_int(identity.get("max_connections")),
        started_at=_as_datetime(identity.get("started_at")),
        connections=[
            DiagnosticsDatabaseConnection(
                state=str(row.get("state") or "unknown"),
                count=_as_int0(row.get("count")),
            )
            for row in connection_rows
        ],
        pools=collect_pool_snapshots(),
        largest_tables=[
            DiagnosticsDatabaseTable(
                name=str(row.get("name") or ""),
                total_bytes=_as_int0(row.get("total_bytes")),
                table_bytes=_as_int0(row.get("table_bytes")),
                index_bytes=_as_int0(row.get("index_bytes")),
                estimated_rows=_as_int(row.get("estimated_rows")),
            )
            for row in table_rows
            if row.get("name")
        ],
    )
