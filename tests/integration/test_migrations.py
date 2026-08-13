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
from sqlalchemy.engine import make_url

from miramedia.imports.models import ScanResultCache
from tests.integration._db_url import alembic_sync_url
from tests.integration.builders import seed_pending_scan_row
from tests.pg_disposable import (
    DisposableDatabaseRejectedError,
    database_name_from_url,
    validate_disposable_database_name,
)

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASE_REVISION = "3ae9e0afdc49"


def _script_directory() -> ScriptDirectory:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def _repo_head_revision() -> str:
    heads = _script_directory().get_heads()
    assert len(heads) == 1
    return heads[0]


def _immediate_predecessor_revision(head: str) -> str:
    revision = _script_directory().get_revision(head)
    predecessor = revision.down_revision
    if predecessor is None:
        msg = f"head revision {head} has no predecessor to downgrade to"
        raise AssertionError(msg)
    if isinstance(predecessor, tuple):
        msg = (
            f"head revision {head} has multiple predecessors {predecessor}; "
            "cannot select a single immediate predecessor"
        )
        raise TypeError(msg)
    return predecessor


def _assert_disposable_database_for_alembic_downgrade(sync_url: str) -> None:
    """Refuse destructive Alembic commands unless the URL is a disposable test DB."""
    validate_disposable_database_name(database_name_from_url(sync_url))


def _sanitize_database_url(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)


def _alembic_subprocess_env(sync_url: str) -> dict[str, str]:
    return {**os.environ, "DATABASE_URL": sync_url}


def _format_alembic_failure(
    proc: subprocess.CompletedProcess[str], sync_url: str
) -> str:
    safe_url = _sanitize_database_url(sync_url)
    return (
        f"alembic command failed (database={safe_url})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


def _run_alembic(sync_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["uv", "run", "--python", "3.13", "alembic", *args],
        check=False,
        capture_output=True,
        text=True,
        env=_alembic_subprocess_env(sync_url),
    )


def _restore_head_revision(sync_url: str) -> subprocess.CompletedProcess[str]:
    _assert_disposable_database_for_alembic_downgrade(sync_url)
    return _run_alembic(sync_url, "upgrade", "head")


def _database_revision(sync_url: str) -> str:
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
            assert row is not None
            return str(row[0])
    finally:
        engine.dispose()


def _plant_unrelated_sentinel(sync_url: str) -> None:
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS plan246_unrelated_sentinel (
                        id integer PRIMARY KEY
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "INSERT INTO plan246_unrelated_sentinel (id) VALUES (1) "
                    "ON CONFLICT DO NOTHING"
                )
            )
    finally:
        engine.dispose()


def _sentinel_row_count(sync_url: str) -> int:
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) FROM plan246_unrelated_sentinel")
            ).first()
            assert row is not None
            return int(row[0])
    finally:
        engine.dispose()


def _drop_unrelated_sentinel(sync_url: str) -> None:
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS plan246_unrelated_sentinel"))
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
    proc = _run_alembic(sync_url, "upgrade", "head")
    assert proc.returncode == 0, _format_alembic_failure(proc, sync_url)
    assert _database_revision(sync_url) == _repo_head_revision()


def test_disposable_database_guard_rejects_application_database() -> None:
    with pytest.raises(DisposableDatabaseRejectedError, match="miramedia"):
        _assert_disposable_database_for_alembic_downgrade(
            "postgresql+psycopg://miramedia:secret@127.0.0.1:5433/miramedia"
        )


@pytest.mark.parametrize("run_index", [1, 2], ids=["first-run", "second-run"])
def test_alembic_reversible_traversal_head_to_immediate_predecessor(
    integration_db_url: str, run_index: int
) -> None:
    del run_index
    sync_url = alembic_sync_url(integration_db_url)
    _assert_disposable_database_for_alembic_downgrade(sync_url)

    head = _repo_head_revision()
    predecessor = _immediate_predecessor_revision(head)
    restore_error: str | None = None

    try:
        downgrade = _run_alembic(sync_url, "downgrade", predecessor)
        assert downgrade.returncode == 0, _format_alembic_failure(downgrade, sync_url)
        assert _database_revision(sync_url) == predecessor

        upgrade = _run_alembic(sync_url, "upgrade", "head")
        assert upgrade.returncode == 0, _format_alembic_failure(upgrade, sync_url)
        assert _database_revision(sync_url) == head
    finally:
        restore = _restore_head_revision(sync_url)
        if restore.returncode != 0:
            restore_error = _format_alembic_failure(restore, sync_url)
        elif _database_revision(sync_url) != head:
            restore_error = (
                f"failed to restore head revision {head} "
                f"(database={_sanitize_database_url(sync_url)}, "
                f"actual={_database_revision(sync_url)!r})"
            )

    if restore_error is not None:
        pytest.fail(restore_error)


def test_alembic_downgrade_to_base_floor_then_refuses_below(
    integration_db_url: str,
) -> None:
    sync_url = alembic_sync_url(integration_db_url)
    _assert_disposable_database_for_alembic_downgrade(sync_url)

    head = _repo_head_revision()
    restore_error: str | None = None

    try:
        to_floor = _run_alembic(sync_url, "downgrade", _BASE_REVISION)
        assert to_floor.returncode == 0, _format_alembic_failure(to_floor, sync_url)
        assert _database_revision(sync_url) == _BASE_REVISION

        _plant_unrelated_sentinel(sync_url)

        below_floor = _run_alembic(sync_url, "downgrade", "base")
        assert below_floor.returncode != 0, (
            "expected downgrade below base to fail; "
            f"stdout:\n{below_floor.stdout}\nstderr:\n{below_floor.stderr}"
        )
        assert "Refusing to downgrade below the base migration revision" in (
            below_floor.stderr + below_floor.stdout
        )
        assert _database_revision(sync_url) == _BASE_REVISION
        assert _sentinel_row_count(sync_url) == 1

        upgrade = _run_alembic(sync_url, "upgrade", "head")
        assert upgrade.returncode == 0, _format_alembic_failure(upgrade, sync_url)
        assert _database_revision(sync_url) == head
    finally:
        _drop_unrelated_sentinel(sync_url)
        restore = _restore_head_revision(sync_url)
        if restore.returncode != 0:
            restore_error = _format_alembic_failure(restore, sync_url)
        elif _database_revision(sync_url) != head:
            restore_error = (
                f"failed to restore head revision {head} "
                f"(database={_sanitize_database_url(sync_url)}, "
                f"actual={_database_revision(sync_url)!r})"
            )

    if restore_error is not None:
        pytest.fail(restore_error)


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


_WATCHLIST_REVISION = "l0a1b2c3d4e5"


def test_upgrade_and_downgrade_traverse_new_watchlist_revision(
    integration_db_url: str,
) -> None:
    sync_url = alembic_sync_url(integration_db_url)
    _assert_disposable_database_for_alembic_downgrade(sync_url)

    head = _repo_head_revision()
    assert head == _WATCHLIST_REVISION
    predecessor = _immediate_predecessor_revision(head)
    assert predecessor == "k9f0a1b2c3d4"
    restore_error: str | None = None

    try:
        downgrade = _run_alembic(sync_url, "downgrade", predecessor)
        assert downgrade.returncode == 0, _format_alembic_failure(downgrade, sync_url)
        assert _database_revision(sync_url) == predecessor

        engine = create_engine(sync_url, pool_pre_ping=True)
        try:
            with engine.connect() as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        text(
                            """
                            SELECT tablename
                            FROM pg_tables
                            WHERE schemaname = 'public'
                              AND tablename IN (
                                'media_watch_state',
                                'watchlist',
                                'watchlist_item'
                              )
                            """
                        )
                    )
                }
            assert tables == set()
        finally:
            engine.dispose()

        upgrade = _run_alembic(sync_url, "upgrade", "head")
        assert upgrade.returncode == 0, _format_alembic_failure(upgrade, sync_url)
        assert _database_revision(sync_url) == head
    finally:
        restore = _restore_head_revision(sync_url)
        if restore.returncode != 0:
            restore_error = _format_alembic_failure(restore, sync_url)
        elif _database_revision(sync_url) != head:
            restore_error = (
                f"failed to restore head revision {head} "
                f"(database={_sanitize_database_url(sync_url)}, "
                f"actual={_database_revision(sync_url)!r})"
            )

    if restore_error is not None:
        pytest.fail(restore_error)
