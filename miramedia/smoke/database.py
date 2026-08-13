"""Disposable PostgreSQL database lifecycle for the smoke stack."""

from __future__ import annotations

import re
import uuid
from urllib.parse import quote_plus, urlparse

import psycopg
from psycopg import sql
from sqlalchemy.engine.url import make_url

_SMOKE_DB_PREFIX = "miramedia_smoke_"
_ADMIN_DB = "postgres"


def _assert_smoke_database_name(dbname: str) -> None:
    if not dbname.startswith(_SMOKE_DB_PREFIX) or not re.fullmatch(
        r"[A-Za-z0-9_]+", dbname
    ):
        msg = f"Refusing SQL against unexpected smoke database name {dbname!r}"
        raise ValueError(msg)


def smoke_database_name(run_id: str | None = None) -> str:
    token = (run_id or uuid.uuid4().hex)[:12]
    return f"{_SMOKE_DB_PREFIX}{token}"


def admin_database_url(database_url: str) -> str:
    url = make_url(database_url)
    driver = "postgresql" if url.drivername.startswith("postgresql") else url.drivername
    return (
        f"{driver}://{quote_plus(url.username or '')}:{quote_plus(url.password or '')}"
        f"@{url.host}:{url.port}/{_ADMIN_DB}"
    )


def database_url_with_name(database_url: str, dbname: str) -> str:
    url = make_url(database_url)
    return url.set(database=dbname).render_as_string(hide_password=False)


def alembic_sync_url(database_url: str) -> str:
    url = make_url(database_url)
    return url.set(drivername="postgresql+psycopg").render_as_string(
        hide_password=False
    )


def assert_safe_smoke_parent_database(database_url: str) -> None:
    """Refuse to create smoke databases against production-looking parents."""
    dbname = urlparse(
        database_url.replace("+asyncpg", "").replace("+psycopg", "")
    ).path.lstrip("/")
    if not dbname:
        msg = "Smoke parent database URL must include a database name"
        raise ValueError(msg)
    if not re.search(r"(test|integration|smoke)", dbname, re.IGNORECASE):
        msg = (
            f"Refusing smoke database creation against parent {dbname!r}. "
            "Point MIRAMEDIA_TEST_DATABASE_URL (or MIRAMEDIA_SMOKE_DATABASE_URL) "
            "at a disposable database whose name contains test, integration, or smoke."
        )
        raise ValueError(msg)


def create_smoke_database(parent_database_url: str, dbname: str) -> str:
    assert_safe_smoke_parent_database(parent_database_url)
    _assert_smoke_database_name(dbname)
    admin_url = admin_database_url(parent_database_url)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    return database_url_with_name(parent_database_url, dbname)


def drop_smoke_database(parent_database_url: str, dbname: str) -> None:
    _assert_smoke_database_name(dbname)
    admin_url = admin_database_url(parent_database_url)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = %s AND pid <> pg_backend_pid()
                """,
                (dbname,),
            )
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(dbname))
            )
