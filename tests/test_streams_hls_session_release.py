"""Regression: HLS handlers release the DB session before slow I/O."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from miramedia.shows.schemas import EpisodeId
from miramedia.streams.router import (
    episode_hls_playlist,
    episode_hls_segment,
    movie_hls_playlist,
    movie_hls_segment,
)
from miramedia.streams.transcode import segment_dir


def _run(coro):
    return asyncio.run(coro)


def test_episode_hls_playlist_releases_session_before_transcode(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    playlist_path = tmp_path / "index.m3u8"
    playlist_path.write_text("#EXTM3U\n", encoding="utf-8")
    video_file = tmp_path / "video.mkv"
    video_file.touch()

    async def _release(_db: object) -> None:
        calls.append("release")

    async def _ensure_hls(_source: Path) -> Path:
        calls.append("transcode")
        return playlist_path

    async def _load_episode_file(**_kwargs: object) -> MagicMock:
        return MagicMock()

    async def _resolve_episode_video_file(**_kwargs: object) -> Path:
        return video_file

    monkeypatch.setattr(
        "miramedia.streams.router.release_session_before_external_io",
        _release,
    )
    monkeypatch.setattr(
        "miramedia.streams.router.ensure_hls_playlist",
        _ensure_hls,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._load_episode_file",
        _load_episode_file,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._resolve_episode_video_file",
        _resolve_episode_video_file,
    )

    _run(
        episode_hls_playlist(
            episode_id=EpisodeId(uuid.uuid4()),
            show_service=MagicMock(),
            db=MagicMock(),
            file_id=uuid.uuid4(),
        )
    )

    assert calls.index("release") < calls.index("transcode")


def test_episode_hls_segment_releases_session_before_file_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cache = tmp_path / "hls_cache"
    monkeypatch.setenv("MIRAMEDIA_HLS_CACHE", str(cache))
    video_file = tmp_path / "video.mkv"
    video_file.touch()
    segment_name = "seg_000.ts"
    seg_dir = segment_dir(video_file)
    seg_dir.mkdir(parents=True)
    (seg_dir / segment_name).write_bytes(b"\x00" * 10)

    release_mock = AsyncMock()

    async def _load_episode_file(**_kwargs: object) -> MagicMock:
        return MagicMock()

    async def _resolve_episode_video_file(**_kwargs: object) -> Path:
        return video_file

    monkeypatch.setattr(
        "miramedia.streams.router.release_session_before_external_io",
        release_mock,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._load_episode_file",
        _load_episode_file,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._resolve_episode_video_file",
        _resolve_episode_video_file,
    )

    response = _run(
        episode_hls_segment(
            episode_id=EpisodeId(uuid.uuid4()),
            show_service=MagicMock(),
            db=MagicMock(),
            segment_name=segment_name,
            file_id=uuid.uuid4(),
        )
    )

    release_mock.assert_awaited_once()
    assert response.media_type == "video/mp2t"


def test_movie_hls_segment_releases_session_before_file_response(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cache = tmp_path / "hls_cache"
    monkeypatch.setenv("MIRAMEDIA_HLS_CACHE", str(cache))
    video_file = tmp_path / "video.mkv"
    video_file.touch()
    segment_name = "seg_000.ts"
    seg_dir = segment_dir(video_file)
    seg_dir.mkdir(parents=True)
    (seg_dir / segment_name).write_bytes(b"\x00" * 10)

    release_mock = AsyncMock()

    async def _load_movie_file(**_kwargs: object) -> MagicMock:
        return MagicMock()

    async def _resolve_movie_video_file(**_kwargs: object) -> Path:
        return video_file

    monkeypatch.setattr(
        "miramedia.streams.router.release_session_before_external_io",
        release_mock,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._load_movie_file",
        _load_movie_file,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._resolve_movie_video_file",
        _resolve_movie_video_file,
    )

    response = _run(
        movie_hls_segment(
            movie=MagicMock(),
            movie_service=MagicMock(),
            db=MagicMock(),
            segment_name=segment_name,
            file_id=uuid.uuid4(),
        )
    )

    release_mock.assert_awaited_once()
    assert response.media_type == "video/mp2t"


def test_movie_hls_playlist_releases_session_before_transcode(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    playlist_path = tmp_path / "index.m3u8"
    playlist_path.write_text("#EXTM3U\n", encoding="utf-8")
    video_file = tmp_path / "video.mkv"
    video_file.touch()

    async def _release(_db: object) -> None:
        calls.append("release")

    async def _ensure_hls(_source: Path) -> Path:
        calls.append("transcode")
        return playlist_path

    async def _load_movie_file(**_kwargs: object) -> MagicMock:
        return MagicMock()

    async def _resolve_movie_video_file(**_kwargs: object) -> Path:
        return video_file

    monkeypatch.setattr(
        "miramedia.streams.router.release_session_before_external_io",
        _release,
    )
    monkeypatch.setattr(
        "miramedia.streams.router.ensure_hls_playlist",
        _ensure_hls,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._load_movie_file",
        _load_movie_file,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._resolve_movie_video_file",
        _resolve_movie_video_file,
    )

    _run(
        movie_hls_playlist(
            movie=MagicMock(),
            movie_service=MagicMock(),
            db=MagicMock(),
            file_id=uuid.uuid4(),
        )
    )

    assert calls.index("release") < calls.index("transcode")
