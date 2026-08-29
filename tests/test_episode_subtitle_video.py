"""Subtitle search should use imported-file paths and stay quiet when missing."""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from miramedia.file_status import ImportOutcome
from miramedia.shows.schemas import EpisodeId
from miramedia.subtitles.service import SubtitleService
from miramedia.torrents.schemas import Quality


def _imported_file() -> MagicMock:
    row = MagicMock()
    row.import_status = ImportOutcome.imported
    row.quality = Quality.fullhd
    row.codec = ""
    row.hdr = False
    row.source = ""
    row.variant = ""
    row.extra = ""
    return row


def test_resolve_episode_video_prefers_imported_file_path(tmp_path: Path) -> None:
    video = tmp_path / "Show.S01E01.1080p.mkv"
    video.write_bytes(b"x")
    episode_id = EpisodeId(uuid.uuid4())
    imported = _imported_file()

    show_service = MagicMock()
    show_service.show_repository.get_episode_files_by_episode_id = AsyncMock(
        return_value=[imported]
    )
    show_service.resolve_episode_file_path = AsyncMock(return_value=video)
    svc = SubtitleService(subtitle_repository=MagicMock(), show_service=show_service)
    svc._find_first_video_file = MagicMock(side_effect=AssertionError("stem fallback"))

    path = asyncio.run(
        svc._resolve_episode_video_for_subtitles(
            episode_id,
            show=MagicMock(),
            season=MagicMock(number=1),
            episode=MagicMock(number=1),
            season_dir=tmp_path,
        )
    )
    assert path == video
    show_service.resolve_episode_file_path.assert_awaited_once_with(imported)


def test_resolve_episode_video_missing_logs_debug_not_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    episode_id = EpisodeId(uuid.uuid4())
    show_service = MagicMock()
    show_service.show_repository.get_episode_files_by_episode_id = AsyncMock(
        return_value=[]
    )
    svc = SubtitleService(subtitle_repository=MagicMock(), show_service=show_service)
    svc._find_first_video_file = MagicMock(return_value=None)

    with caplog.at_level(logging.DEBUG):
        path = asyncio.run(
            svc._resolve_episode_video_for_subtitles(
                episode_id,
                show=MagicMock(name="Show", year=2020),
                season=MagicMock(number=1),
                episode=MagicMock(number=1),
                season_dir=tmp_path,
            )
        )

    assert path is None
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert any("No video file found" in r.getMessage() for r in caplog.records)
