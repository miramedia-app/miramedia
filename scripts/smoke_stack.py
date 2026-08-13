#!/usr/bin/env python3
"""Launch a disposable FastAPI + static-export stack for real-browser smoke tests.

Ports and health checks
-----------------------
* **SMOKE_PORT** (default ``43219``): FastAPI serves the built static export and
  the live API on ``http://localhost:<port>``.
* Readiness: ``GET http://localhost:<port>/api/v1/health`` with ``db.ok == true``.
* Credentials for Playwright are written to **SMOKE_CREDS_FILE** (default
  ``web/.smoke-credentials.json``); the file is chmod 0600 and never logged.

Environment
-----------
* **MIRAMEDIA_SMOKE_DATABASE_URL** or **MIRAMEDIA_TEST_DATABASE_URL**: parent
  PostgreSQL URL whose database name contains ``test``, ``integration``, or
  ``smoke``. A disposable ``miramedia_smoke_<id>`` database is created and dropped
  on teardown.
* **FRONTEND_FILES_DIR** (default ``web/out``): static export directory from
  ``make frontend-build``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )


def _repo_path(*parts: str) -> Path:
    return REPO_ROOT.joinpath(*parts)


def _default_frontend_dir() -> Path:
    return _repo_path("web", "out")


def _default_creds_file() -> Path:
    return _repo_path("web", ".smoke-credentials.json")


def _parent_database_url() -> str:
    raw = os.environ.get("MIRAMEDIA_SMOKE_DATABASE_URL") or os.environ.get(
        "MIRAMEDIA_TEST_DATABASE_URL"
    )
    if not raw:
        msg = (
            "Smoke stack requires MIRAMEDIA_SMOKE_DATABASE_URL or "
            "MIRAMEDIA_TEST_DATABASE_URL pointing at disposable PostgreSQL"
        )
        raise SystemExit(msg)
    return raw.replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic_upgrade(sync_url: str) -> None:
    env = {
        **os.environ,
        "DATABASE_URL": sync_url,
        "MIRAMEDIA_LOG_FILE": os.environ.get("MIRAMEDIA_LOG_FILE", "/dev/null"),
    }
    proc = subprocess.run(
        ["uv", "run", "--python", "3.13", "alembic", "upgrade", "head"],  # noqa: S607
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        msg = "alembic upgrade head failed for smoke database"
        raise SystemExit(msg)


def _write_credentials(path: Path, email: str, password: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"email": email, "password": password}),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _build_uvicorn_command(port: int) -> list[str]:
    return [
        "uv",
        "run",
        "--python",
        "3.13",
        "fastapi",
        "run",
        "miramedia/main.py",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--proxy-headers",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SMOKE_PORT", "43219")),
        help="Port for the combined static SPA + API server (default: 43219)",
    )
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=Path(os.environ.get("FRONTEND_FILES_DIR", _default_frontend_dir())),
        help="Built static export directory (default: web/out)",
    )
    parser.add_argument(
        "--creds-file",
        type=Path,
        default=Path(os.environ.get("SMOKE_CREDS_FILE", _default_creds_file())),
        help="Where to write disposable admin credentials for Playwright",
    )
    parser.add_argument(
        "--skip-server",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    _configure_logging()
    log = logging.getLogger("smoke_stack")

    if not args.frontend_dir.is_dir():
        msg = (
            f"Static export not found at {args.frontend_dir}. "
            "Run `make frontend-build` first."
        )
        raise SystemExit(msg)

    port = args.port
    base_url = f"http://localhost:{port}"
    parent_url = _parent_database_url()

    from sqlalchemy.engine.url import make_url

    from miramedia.smoke.config import write_smoke_config
    from miramedia.smoke.database import (
        alembic_sync_url,
        create_smoke_database,
        drop_smoke_database,
        smoke_database_name,
    )
    from miramedia.smoke.health import wait_for_smoke_stack_ready

    parent = make_url(parent_url)
    dbname = smoke_database_name()
    smoke_url = create_smoke_database(parent_url, dbname)
    sync_url = alembic_sync_url(smoke_url)

    temp_root = Path(tempfile.mkdtemp(prefix="miramedia-smoke-"))
    config_dir = temp_root / "config"
    data_root = temp_root / "data"
    token_secret = secrets.token_hex(32)

    cleanup_state = {"done": False}

    def cleanup() -> None:
        if cleanup_state["done"]:
            return
        cleanup_state["done"] = True
        try:
            drop_smoke_database(parent_url, dbname)
        except Exception:
            log.exception("Failed to drop smoke database %s", dbname)
        creds_path = args.creds_file
        if creds_path.exists():
            creds_path.unlink()
        shutil.rmtree(temp_root, ignore_errors=True)

    os.environ.update(
        {
            "MIRAMEDIA_CONFIG_DIR": str(config_dir),
            "MIRAMEDIA_CONFIG_FILE": str(config_dir / "config.toml"),
            "MIRAMEDIA_AUTH__TOKEN_SECRET": token_secret,
            "MIRAMEDIA_LOG_FILE": os.environ.get("MIRAMEDIA_LOG_FILE", "/dev/null"),
            "MIRAMEDIA_SCHEDULER_DISABLED": "true",
            "MIRAMEDIA_EVENT_BRIDGE_DISABLED": "true",
            "MIRAMEDIA_LIBRARY_WATCHER": "false",
            "FRONTEND_FILES_DIR": str(args.frontend_dir.resolve()),
            "DATABASE_URL": sync_url,
            "SMOKE_CREDS_FILE": str(args.creds_file.resolve()),
            "SMOKE_PORT": str(port),
        }
    )

    write_smoke_config(
        config_dir=config_dir,
        frontend_url=base_url,
        database_host=parent.host or "127.0.0.1",
        database_port=parent.port or 5432,
        database_user=parent.username or "",
        database_password=parent.password or "",
        database_name=dbname,
        data_root=data_root,
        token_secret=token_secret,
    )

    log.info("Running migrations on disposable database %s", dbname)
    _run_alembic_upgrade(sync_url)

    from miramedia.smoke.seed import seed_disposable_admin

    credentials = asyncio.run(seed_disposable_admin())
    _write_credentials(args.creds_file, credentials.email, credentials.password)
    log.info("Seeded disposable smoke admin at %s", credentials.email)

    if args.skip_server:
        cleanup()
        return 0

    cmd = _build_uvicorn_command(port)
    log.info(
        "Starting smoke stack at %s (health: %s/api/v1/health)", base_url, base_url
    )
    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=REPO_ROOT,
        env=os.environ.copy(),
    )

    shutdown_requested = False

    def _terminate_server() -> None:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _request_shutdown(signum: int, _frame: object) -> None:
        nonlocal shutdown_requested
        shutdown_requested = True
        log.info("Smoke stack shutdown requested (signal %s)", signum)
        _terminate_server()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)

    exit_code = 0
    try:
        wait_for_smoke_stack_ready(base_url)
        log.info("Smoke stack ready")
        while proc.poll() is None and not shutdown_requested:
            time.sleep(0.25)
        exit_code = proc.returncode or 0
    except Exception:
        _terminate_server()
        raise
    finally:
        cleanup()

    if shutdown_requested:
        return 128 + signal.SIGTERM
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
