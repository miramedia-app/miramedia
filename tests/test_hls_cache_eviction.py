"""Tests for HLS on-disk cache eviction."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import miramedia.streams.transcode as transcode
from miramedia.streams.transcode import (
    _complete_marker,
    _InflightEntry,
    _is_inflight_cache_dir,
    cache_key_for,
    sweep_hls_cache,
)


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


def _touch_tree(dir_path: Path, mtime: float, atime: float) -> None:
    for file_path in dir_path.rglob("*"):
        if file_path.is_file():
            os.utime(file_path, (atime, mtime))


def _write_complete_dir(
    cache_root: Path,
    key: str,
    *,
    content: bytes = b"x",
    mtime: float | None = None,
    atime: float | None = None,
) -> Path:
    out_dir = cache_root / key
    out_dir.mkdir(parents=True, exist_ok=True)
    segment = out_dir / "seg_000.ts"
    segment.write_bytes(content)
    (out_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    _complete_marker(out_dir).touch()
    if mtime is not None:
        _touch_tree(out_dir, mtime, atime or mtime)
    return out_dir


def _write_incomplete_dir(
    cache_root: Path,
    key: str,
    *,
    mtime: float | None = None,
    atime: float | None = None,
    tmp: bool = False,
) -> Path:
    name = f".tmp-{key}-abcd1234" if tmp else key
    out_dir = cache_root / name
    out_dir.mkdir(parents=True, exist_ok=True)
    segment = out_dir / "seg_000.ts"
    segment.write_bytes(b"partial")
    (out_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    if mtime is not None:
        _touch_tree(out_dir, mtime, atime or mtime)
    return out_dir


@pytest.mark.usefixtures("hls_cache")
def test_expired_complete_dir_deleted_fresh_kept(
    hls_cache: Path, tmp_path: Path
) -> None:
    source = _make_source(tmp_path)
    key = cache_key_for(source)
    old_mtime = time.time() - 90 * 86400
    fresh_mtime = time.time() - 3600

    _write_complete_dir(
        hls_cache, key, content=b"old", mtime=old_mtime, atime=old_mtime
    )
    _write_complete_dir(
        hls_cache,
        "00000000000000000000000000000002",
        mtime=fresh_mtime,
        atime=fresh_mtime,
    )

    summary = sweep_hls_cache(max_bytes=10 * 1024 * 1024 * 1024, max_age_s=30 * 86400)

    assert summary["deleted_dirs"] == 1
    assert not (hls_cache / key).exists()
    assert (hls_cache / "00000000000000000000000000000002").exists()


@pytest.mark.usefixtures("hls_cache")
def test_inflight_key_never_deleted_even_when_expired(
    hls_cache: Path, tmp_path: Path
) -> None:
    source = _make_source(tmp_path)
    key = cache_key_for(source)
    old_mtime = time.time() - 90 * 86400
    out_dir = _write_complete_dir(hls_cache, key, mtime=old_mtime, atime=old_mtime)
    tmp_dir = _write_incomplete_dir(hls_cache, key, tmp=True)

    transcode._inflight[key] = _InflightEntry(
        task=MagicMock(),
        tmp_dir=tmp_dir,
    )

    summary = sweep_hls_cache(max_bytes=10 * 1024 * 1024 * 1024, max_age_s=30 * 86400)

    assert summary["deleted_dirs"] == 0
    assert out_dir.exists()
    assert tmp_dir.exists()


@pytest.mark.usefixtures("hls_cache")
def test_recently_touched_dir_kept(hls_cache: Path) -> None:
    now = time.time()
    _write_complete_dir(
        hls_cache,
        "00000000000000000000000000000001",
        mtime=now - 90 * 86400,
        atime=now - 60,
    )

    summary = sweep_hls_cache(max_bytes=10 * 1024 * 1024 * 1024, max_age_s=30 * 86400)

    assert summary["deleted_dirs"] == 0
    assert (hls_cache / "00000000000000000000000000000001").exists()


@pytest.mark.usefixtures("hls_cache")
def test_over_budget_deletes_oldest_first(hls_cache: Path) -> None:
    old_mtime = time.time() - 5 * 86400
    mid_mtime = time.time() - 4 * 86400
    new_mtime = time.time() - 3 * 86400

    _write_complete_dir(
        hls_cache,
        "00000000000000000000000000000001",
        content=b"o" * 500,
        mtime=old_mtime,
        atime=old_mtime,
    )
    _write_complete_dir(
        hls_cache,
        "00000000000000000000000000000002",
        content=b"m" * 500,
        mtime=mid_mtime,
        atime=mid_mtime,
    )
    _write_complete_dir(
        hls_cache,
        "00000000000000000000000000000003",
        content=b"n" * 500,
        mtime=new_mtime,
        atime=new_mtime,
    )

    summary = sweep_hls_cache(max_bytes=800, max_age_s=30 * 86400)

    assert summary["deleted_dirs"] >= 1
    assert not (hls_cache / "00000000000000000000000000000001").exists()
    assert (hls_cache / "00000000000000000000000000000003").exists()


@pytest.mark.usefixtures("hls_cache")
def test_stale_incomplete_deleted_young_incomplete_kept(hls_cache: Path) -> None:
    old_mtime = time.time() - 7200
    young_mtime = time.time() - 300

    _write_incomplete_dir(
        hls_cache,
        "00000000000000000000000000000004",
        mtime=old_mtime,
        atime=old_mtime,
    )
    _write_incomplete_dir(
        hls_cache,
        "00000000000000000000000000000005",
        mtime=young_mtime,
        atime=young_mtime,
    )

    summary = sweep_hls_cache(max_bytes=10 * 1024 * 1024 * 1024, max_age_s=30 * 86400)

    assert summary["deleted_dirs"] == 1
    assert not (hls_cache / "00000000000000000000000000000004").exists()
    assert (hls_cache / "00000000000000000000000000000005").exists()


@pytest.mark.usefixtures("hls_cache")
def test_stale_tmp_dir_deleted(hls_cache: Path) -> None:
    key = "00000000000000000000000000000006"
    old_mtime = time.time() - 7200
    out_dir = _write_incomplete_dir(
        hls_cache,
        key,
        mtime=old_mtime,
        atime=old_mtime,
        tmp=True,
    )

    summary = sweep_hls_cache(max_bytes=10 * 1024 * 1024 * 1024, max_age_s=30 * 86400)

    assert summary["deleted_dirs"] == 1
    assert not out_dir.exists()


def _touch_lastread(dir_path: Path, mtime: float) -> None:
    path = dir_path / ".lastread"
    path.touch()
    os.utime(path, (mtime, mtime))


@pytest.mark.usefixtures("hls_cache")
def test_lastread_keeps_dir_out_of_tier_one(hls_cache: Path) -> None:
    now = time.time()
    old_mtime = now - 90 * 86400
    recent_lastread = now - 120

    out_dir = _write_complete_dir(
        hls_cache,
        "00000000000000000000000000000007",
        mtime=old_mtime,
        atime=old_mtime,
    )
    _touch_lastread(out_dir, recent_lastread)

    summary = sweep_hls_cache(max_bytes=10 * 1024 * 1024 * 1024, max_age_s=30 * 86400)

    assert summary["skipped_recent_dirs"] == 1
    assert summary["deleted_dirs"] == 0
    assert out_dir.exists()


@pytest.mark.usefixtures("hls_cache")
def test_fresh_tmp_dir_survives_without_inflight(hls_cache: Path) -> None:
    assert transcode._inflight == {}
    young_mtime = time.time() - 300
    out_dir = _write_incomplete_dir(
        hls_cache,
        "00000000000000000000000000000008",
        mtime=young_mtime,
        atime=young_mtime,
        tmp=True,
    )

    summary = sweep_hls_cache(max_bytes=10 * 1024 * 1024 * 1024, max_age_s=30 * 86400)

    assert summary["deleted_dirs"] == 0
    assert out_dir.exists()


@pytest.mark.usefixtures("hls_cache")
def test_tier_two_eviction_when_all_recent(hls_cache: Path) -> None:
    now = time.time()
    oldest_recency = now - 3500
    mid_recency = now - 2400
    newest_recency = now - 120

    oldest = _write_complete_dir(
        hls_cache,
        "00000000000000000000000000000009",
        content=b"o" * 500,
        mtime=oldest_recency,
        atime=oldest_recency,
    )
    _write_complete_dir(
        hls_cache,
        "0000000000000000000000000000000a",
        content=b"m" * 500,
        mtime=mid_recency,
        atime=mid_recency,
    )
    newest = _write_complete_dir(
        hls_cache,
        "0000000000000000000000000000000b",
        content=b"n" * 500,
        mtime=newest_recency,
        atime=newest_recency,
    )

    summary = sweep_hls_cache(max_bytes=800, max_age_s=30 * 86400)

    assert summary["skipped_recent_dirs"] == 3
    assert summary["deleted_dirs"] >= 1
    assert not oldest.exists()
    assert newest.exists()
    assert summary["remaining_bytes"] <= 800


@pytest.mark.usefixtures("hls_cache")
def test_tier_two_floor_protects_actively_streaming(hls_cache: Path) -> None:
    now = time.time()
    recent_recency = now - 60

    dir_a = _write_complete_dir(
        hls_cache,
        "0000000000000000000000000000000c",
        content=b"a" * 500,
        mtime=recent_recency,
        atime=recent_recency,
    )
    dir_b = _write_complete_dir(
        hls_cache,
        "0000000000000000000000000000000d",
        content=b"b" * 500,
        mtime=recent_recency,
        atime=recent_recency,
    )

    summary = sweep_hls_cache(max_bytes=100, max_age_s=30 * 86400)

    assert summary["deleted_dirs"] == 0
    assert dir_a.exists()
    assert dir_b.exists()
    assert summary["remaining_bytes"] > 100


def test_is_inflight_cache_dir_uses_snapshot_only(hls_cache: Path) -> None:
    key = "0000000000000000000000000000000e"
    tmp_dir = hls_cache / f".tmp-{key}-abcd1234"
    tmp_dir.mkdir(parents=True)
    published = hls_cache / key
    published.mkdir(parents=True)

    inflight_tmp = {tmp_dir.resolve()}
    inflight_keys = {key}

    assert _is_inflight_cache_dir(tmp_dir, inflight_tmp, inflight_keys)
    assert _is_inflight_cache_dir(published, inflight_tmp, inflight_keys)
    assert not _is_inflight_cache_dir(tmp_dir, set(), set())
    assert not _is_inflight_cache_dir(published, inflight_tmp, set())


@pytest.mark.usefixtures("hls_cache")
def test_summary_includes_skipped_recent_and_remaining_bytes(hls_cache: Path) -> None:
    now = time.time()
    _write_complete_dir(
        hls_cache,
        "0000000000000000000000000000000f",
        content=b"x" * 400,
        mtime=now - 3600,
        atime=now - 3600,
    )
    _write_complete_dir(
        hls_cache,
        "00000000000000000000000000000010",
        content=b"y" * 400,
        mtime=now - 120,
        atime=now - 120,
    )

    summary = sweep_hls_cache(max_bytes=500, max_age_s=30 * 86400)

    assert "skipped_recent_dirs" in summary
    assert summary["skipped_recent_dirs"] == 1
    assert summary["remaining_bytes"] <= 500
