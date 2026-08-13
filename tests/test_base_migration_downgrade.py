"""Guards for the base migration downgrade floor."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "3ae9e0afdc49_initial_schema.py"
)
_BASE_REVISION = "3ae9e0afdc49"


def _load_migration():
    spec = importlib.util.spec_from_file_location("initial_schema", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_base_migration_source_does_not_drop_public_schema() -> None:
    source = _MIGRATION_PATH.read_text(encoding="utf-8")
    assert "DROP SCHEMA public CASCADE" not in source


def test_base_migration_downgrade_refuses_below_floor() -> None:
    migration = _load_migration()
    with pytest.raises(RuntimeError, match="Refusing to downgrade below the base"):
        migration.downgrade()


def test_base_migration_downgrade_message_names_supported_floor() -> None:
    migration = _load_migration()
    with pytest.raises(RuntimeError, match=_BASE_REVISION):
        migration.downgrade()
