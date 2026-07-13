"""Regression tests for Alembic migration URL resolution (no live database)."""

from __future__ import annotations

from configparser import ConfigParser
from unittest.mock import MagicMock

import pytest
from alembic.config import Config

from miramedia.database.migration_url import (
    migration_url_for_alembic_config,
    resolve_migration_url_raw,
)


def test_explicit_database_url_percent_is_doubled_for_alembic_config() -> None:
    raw = "postgresql+psycopg://user:p%25word@localhost:5432/testdb"
    escaped = migration_url_for_alembic_config(raw)
    assert escaped == "postgresql+psycopg://user:p%%25word@localhost:5432/testdb"

    cfg = Config()
    cfg.set_main_option("sqlalchemy.url", escaped)
    assert cfg.get_main_option("sqlalchemy.url") == raw


def test_fallback_rendered_url_percent_is_doubled_for_alembic_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db = MagicMock()
    db.user = "user%name"
    db.password = "p@ss:w/rd%"
    db.host = "db.example.com"
    db.port = 5432
    db.dbname = "miramedia_integration_test"
    cfg = MagicMock()
    cfg.database = db
    monkeypatch.setattr("miramedia.database.migration_url.MiraMediaConfig", lambda: cfg)

    raw = resolve_migration_url_raw()
    escaped = migration_url_for_alembic_config(raw)
    assert "%%" in escaped
    assert "%" in raw

    alembic_cfg = Config()
    alembic_cfg.set_main_option("sqlalchemy.url", escaped)
    assert alembic_cfg.get_main_option("sqlalchemy.url") == raw

    parser = ConfigParser()
    parser.read_dict({"alembic": {"sqlalchemy.url": escaped}})
    assert parser.get("alembic", "sqlalchemy.url") == raw


def test_offline_alembic_config_roundtrip_without_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://int%25:sec%40ret@127.0.0.1:5432/miramedia_integration_test",
    )
    escaped = migration_url_for_alembic_config()
    ini = Config()
    ini.set_main_option("sqlalchemy.url", escaped)
    offline_url = ini.get_main_option("sqlalchemy.url")
    assert offline_url == resolve_migration_url_raw()
    assert "integration_test" in offline_url
