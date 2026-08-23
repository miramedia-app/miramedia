"""Plan 082 audit chunk: DB session closed before filesystem hashing."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import miramedia.scheduler as scheduler
from tests.fakes.config import fake_scheduler_config
from tests.integration.builders import insert_show_episode_file

pytestmark = pytest.mark.integration

_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_verify_imported_files_hashes_after_background_session_closed(
    db,
    session_factory,
    run_async,
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def _run_test() -> None:
        _show, episode_file = await insert_show_episode_file(db, sha1=_SHA)
        media = tmp_path / "episode.mkv"
        media.write_bytes(b"verified")

        session_depth = {"n": 0}

        @asynccontextmanager
        async def integration_background_session():
            session_depth["n"] += 1
            try:
                async with session_factory() as session:
                    yield session
            finally:
                session_depth["n"] -= 1

        cfg = fake_scheduler_config(integrity_check_enabled=True)
        monkeypatch.setattr(
            "miramedia.scheduler_tasks.integrity.MiraMediaConfig", lambda: cfg
        )
        monkeypatch.setattr("miramedia.torrents.integrity.MiraMediaConfig", lambda: cfg)
        monkeypatch.setattr(
            "miramedia.scheduler_tasks.integrity.background_session",
            integration_background_session,
        )
        monkeypatch.setattr(
            "miramedia.torrents.integrity.batch_resolve_episode_paths_async",
            AsyncMock(return_value={episode_file.id: media}),
        )
        monkeypatch.setattr(
            "miramedia.torrents.integrity.batch_resolve_movie_paths_async",
            AsyncMock(return_value={}),
        )

        async def compute_after_session_close(_path: Path) -> str:
            assert session_depth["n"] == 0, "hash ran while background session open"
            return _SHA

        monkeypatch.setattr(
            "miramedia.scheduler_tasks.integrity.compute_sha1_async",
            compute_after_session_close,
        )

        await scheduler.verify_imported_files_task()

    run_async(_run_test())
