"""PostgreSQL characterization and audit tests for the specials skip migration."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

from tests.pg_disposable import (
    assert_temporary_schema_name,
    disposable_database_sync_url,
    new_temporary_schema_name,
)

_C8_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "c8d2e3f4a5b6_persist_specials_skip.py"
)
_AUDIT_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "g5c6d7e8f9a0_audit_specials_skip_migration.py"
)
_B7_REVISION = "b7c1d2e3f4a5"
_C8_REVISION = "c8d2e3f4a5b6"
_AUDIT_REVISION = "g5c6d7e8f9a0"
_SCHEMA_PREFIX = "specials_migration_test"
pytestmark = pytest.mark.postgresql


def _load_migration(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pg_engine():
    url = disposable_database_sync_url()
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return engine


def _set_search_path(conn, schema: str) -> None:
    conn.execute(text(f'SET search_path TO "{schema}"'))


def _run_migration(conn, migration, *, direction: str) -> None:
    ctx = MigrationContext.configure(connection=conn)
    with Operations.context(ctx):
        if direction == "upgrade":
            migration.upgrade()
        else:
            migration.downgrade()


def _create_show_tables(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE show (
                id uuid PRIMARY KEY,
                external_id varchar NOT NULL,
                metadata_provider varchar NOT NULL,
                name varchar NOT NULL,
                overview varchar NOT NULL,
                year integer,
                ended boolean NOT NULL,
                continuous_download boolean,
                library varchar NOT NULL,
                skipped boolean NOT NULL,
                wanted_episode_count integer NOT NULL DEFAULT 0,
                downloaded_episode_count integer NOT NULL DEFAULT 0,
                list_progress_status varchar NOT NULL DEFAULT 'none'
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE season (
                id uuid PRIMARY KEY,
                show_id uuid NOT NULL REFERENCES show(id) ON DELETE CASCADE,
                number integer NOT NULL,
                skipped boolean NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE episode (
                id uuid PRIMARY KEY,
                season_id uuid NOT NULL REFERENCES season(id) ON DELETE CASCADE,
                number integer NOT NULL,
                title varchar NOT NULL,
                overview varchar,
                skipped boolean NOT NULL,
                downloaded boolean NOT NULL DEFAULT false
            )
            """
        )
    )


def _seed_specials(
    conn,
    *,
    show_id: uuid.UUID,
    season_id: uuid.UUID,
    episode_specs: list[tuple[uuid.UUID, int, bool, bool]],
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO show (
                id, external_id, metadata_provider, name, overview,
                ended, library, skipped
            ) VALUES (
                :show_id, 'ext-1', 'native', 'Test Show', '',
                false, '/data', false
            )
            """
        ),
        {"show_id": show_id},
    )
    conn.execute(
        text(
            """
            INSERT INTO season (id, show_id, number, skipped)
            VALUES (:season_id, :show_id, 0, false)
            """
        ),
        {"season_id": season_id, "show_id": show_id},
    )
    for episode_id, number, skipped, downloaded in episode_specs:
        conn.execute(
            text(
                """
                INSERT INTO episode (
                    id, season_id, number, title, skipped, downloaded
                ) VALUES (
                    :id, :season_id, :number, :title, :skipped, :downloaded
                )
                """
            ),
            {
                "id": episode_id,
                "season_id": season_id,
                "number": number,
                "title": f"Special {number}",
                "skipped": skipped,
                "downloaded": downloaded,
            },
        )


def _fetch_episode_skipped(conn, episode_id: uuid.UUID) -> bool:
    row = conn.execute(
        text("SELECT skipped FROM episode WHERE id = :id"),
        {"id": episode_id},
    ).first()
    assert row is not None
    return bool(row[0])


def _fetch_season_skipped(conn, season_id: uuid.UUID) -> bool:
    row = conn.execute(
        text("SELECT skipped FROM season WHERE id = :id"),
        {"id": season_id},
    ).first()
    assert row is not None
    return bool(row[0])


def _fetch_audit_rows(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT show_name, episode_number, episode_skipped, episode_downloaded,
                   skip_category
            FROM specials_skip_audit
            ORDER BY episode_number
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


_UV_EXECUTABLE = shutil.which("uv")


def _run_alembic(
    sync_url: str, *args: str, **env_overrides: str
) -> subprocess.CompletedProcess[str]:
    if _UV_EXECUTABLE is None:
        pytest.fail("uv executable not found on PATH")
    env = {**os.environ, "DATABASE_URL": sync_url, **env_overrides}
    return subprocess.run(  # noqa: S603
        [_UV_EXECUTABLE, "run", "--python", "3.13", "alembic", *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def _truncate_public_tables(sync_url: str) -> None:
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname = 'public'
                      AND tablename <> 'alembic_version'
                    ORDER BY tablename
                    """
                )
            )
            tables = [row[0] for row in result]
            if tables:
                quoted = ", ".join(f'"{name}"' for name in tables)
                conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()


def _seed_specials_on_public(sync_url: str) -> dict[str, uuid.UUID]:
    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    ep_wanted = uuid.uuid4()
    ep_downloaded = uuid.uuid4()
    ep_skipped = uuid.uuid4()
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.begin() as conn:
            _seed_specials(
                conn,
                show_id=show_id,
                season_id=season_id,
                episode_specs=[
                    (ep_wanted, 1, False, False),
                    (ep_downloaded, 2, False, True),
                    (ep_skipped, 3, True, False),
                ],
            )
    finally:
        engine.dispose()
    return {
        "show_id": show_id,
        "season_id": season_id,
        "ep_wanted": ep_wanted,
        "ep_downloaded": ep_downloaded,
        "ep_skipped": ep_skipped,
    }


@pytest.fixture
def pg_specials_schema():
    engine = _pg_engine()
    c8_migration = _load_migration(_C8_MIGRATION_PATH, "persist_specials_skip")
    audit_migration = _load_migration(
        _AUDIT_MIGRATION_PATH, "audit_specials_skip_migration"
    )
    schema = new_temporary_schema_name(prefix=_SCHEMA_PREFIX)
    conn = engine.connect()
    conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    _set_search_path(conn, schema)
    _create_show_tables(conn)
    conn.commit()
    try:
        yield conn, c8_migration, audit_migration, engine, schema
    finally:
        conn.close()
        with engine.connect() as cleanup:
            assert_temporary_schema_name(schema, prefix=_SCHEMA_PREFIX)
            cleanup.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            cleanup.commit()
        engine.dispose()


def test_audit_migration_source_documents_limits_and_skips_config() -> None:
    source = _AUDIT_MIGRATION_PATH.read_text(encoding="utf-8")
    lowered = source.lower()
    assert "do not" in lowered
    assert "mass-rewrite" in lowered
    assert "distinguished from the one-time backfill" in lowered
    assert "MiraMediaConfig" not in source
    assert "specials_skip_audit" in source


def test_c8_disabled_branch_backfills_undownloaded_specials(
    pg_specials_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, migration, _audit, _engine, _schema = pg_specials_schema
    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    ep_wanted = uuid.uuid4()
    ep_downloaded = uuid.uuid4()
    ep_skipped = uuid.uuid4()
    _seed_specials(
        conn,
        show_id=show_id,
        season_id=season_id,
        episode_specs=[
            (ep_wanted, 1, False, False),
            (ep_downloaded, 2, False, True),
            (ep_skipped, 3, True, False),
        ],
    )

    class StubConfig:
        def __init__(self) -> None:
            self.misc = SimpleNamespace(download_specials=False)

    monkeypatch.setattr("miramedia.config.MiraMediaConfig", StubConfig)
    _run_migration(conn, migration, direction="upgrade")

    assert _fetch_season_skipped(conn, season_id) is True
    assert _fetch_episode_skipped(conn, ep_wanted) is True
    assert _fetch_episode_skipped(conn, ep_downloaded) is False
    assert _fetch_episode_skipped(conn, ep_skipped) is True


def test_c8_enabled_branch_leaves_skip_flags(
    pg_specials_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, migration, _audit, _engine, _schema = pg_specials_schema
    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    ep_wanted = uuid.uuid4()
    ep_downloaded = uuid.uuid4()
    ep_skipped = uuid.uuid4()
    _seed_specials(
        conn,
        show_id=show_id,
        season_id=season_id,
        episode_specs=[
            (ep_wanted, 1, False, False),
            (ep_downloaded, 2, False, True),
            (ep_skipped, 3, True, False),
        ],
    )

    class StubConfig:
        def __init__(self) -> None:
            self.misc = SimpleNamespace(download_specials=True)

    monkeypatch.setattr("miramedia.config.MiraMediaConfig", StubConfig)
    _run_migration(conn, migration, direction="upgrade")

    assert _fetch_season_skipped(conn, season_id) is False
    assert _fetch_episode_skipped(conn, ep_wanted) is False
    assert _fetch_episode_skipped(conn, ep_downloaded) is False
    assert _fetch_episode_skipped(conn, ep_skipped) is True


def test_audit_migration_preserves_user_unskip_after_backfill(
    pg_specials_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, c8_migration, audit_migration, _engine, _schema = pg_specials_schema
    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    ep_wanted = uuid.uuid4()
    _seed_specials(
        conn,
        show_id=show_id,
        season_id=season_id,
        episode_specs=[(ep_wanted, 1, False, False)],
    )

    class DisabledSpecials:
        def __init__(self) -> None:
            self.misc = SimpleNamespace(download_specials=False)

    monkeypatch.setattr("miramedia.config.MiraMediaConfig", DisabledSpecials)
    _run_migration(conn, c8_migration, direction="upgrade")
    assert _fetch_episode_skipped(conn, ep_wanted) is True

    conn.execute(
        text("UPDATE episode SET skipped = false WHERE id = :id"),
        {"id": ep_wanted},
    )
    _run_migration(conn, audit_migration, direction="upgrade")

    assert _fetch_episode_skipped(conn, ep_wanted) is False
    rows = _fetch_audit_rows(conn)
    assert len(rows) == 1
    assert rows[0]["skip_category"] == "wanted_undownloaded"


def test_audit_migration_is_idempotent(pg_specials_schema) -> None:
    conn, _c8, audit_migration, _engine, _schema = pg_specials_schema
    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    ep_id = uuid.uuid4()
    _seed_specials(
        conn,
        show_id=show_id,
        season_id=season_id,
        episode_specs=[(ep_id, 1, True, False)],
    )

    _run_migration(conn, audit_migration, direction="upgrade")
    first = _fetch_audit_rows(conn)
    _run_migration(conn, audit_migration, direction="upgrade")
    second = _fetch_audit_rows(conn)
    assert first == second


def test_audit_migration_downgrade_drops_view(pg_specials_schema) -> None:
    conn, _c8, audit_migration, _engine, _schema = pg_specials_schema
    show_id = uuid.uuid4()
    season_id = uuid.uuid4()
    ep_id = uuid.uuid4()
    _seed_specials(
        conn,
        show_id=show_id,
        season_id=season_id,
        episode_specs=[(ep_id, 1, True, False)],
    )

    _run_migration(conn, audit_migration, direction="upgrade")
    _run_migration(conn, audit_migration, direction="downgrade")

    row = conn.execute(
        text(
            """
            SELECT COUNT(*) FROM pg_views
            WHERE schemaname = current_schema()
              AND viewname = 'specials_skip_audit'
            """
        )
    ).first()
    assert row is not None
    assert int(row[0]) == 0


def test_alembic_c8_branches_diverge_then_audit_preserves_rows() -> None:
    sync_url = disposable_database_sync_url()
    restore_error: str | None = None

    try:
        downgrade = _run_alembic(sync_url, "downgrade", _B7_REVISION)
        assert downgrade.returncode == 0, downgrade.stderr + downgrade.stdout
        _truncate_public_tables(sync_url)
        ids = _seed_specials_on_public(sync_url)

        disabled = _run_alembic(
            sync_url,
            "upgrade",
            _C8_REVISION,
            MIRAMEDIA_MISC__DOWNLOAD_SPECIALS="false",
        )
        assert disabled.returncode == 0, disabled.stderr + disabled.stdout

        engine = create_engine(sync_url, pool_pre_ping=True)
        with engine.connect() as conn:
            assert _fetch_season_skipped(conn, ids["season_id"]) is True
            assert _fetch_episode_skipped(conn, ids["ep_wanted"]) is True
            assert _fetch_episode_skipped(conn, ids["ep_downloaded"]) is False
            conn.execute(
                text("UPDATE episode SET skipped = false WHERE id = :id"),
                {"id": ids["ep_wanted"]},
            )
            conn.commit()

        audit = _run_alembic(sync_url, "upgrade", _AUDIT_REVISION)
        assert audit.returncode == 0, audit.stderr + audit.stdout

        with engine.connect() as conn:
            assert _fetch_episode_skipped(conn, ids["ep_wanted"]) is False
            rows = conn.execute(
                text(
                    """
                    SELECT skip_category FROM specials_skip_audit
                    WHERE episode_id = :id
                    """
                ),
                {"id": ids["ep_wanted"]},
            ).first()
            assert rows is not None
            assert rows[0] == "wanted_undownloaded"
        engine.dispose()

        downgrade_b7 = _run_alembic(sync_url, "downgrade", _B7_REVISION)
        assert downgrade_b7.returncode == 0, downgrade_b7.stderr + downgrade_b7.stdout
        _truncate_public_tables(sync_url)
        ids_enabled = _seed_specials_on_public(sync_url)

        enabled = _run_alembic(
            sync_url,
            "upgrade",
            _C8_REVISION,
            MIRAMEDIA_MISC__DOWNLOAD_SPECIALS="true",
        )
        assert enabled.returncode == 0, enabled.stderr + enabled.stdout

        engine = create_engine(sync_url, pool_pre_ping=True)
        with engine.connect() as conn:
            assert _fetch_season_skipped(conn, ids_enabled["season_id"]) is False
            assert _fetch_episode_skipped(conn, ids_enabled["ep_wanted"]) is False
        engine.dispose()
    finally:
        restore = _run_alembic(sync_url, "upgrade", "head")
        if restore.returncode != 0:
            restore_error = restore.stderr + restore.stdout

    if restore_error is not None:
        pytest.fail(restore_error)
