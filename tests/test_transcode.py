"""Unit tests for HLS transcode command construction, cache paths, and playlist wait."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

import miramedia.streams.transcode as transcode
from miramedia.streams.transcode import (
    _COMPLETE_MARKER,
    HlsTranscodeError,
    _encode_hls,
    _ffmpeg_cmd,
    _wait_for_playlist_start,
    cache_key_for,
    ensure_hls_playlist,
    hls_cache_root,
    hls_playlist_ready,
    hls_playlist_started,
    playlist_path,
    schedule_hls_warm,
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


def _write_playlist_segment(playlist: Path, out_dir: Path) -> None:
    playlist.parent.mkdir(parents=True, exist_ok=True)
    playlist.write_text("#EXTM3U\n", encoding="utf-8")
    (out_dir / "seg_000.ts").write_bytes(b"segment")


def _write_started_playlist(source: Path) -> Path:
    out_dir = segment_dir(source)
    playlist = out_dir / "index.m3u8"
    _write_playlist_segment(playlist, out_dir)
    return playlist


def _write_ready_playlist(source: Path) -> Path:
    playlist = _write_started_playlist(source)
    (segment_dir(source) / _COMPLETE_MARKER).touch()
    return playlist


async def _await_inflight_encode(source: Path) -> None:
    entry = transcode._inflight.get(cache_key_for(source))
    if entry is not None:
        await entry.task


def _encode_args(source: Path, suffix: str = "test") -> tuple[str, Path]:
    key = cache_key_for(source)
    tmp_dir = hls_cache_root() / f".tmp-{key}-{suffix}"
    return key, tmp_dir


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
    assert "event" in cmd
    assert "vod" not in cmd
    assert "-hls_flags" in cmd
    assert "independent_segments" in cmd
    assert "-hls_segment_filename" in cmd
    seg_idx = cmd.index("-hls_segment_filename") + 1
    assert cmd[seg_idx] == str(out_dir / "seg_%03d.ts")


def test_cache_key_is_deterministic_for_same_file(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    assert cache_key_for(source) == cache_key_for(source)


def test_cache_key_changes_with_hls_format_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    first = cache_key_for(source)
    monkeypatch.setattr(transcode, "_HLS_CACHE_VERSION", "test-next")
    assert cache_key_for(source) != first


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
def test_hls_playlist_started_requires_playlist_and_segment(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    out_dir = segment_dir(source)
    out_dir.mkdir(parents=True)
    (out_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    assert hls_playlist_started(source) is False
    assert hls_playlist_ready(source) is False

    (out_dir / "seg_000.ts").write_bytes(b"segment")
    assert hls_playlist_started(source) is True
    assert hls_playlist_ready(source) is False


@pytest.mark.usefixtures("hls_cache")
def test_hls_playlist_ready_requires_completion_marker(tmp_path: Path) -> None:
    source = _make_source(tmp_path)
    _write_started_playlist(source)
    assert hls_playlist_started(source) is True
    assert hls_playlist_ready(source) is False

    (segment_dir(source) / _COMPLETE_MARKER).touch()
    assert hls_playlist_ready(source) is True


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
        await _wait_for_playlist_start(proc, out_dir=out_dir)
        await task

    _run(_run_wait())


def test_wait_for_playlist_start_times_out_when_segment_never_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fast_wait_constants(monkeypatch)
    out_dir = tmp_path / "out"
    proc = FakePopen([])

    with pytest.raises(HlsTranscodeError, match="timed out waiting for first segment"):
        _run(_wait_for_playlist_start(proc, out_dir=out_dir))

    assert proc.poll() is not None


def test_wait_for_playlist_start_fails_fast_when_process_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fast_wait_constants(monkeypatch)
    out_dir = tmp_path / "out"
    proc = FakePopen([])
    proc._returncode = 1
    proc.stderr = io.BytesIO(b"encode failed")

    with pytest.raises(HlsTranscodeError, match="timed out waiting for first segment"):
        _run(_wait_for_playlist_start(proc, out_dir=out_dir))


@pytest.mark.usefixtures("hls_cache")
def test_encode_hls_happy_path_records_ffmpeg_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    _install_fake_popen(monkeypatch)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    async def _run_encode() -> None:
        key, tmp_dir = _encode_args(source, "happy")
        playlist = tmp_dir / "index.m3u8"
        task = asyncio.create_task(_create_segment_after_delay(playlist, tmp_dir, 0.1))
        await _encode_hls(source, key, tmp_dir)
        await task

    _run(_run_encode())

    assert len(FakePopen.recorded_cmds) == 1
    _, tmp_dir = _encode_args(source, "happy")
    expected = _ffmpeg_cmd(
        "/usr/bin/ffmpeg",
        source,
        tmp_dir,
        tmp_dir / "index.m3u8",
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

    key, tmp_dir = _encode_args(source, "early-fail")
    with pytest.raises(HlsTranscodeError, match="timed out waiting for first segment"):
        _run(_encode_hls(source, key, tmp_dir))


@pytest.mark.usefixtures("hls_cache")
def test_ensure_hls_playlist_returns_playlist_after_encode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    _install_fake_popen(monkeypatch)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    async def _run_ensure() -> Path:
        inflight_ready = asyncio.Event()

        async def _delayed_segment() -> None:
            await inflight_ready.wait()
            entry = transcode._inflight[cache_key_for(source)]
            tmp_dir = entry.tmp_dir
            playlist = tmp_dir / "index.m3u8"
            await _create_segment_after_delay(playlist, tmp_dir, 0.1)

        delay_task = asyncio.create_task(_delayed_segment())
        ensure_task = asyncio.create_task(ensure_hls_playlist(source))
        for _ in range(1000):
            if cache_key_for(source) in transcode._inflight:
                break
            await asyncio.sleep(0)
        inflight_ready.set()
        result = await ensure_task
        await delay_task
        await _await_inflight_encode(source)
        return result

    playlist = _run(_run_ensure())
    # Waiter returns at first segment — may still point at the temp encode dir.
    assert playlist.name == "index.m3u8"
    assert hls_playlist_ready(source)


@pytest.mark.usefixtures("hls_cache")
def test_cancelled_waiter_does_not_kill_shared_encode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    encode_started = asyncio.Event()
    encode_complete = asyncio.Event()
    encode_count = 0
    encode_ran_to_completion = False

    async def _slow_fake_encode(src: Path, _key: str, tmp_dir: Path) -> None:
        nonlocal encode_count, encode_ran_to_completion
        encode_count += 1
        encode_started.set()
        await asyncio.sleep(0.5)
        playlist = tmp_dir / "index.m3u8"
        _write_playlist_segment(playlist, tmp_dir)
        encode_ran_to_completion = True
        encode_complete.set()
        (tmp_dir / ".complete").touch()
        transcode._publish_tmp_dir(tmp_dir, segment_dir(src))

    monkeypatch.setattr(transcode, "_encode_hls", _slow_fake_encode)

    async def _run_concurrent() -> None:
        first = asyncio.create_task(ensure_hls_playlist(source))
        await encode_started.wait()
        second = asyncio.create_task(ensure_hls_playlist(source))
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        result = await second
        await encode_complete.wait()
        assert result == segment_dir(source) / "index.m3u8"
        assert encode_count == 1
        assert encode_ran_to_completion

    _run(_run_concurrent())


def test_hls_cache_root_uses_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom = tmp_path / "custom-hls"
    monkeypatch.setenv("MIRAMEDIA_HLS_CACHE", str(custom))
    assert hls_cache_root() == custom
    assert custom.is_dir()


@pytest.mark.usefixtures("hls_cache")
def test_schedule_hls_warm_logs_unexpected_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = _make_source(tmp_path, "crash-test.mkv")
    monkeypatch.setattr(transcode, "hls_transcode_available", lambda: True)

    async def _boom(_src: Path) -> Path:
        msg = "boom"
        raise ValueError(msg)

    monkeypatch.setattr(transcode, "ensure_hls_playlist", _boom)

    async def _run_warm() -> None:
        schedule_hls_warm(source)
        task = next(iter(transcode._warm_tasks))
        with pytest.raises(ValueError, match="boom"):
            await task

    with caplog.at_level(logging.ERROR, logger="miramedia.streams.transcode"):
        _run(_run_warm())

    crashed = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR
        and "crashed" in r.message
        and source.name in r.message
    ]
    assert len(crashed) == 1
    assert crashed[0].exc_info is not None
    assert crashed[0].exc_info[0] is ValueError


@pytest.mark.usefixtures("hls_cache")
def test_schedule_hls_warm_logs_hls_transcode_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = _make_source(tmp_path, "failed.mkv")
    monkeypatch.setattr(transcode, "hls_transcode_available", lambda: True)

    async def _fail(_src: Path) -> Path:
        msg = "encode blew up"
        raise HlsTranscodeError(msg)

    monkeypatch.setattr(transcode, "ensure_hls_playlist", _fail)

    async def _run_warm() -> None:
        schedule_hls_warm(source)
        task = next(iter(transcode._warm_tasks))
        with pytest.raises(HlsTranscodeError, match="encode blew up"):
            await task

    with caplog.at_level(logging.ERROR, logger="miramedia.streams.transcode"):
        _run(_run_warm())

    failed = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR
        and "failed" in r.message
        and source.name in r.message
        and "encode blew up" in r.message
    ]
    assert len(failed) == 1
    assert not failed[0].exc_info


@pytest.mark.usefixtures("hls_cache")
def test_successful_encode_writes_completion_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    _install_fake_popen(monkeypatch)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    async def _run_encode() -> None:
        key, tmp_dir = _encode_args(source, "marker")
        playlist = tmp_dir / "index.m3u8"
        task = asyncio.create_task(_create_segment_after_delay(playlist, tmp_dir, 0.1))
        await _encode_hls(source, key, tmp_dir)
        await task

    _run(_run_encode())
    assert (segment_dir(source) / _COMPLETE_MARKER).is_file()


@pytest.mark.usefixtures("hls_cache")
def test_failed_encode_does_not_write_completion_marker(
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

    key, tmp_dir = _encode_args(source, "early-fail")
    with pytest.raises(HlsTranscodeError, match="timed out waiting for first segment"):
        _run(_encode_hls(source, key, tmp_dir))

    assert not (segment_dir(source) / _COMPLETE_MARKER).exists()


@pytest.mark.usefixtures("hls_cache")
def test_stale_partial_cache_is_replaced_on_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    out_dir = segment_dir(source)
    out_dir.mkdir(parents=True)
    (out_dir / "index.m3u8").write_text("stale\n", encoding="utf-8")
    (out_dir / "seg_000.ts").write_bytes(b"stale-segment")

    _install_fake_popen(monkeypatch)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    async def _run_encode() -> None:
        key, tmp_dir = _encode_args(source, "stale")
        playlist = tmp_dir / "index.m3u8"
        task = asyncio.create_task(_create_segment_after_delay(playlist, tmp_dir, 0.1))
        await _encode_hls(source, key, tmp_dir)
        await task

    _run(_run_encode())

    assert (out_dir / "index.m3u8").read_text(encoding="utf-8") == "#EXTM3U\n"
    assert hls_playlist_ready(source)


@pytest.mark.usefixtures("hls_cache")
def test_encode_hls_nonzero_exit_empty_stderr_removes_tmp_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = _make_source(tmp_path)
    _fast_wait_constants(monkeypatch)
    _install_fake_popen(monkeypatch)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(FakePopen, "wait", lambda _self: 1)

    key, tmp_dir = _encode_args(source, "empty-stderr")

    async def _run_encode() -> None:
        playlist = tmp_dir / "index.m3u8"
        task = asyncio.create_task(_create_segment_after_delay(playlist, tmp_dir, 0.1))
        await _encode_hls(source, key, tmp_dir)
        await task

    with caplog.at_level(logging.ERROR, logger="miramedia.streams.transcode"):
        _run(_run_encode())

    assert not tmp_dir.exists()
    failed = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR
        and "exit code" in r.message
        and source.name in r.message
    ]
    assert len(failed) == 1


@pytest.mark.usefixtures("hls_cache")
def test_publish_tmp_dir_oserror_leaves_temp_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    key, tmp_dir = _encode_args(source, "publish-fail")
    out_dir = segment_dir(source)

    def _raise_oserror(_tmp: Path, _out: Path) -> None:
        msg = "publish failed"
        raise OSError(msg)

    monkeypatch.setattr(transcode, "_publish_tmp_dir", _raise_oserror)
    _install_fake_popen(monkeypatch)
    monkeypatch.setattr(transcode.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    async def _run_encode() -> None:
        playlist = tmp_dir / "index.m3u8"
        task = asyncio.create_task(_create_segment_after_delay(playlist, tmp_dir, 0.05))
        await _encode_hls(source, key, tmp_dir)
        await task

    _run(_run_encode())

    assert tmp_dir.is_dir()
    assert not out_dir.exists()


@pytest.mark.usefixtures("hls_cache")
def test_reap_stale_tmp_dirs_removes_empty_tmp_dir(
    tmp_path: Path,
) -> None:
    source = _make_source(tmp_path)
    key = cache_key_for(source)
    stale_tmp = hls_cache_root() / f".tmp-{key}-empty"
    stale_tmp.mkdir(parents=True)

    old_mtime = time.time() - transcode._TMP_REAP_AGE_S - 10
    os.utime(stale_tmp, (old_mtime, old_mtime))

    transcode._reap_stale_tmp_dirs()
    assert not stale_tmp.exists()
