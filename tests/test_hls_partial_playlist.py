"""Tests for temp-dir encode, atomic publish, and honest partial playlists."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import miramedia.streams.transcode as transcode
from miramedia.streams.router import (
    _hls_playlist_cache_control,
    _resolve_hls_segment,
    movie_hls_playlist,
    movie_hls_segment,
)
from miramedia.streams.transcode import (
    _ffmpeg_cmd,
    _publish_tmp_dir,
    cache_key_for,
    ensure_hls_playlist,
    hls_cache_root,
    hls_playlist_ready,
    segment_dir,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_inflight() -> Iterator[None]:
    transcode._inflight.clear()
    yield
    transcode._inflight.clear()


@pytest.fixture
def hls_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "hls-cache"
    monkeypatch.setenv("MIRAMEDIA_HLS_CACHE", str(cache))
    return cache


def _make_source(tmp_path: Path, name: str = "movie.mkv") -> Path:
    source = tmp_path / name
    source.write_bytes(b"fake-video")
    return source


def _encode_tmp_dir(source: Path, suffix: str = "test") -> Path:
    key = cache_key_for(source)
    return hls_cache_root() / f".tmp-{key}-{suffix}"


def _write_started(tmp_dir: Path) -> None:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    (tmp_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (tmp_dir / "seg_000.ts").write_bytes(b"segment")


@pytest.mark.usefixtures("hls_cache")
def test_successful_encode_publishes_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)

    async def _stub_encode(src: Path, _key: str, encode_tmp: Path) -> None:
        _write_started(encode_tmp)
        (encode_tmp / ".complete").touch()
        _publish_tmp_dir(encode_tmp, segment_dir(src))

    monkeypatch.setattr(transcode, "_encode_hls", _stub_encode)

    async def _run_scenario() -> None:
        playlist = await ensure_hls_playlist(source)
        out_dir = segment_dir(source)
        assert playlist == out_dir / "index.m3u8"
        assert hls_playlist_ready(source)
        assert (out_dir / ".complete").is_file()
        assert not list(hls_cache_root().glob(".tmp-*"))

    _run(_run_scenario())


@pytest.mark.usefixtures("hls_cache")
def test_failed_encode_removes_temp_dir_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    from miramedia.streams.transcode import _HLS_START_TIMEOUT_S

    transcode._HLS_START_TIMEOUT_S = 0.2
    transcode._HLS_POLL_INTERVAL_S = 0.05
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    class FailingPopen:
        def __init__(self, _cmd: list[str], **_kwargs: Any) -> None:
            self._returncode = 1
            self.stderr = __import__("io").BytesIO(b"ffmpeg failed")

        def poll(self) -> int:
            return self._returncode

        def wait(self) -> int:
            return self._returncode

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

    monkeypatch.setattr(transcode.subprocess, "Popen", FailingPopen)

    async def _run_scenario() -> None:
        with pytest.raises(transcode.HlsTranscodeError):
            await ensure_hls_playlist(source)
        assert not segment_dir(source).exists()
        assert not list(hls_cache_root().glob(".tmp-*"))

    _run(_run_scenario())
    transcode._HLS_START_TIMEOUT_S = _HLS_START_TIMEOUT_S


@pytest.mark.usefixtures("hls_cache")
def test_concurrent_request_shares_temp_playlist_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    encode_started = asyncio.Event()
    encode_gate = asyncio.Event()
    encode_count = 0

    async def _stub_encode(_src: Path, _key: str, encode_tmp: Path) -> None:
        nonlocal encode_count
        encode_count += 1
        encode_started.set()
        await encode_gate.wait()
        _write_started(encode_tmp)

    monkeypatch.setattr(transcode, "_encode_hls", _stub_encode)

    async def _run_scenario() -> None:
        first = asyncio.create_task(ensure_hls_playlist(source))
        await encode_started.wait()
        entry = transcode._inflight[cache_key_for(source)]
        second = asyncio.create_task(ensure_hls_playlist(source))
        encode_gate.set()
        first_path = await first
        second_path = await second
        assert first_path == second_path == entry.tmp_dir / "index.m3u8"
        assert encode_count == 1

    _run(_run_scenario())


@pytest.mark.usefixtures("hls_cache")
def test_post_failure_rerequest_keeps_published_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    out_dir = segment_dir(source)
    out_dir.mkdir(parents=True)
    (out_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (out_dir / "seg_000.ts").write_bytes(b"published")
    (out_dir / ".complete").touch()
    encode_count = 0

    async def _failing_encode(_src: Path, _key: str, _tmp: Path) -> None:
        nonlocal encode_count
        encode_count += 1
        msg = "encode failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(transcode, "_encode_hls", _failing_encode)

    async def _run_scenario() -> None:
        playlist = await ensure_hls_playlist(source)
        assert playlist == out_dir / "index.m3u8"
        assert encode_count == 0
        assert (out_dir / "seg_000.ts").read_bytes() == b"published"

    _run(_run_scenario())


def test_ffmpeg_cmd_uses_event_playlist_for_progressive_playback(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path)
    out_dir = tmp_path / "out"
    playlist = out_dir / "index.m3u8"
    cmd = _ffmpeg_cmd("/usr/bin/ffmpeg", source, out_dir, playlist)
    assert "event" in cmd
    assert "vod" not in cmd


@pytest.mark.usefixtures("hls_cache")
def test_hls_playlist_cache_control_no_store_while_incomplete(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    tmp_dir = _encode_tmp_dir(source)
    _write_started(tmp_dir)
    assert _hls_playlist_cache_control(tmp_dir / "index.m3u8") == "no-store"


@pytest.mark.usefixtures("hls_cache")
def test_hls_playlist_cache_control_max_age_when_published(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    out_dir = segment_dir(source)
    _write_started(out_dir)
    (out_dir / ".complete").touch()
    assert (
        _hls_playlist_cache_control(out_dir / "index.m3u8") == "private, max-age=3600"
    )


@pytest.mark.usefixtures("hls_cache")
def test_movie_hls_playlist_returns_no_store_while_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    tmp_dir = _encode_tmp_dir(source)
    _write_started(tmp_dir)
    playlist_path = tmp_dir / "index.m3u8"

    async def _ensure_hls(_source: Path) -> Path:
        return playlist_path

    async def _load_movie_file(**_kwargs: object) -> MagicMock:
        return MagicMock()

    async def _resolve_movie_video_file(**_kwargs: object) -> Path:
        return source

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
    monkeypatch.setattr(
        "miramedia.streams.router.release_session_before_external_io",
        AsyncMock(),
    )

    response = _run(
        movie_hls_playlist(
            movie=MagicMock(),
            movie_service=MagicMock(),
            db=MagicMock(),
            file_id=uuid.uuid4(),
        )
    )

    assert response.headers["cache-control"] == "no-store"


@pytest.mark.usefixtures("hls_cache")
def test_movie_hls_playlist_returns_max_age_when_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    out_dir = segment_dir(source)
    _write_started(out_dir)
    (out_dir / ".complete").touch()
    playlist_path = out_dir / "index.m3u8"

    async def _ensure_hls(_source: Path) -> Path:
        return playlist_path

    async def _load_movie_file(**_kwargs: object) -> MagicMock:
        return MagicMock()

    async def _resolve_movie_video_file(**_kwargs: object) -> Path:
        return source

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
    monkeypatch.setattr(
        "miramedia.streams.router.release_session_before_external_io",
        AsyncMock(),
    )

    response = _run(
        movie_hls_playlist(
            movie=MagicMock(),
            movie_service=MagicMock(),
            db=MagicMock(),
            file_id=uuid.uuid4(),
        )
    )

    assert response.headers["cache-control"] == "private, max-age=3600"


@pytest.mark.usefixtures("hls_cache")
def test_resolve_hls_segment_falls_back_after_publish_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    tmp_dir = _encode_tmp_dir(source, "race")
    out_dir = segment_dir(source)
    segment_name = "seg_000.ts"
    _write_started(out_dir)
    published_seg = out_dir / segment_name

    def _current_hls_dir(_source: Path) -> Path:
        return tmp_dir

    monkeypatch.setattr(transcode, "current_hls_dir", _current_hls_dir)

    seg = _resolve_hls_segment(source, segment_name)
    assert seg == published_seg
    assert seg.is_file()


@pytest.mark.usefixtures("hls_cache")
def test_movie_hls_segment_serves_after_publish_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    tmp_dir = _encode_tmp_dir(source, "race")
    out_dir = segment_dir(source)
    segment_name = "seg_000.ts"
    _write_started(out_dir)

    async def _load_movie_file(**_kwargs: object) -> MagicMock:
        return MagicMock()

    async def _resolve_movie_video_file(**_kwargs: object) -> Path:
        return source

    monkeypatch.setattr(transcode, "current_hls_dir", lambda _s: tmp_dir)
    monkeypatch.setattr(
        "miramedia.streams.router._load_movie_file",
        _load_movie_file,
    )
    monkeypatch.setattr(
        "miramedia.streams.router._resolve_movie_video_file",
        _resolve_movie_video_file,
    )
    monkeypatch.setattr(
        "miramedia.streams.router.release_session_before_external_io",
        AsyncMock(),
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

    assert response.status_code == 200
    assert response.media_type == "video/mp2t"
