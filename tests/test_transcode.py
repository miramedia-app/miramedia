"""Unit tests for HLS transcode command construction, cache paths, and playlist wait."""

from __future__ import annotations

import asyncio
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

import miramedia.streams.transcode as transcode
from miramedia.streams.transcode import (
    HlsTranscodeError,
    _encode_hls,
    _ffmpeg_cmd,
    _wait_for_playlist_start,
    cache_key_for,
    ensure_hls_playlist,
    hls_cache_root,
    hls_playlist_ready,
    playlist_path,
    segment_dir,
)


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_transcode_state() -> Iterator[None]:
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


def _write_ready_playlist(source: Path) -> Path:
    out_dir = segment_dir(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    playlist = out_dir / "index.m3u8"
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    (out_dir / "seg_000.ts").write_bytes(b"segment")
    return playlist


class FakePopen:
    recorded_cmds: ClassVar[list[list[str]]] = []

    def __init__(self, cmd: list[str], **_kwargs: Any) -> None:
        self.args = cmd
        self._returncode: int | None = None
        self.stderr = io.BytesIO(b"")
        FakePopen.recorded_cmds.append(cmd)

    def poll(self) -> int | None:
        return self._returncode

    def wait(self) -> int:
        return self._returncode if self._returncode is not None else 0

    def terminate(self) -> None:
        if self._returncode is None:
            self._returncode = -15

    def kill(self) -> None:
        self._returncode = -9


def _install_fake_popen(monkeypatch: pytest.MonkeyPatch) -> None:
    FakePopen.recorded_cmds.clear()
    monkeypatch.setattr(transcode.subprocess, "Popen", FakePopen)


def _fast_wait_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcode, "_HLS_START_TIMEOUT_S", 0.2)
    monkeypatch.setattr(transcode, "_HLS_POLL_INTERVAL_S", 0.05)


def test_ffmpeg_cmd_is_trusted_argv_list(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    out_dir = tmp_path / "out"
    playlist = out_dir / "index.m3u8"
    ffmpeg = "/usr/bin/ffmpeg"

    cmd = _ffmpeg_cmd(ffmpeg, source, out_dir, playlist)

    assert isinstance(cmd, list)
    assert cmd.count(str(source)) == 1
    assert cmd[-1] == str(playlist)
    assert "-f" in cmd
    assert "hls" in cmd
    assert "-hls_time" in cmd
    assert str(transcode._HLS_SEGMENT_SECONDS) in cmd
    assert "-hls_playlist_type" in cmd
    assert "vod" in cmd
    assert "-hls_flags" in cmd
    assert "independent_segments" in cmd
    assert "-hls_segment_filename" in cmd
    seg_idx = cmd.index("-hls_segment_filename") + 1
    assert cmd[seg_idx] == str(out_dir / "seg_%03d.ts")


def test_cache_key_is_deterministic_for_same_file(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    assert cache_key_for(source) == cache_key_for(source)


def test_cache_key_changes_when_file_changes(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    first = cache_key_for(source)
    source.write_bytes(b"longer-fake-video")
    second = cache_key_for(source)
    assert first != second


def test_playlist_and_segment_paths_stay_under_cache(
    tmp_path: Path, hls_cache: Path
) -> None:
    source = _make_source(tmp_path, "weird .. name.mkv")
    key = cache_key_for(source)
    playlist = playlist_path(source)
    seg_dir = segment_dir(source)

    assert playlist == hls_cache / key / "index.m3u8"
    assert seg_dir == hls_cache / key
    assert ".." not in playlist.parts
    assert playlist.is_relative_to(hls_cache.resolve())
    assert seg_dir.is_relative_to(hls_cache.resolve())


@pytest.mark.usefixtures("hls_cache")
def test_hls_playlist_ready_requires_playlist_and_segment(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    out_dir = segment_dir(source)
    out_dir.mkdir(parents=True)
    (out_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    assert hls_playlist_ready(source) is False

    (out_dir / "seg_000.ts").write_bytes(b"segment")
    assert hls_playlist_ready(source) is True


def _write_playlist_segment(playlist: Path, out_dir: Path) -> None:
    playlist.parent.mkdir(parents=True, exist_ok=True)
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    (out_dir / "seg_000.ts").write_bytes(b"segment")


async def _create_segment_after_delay(
    playlist: Path, out_dir: Path, delay: float
) -> None:
    await asyncio.sleep(delay)
    await asyncio.to_thread(_write_playlist_segment, playlist, out_dir)


def test_wait_for_playlist_start_succeeds_when_segment_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fast_wait_constants(monkeypatch)
    out_dir = tmp_path / "out"
    playlist = out_dir / "index.m3u8"
    proc = FakePopen([])

    async def _run_wait() -> None:
        task = asyncio.create_task(_create_segment_after_delay(playlist, out_dir, 0.1))
        await _wait_for_playlist_start(proc, playlist=playlist, out_dir=out_dir)
        await task

    _run(_run_wait())


def test_wait_for_playlist_start_times_out_when_segment_never_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fast_wait_constants(monkeypatch)
    out_dir = tmp_path / "out"
    playlist = out_dir / "index.m3u8"
    proc = FakePopen([])

    with pytest.raises(HlsTranscodeError, match="timed out waiting for first segment"):
        _run(_wait_for_playlist_start(proc, playlist=playlist, out_dir=out_dir))

    assert proc.poll() is not None


def test_wait_for_playlist_start_fails_fast_when_process_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fast_wait_constants(monkeypatch)
    out_dir = tmp_path / "out"
    playlist = out_dir / "index.m3u8"
    proc = FakePopen([])
    proc._returncode = 1
    proc.stderr = io.BytesIO(b"encode failed")

    with pytest.raises(HlsTranscodeError, match="timed out waiting for first segment"):
        _run(_wait_for_playlist_start(proc, playlist=playlist, out_dir=out_dir))


@pytest.mark.usefixtures("hls_cache")
def test_encode_hls_happy_path_records_ffmpeg_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    _install_fake_popen(monkeypatch)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    async def _run_encode() -> None:
        out_dir = segment_dir(source)
        playlist = out_dir / "index.m3u8"
        task = asyncio.create_task(_create_segment_after_delay(playlist, out_dir, 0.1))
        await _encode_hls(source)
        await task

    _run(_run_encode())

    assert len(FakePopen.recorded_cmds) == 1
    expected = _ffmpeg_cmd(
        "/usr/bin/ffmpeg",
        source,
        segment_dir(source),
        segment_dir(source) / "index.m3u8",
    )
    assert FakePopen.recorded_cmds[0] == expected
    assert hls_playlist_ready(source)


@pytest.mark.usefixtures("hls_cache")
def test_ensure_hls_playlist_cache_hit_skips_popen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    playlist = _write_ready_playlist(source)
    _install_fake_popen(monkeypatch)

    result = _run(ensure_hls_playlist(source))

    assert result == playlist
    assert FakePopen.recorded_cmds == []


@pytest.mark.usefixtures("hls_cache")
def test_encode_hls_surfaces_failure_when_process_exits_early(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    _fast_wait_constants(monkeypatch)
    _install_fake_popen(monkeypatch)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    original_init = FakePopen.__init__

    def _failing_init(self: FakePopen, cmd: list[str], **kwargs: Any) -> None:
        original_init(self, cmd, **kwargs)
        self._returncode = 1
        self.stderr = io.BytesIO(b"ffmpeg: invalid data found\n")

    monkeypatch.setattr(FakePopen, "__init__", _failing_init)

    with pytest.raises(HlsTranscodeError, match="timed out waiting for first segment"):
        _run(_encode_hls(source))


@pytest.mark.usefixtures("hls_cache")
def test_ensure_hls_playlist_returns_playlist_after_encode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    _install_fake_popen(monkeypatch)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    async def _run_ensure() -> Path:
        out_dir = segment_dir(source)
        playlist = out_dir / "index.m3u8"
        task = asyncio.create_task(_create_segment_after_delay(playlist, out_dir, 0.1))
        result = await ensure_hls_playlist(source)
        await task
        return result

    playlist = _run(_run_ensure())
    assert playlist == segment_dir(source) / "index.m3u8"
    assert hls_playlist_ready(source)


def test_hls_cache_root_uses_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom-hls"
    monkeypatch.setenv("MIRAMEDIA_HLS_CACHE", str(custom))
    assert hls_cache_root() == custom
    assert custom.is_dir()
