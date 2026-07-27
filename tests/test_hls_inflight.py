"""Tests for HLS in-flight dedupe: entry lifetime tracks the encode task, not waiters."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import miramedia.streams.transcode as transcode
from miramedia.streams.transcode import cache_key_for, ensure_hls_playlist, segment_dir


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


def _write_ready_playlist(source: Path) -> None:
    out_dir = segment_dir(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (out_dir / "seg_000.ts").write_bytes(b"segment")


@pytest.mark.usefixtures("hls_cache")
def test_waiter_cancel_keeps_inflight_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    key = cache_key_for(source)
    encode_started = asyncio.Event()
    encode_gate = asyncio.Event()
    encode_count = 0

    async def _stub_encode(src: Path) -> None:
        nonlocal encode_count
        encode_count += 1
        encode_started.set()
        await encode_gate.wait()
        _write_ready_playlist(src)

    monkeypatch.setattr(transcode, "_encode_hls", _stub_encode)

    async def _run_scenario() -> None:
        waiter = asyncio.create_task(ensure_hls_playlist(source))
        await encode_started.wait()
        shared_task = transcode._inflight[key]
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert key in transcode._inflight
        assert transcode._inflight[key] is shared_task

        second = asyncio.create_task(ensure_hls_playlist(source))
        assert transcode._inflight[key] is shared_task
        encode_gate.set()
        await second
        await shared_task
        assert encode_count == 1

    _run(_run_scenario())


@pytest.mark.usefixtures("hls_cache")
def test_inflight_entry_removed_on_encode_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    key = cache_key_for(source)
    encode_gate = asyncio.Event()

    async def _stub_encode(src: Path) -> None:
        await encode_gate.wait()
        _write_ready_playlist(src)

    monkeypatch.setattr(transcode, "_encode_hls", _stub_encode)

    async def _run_scenario() -> None:
        task = asyncio.create_task(ensure_hls_playlist(source))
        await asyncio.sleep(0)
        assert key in transcode._inflight
        encode_gate.set()
        await task
        assert key not in transcode._inflight

    _run(_run_scenario())


@pytest.mark.usefixtures("hls_cache")
def test_inflight_entry_removed_on_encode_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _make_source(tmp_path)
    key = cache_key_for(source)

    async def _failing_encode(_src: Path) -> None:
        msg = "encode failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(transcode, "_encode_hls", _failing_encode)

    async def _run_scenario() -> None:
        with pytest.raises(RuntimeError, match="encode failed"):
            await ensure_hls_playlist(source)
        assert key not in transcode._inflight

    _run(_run_scenario())
