"""Verify Alembic has a single head and (optionally) matches a live database."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(_repo_root() / "alembic.ini"))
    cfg.set_main_option("script_location", str(_repo_root() / "alembic"))
    return cfg


def repo_head_revision() -> str:
    script = ScriptDirectory.from_config(_alembic_config())
    heads = script.get_heads()
    if not heads:
        msg = "No Alembic heads found in the repository"
        raise SystemExit(msg)
    if len(heads) > 1:
        msg = f"Multiple Alembic heads detected: {', '.join(heads)}"
        raise SystemExit(msg)
    return heads[0]


def database_revision(url: str) -> str | None:
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            try:
                row = conn.execute(
                    text("SELECT version_num FROM alembic_version")
                ).first()
            except Exception as exc:
                msg = f"Failed to read alembic_version: {exc}"
                raise SystemExit(msg) from exc
            return None if row is None else str(row[0])
    finally:
        engine.dispose()


def main() -> None:
    head = repo_head_revision()
    sys.stdout.write(f"repository head: {head}\n")

    if "--verify-db" not in sys.argv:
        return

    url = os.environ.get("DATABASE_URL")
    if not url:
        msg = "--verify-db requires DATABASE_URL"
        raise SystemExit(msg)

    db_rev = database_revision(url)
    if db_rev is None:
        msg = "alembic_version is empty — run `alembic upgrade head` first"
        raise SystemExit(msg)
    sys.stdout.write(f"database revision: {db_rev}\n")
    if db_rev != head:
        msg = f"database revision {db_rev!r} != repository head {head!r}"
        raise SystemExit(msg)


if __name__ == "__main__":
    main()
