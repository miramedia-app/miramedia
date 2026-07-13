"""Migration smoke and PostgreSQL JSONB semantics."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text

from miramedia.imports.models import ScanResultCache
from tests.integration._db_url import alembic_sync_url
from tests.integration.builders import seed_pending_scan_row

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _repo_head_revision() -> str:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    return heads[0]


def _database_revision(sync_url: str) -> str:
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert row is not None
            return str(row[0])
    finally:
        engine.dispose()


def test_repository_head_is_singular() -> None:
    head = _repo_head_revision()
    assert head.strip()


def test_database_revision_matches_repository_head(integration_db_url: str) -> None:
    sync_url = alembic_sync_url(integration_db_url)
    assert _database_revision(sync_url) == _repo_head_revision()


def test_alembic_upgrade_head_is_idempotent(integration_db_url: str) -> None:
    sync_url = alembic_sync_url(integration_db_url)
    env = {**os.environ, "DATABASE_URL": sync_url}
    proc = subprocess.run(
        ["uv", "run", "--python", "3.13", "alembic", "upgrade", "head"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    assert _database_revision(sync_url) == _repo_head_revision()


async def _jsonb_round_trip(db) -> None:
    directory = f"/integration/jsonb/{uuid.uuid4()}"
    await seed_pending_scan_row(
        db,
        directory=directory,
        status="failed",
        extra_payload={"import_error": "boom", "nullable": None},
    )
    row = (
        await db.execute(
            select(ScanResultCache).where(ScanResultCache.directory == directory)
        )
    ).scalar_one()
    assert row.payload["status"] == "failed"
    assert row.payload["import_error"] == "boom"
    assert row.payload["nullable"] is None

    await db.execute(
        text(
            """
            UPDATE scan_result_cache
            SET payload = payload || jsonb_build_object(
                'status', 'queued',
                'claim_token', CAST(:token AS text)
            )
            WHERE directory = :directory
            """
        ),
        {"directory": directory, "token": str(uuid.uuid4())},
    )
    await db.commit()

    refreshed = (
        await db.execute(
            select(ScanResultCache.payload).where(
                ScanResultCache.directory == directory
            )
        )
    ).scalar_one()
    assert refreshed["status"] == "queued"
    assert "claim_token" in refreshed
    assert refreshed["import_error"] == "boom"


def test_jsonb_payload_round_trip_and_nullable_keys(db, run_async) -> None:
    run_async(_jsonb_round_trip(db))
