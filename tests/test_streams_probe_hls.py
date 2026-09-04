"""Regression coverage for HLS URLs advertised by stream probes."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from miramedia.streams.router import _probe_hls_playlist_url, _render_hls_playlist
from miramedia.streams.transcode import _COMPLETE_MARKER, segment_dir


@pytest.fixture
def hls_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "hls-cache"
    monkeypatch.setenv("MIRAMEDIA_HLS_CACHE", str(cache))
    return cache


def _make_source(tmp_path: Path) -> Path:
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"fake-video")
    return source


def _write_started_only(source: Path) -> None:
    out_dir = segment_dir(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (out_dir / "seg_000.ts").write_bytes(b"segment")


def _write_complete_cache(source: Path) -> None:
    _write_started_only(source)
    (segment_dir(source) / _COMPLETE_MARKER).touch()


@pytest.mark.usefixtures("hls_cache")
def test_probe_partial_cache_schedules_warm_without_advertising_live_playlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    _write_started_only(source)
    file_id = uuid.uuid4()
    schedule = MagicMock()
    monkeypatch.setattr(
        "miramedia.streams.router.hls_transcode_available", lambda: True
    )
    monkeypatch.setattr("miramedia.streams.router.schedule_hls_warm", schedule)

    url = _probe_hls_playlist_url(
        direct_play=False,
        media_kind="movies",
        media_id="movie-id",
        file_id=file_id,
        source_file=source,
    )

    assert url is None
    schedule.assert_called_once_with(source)


@pytest.mark.usefixtures("hls_cache")
def test_probe_omits_hls_when_ffmpeg_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    file_id = uuid.uuid4()
    monkeypatch.setattr(
        "miramedia.streams.router.hls_transcode_available", lambda: False
    )

    url = _probe_hls_playlist_url(
        direct_play=False,
        media_kind="movies",
        media_id="movie-id",
        file_id=file_id,
        source_file=source,
    )

    assert url is None


@pytest.mark.usefixtures("hls_cache")
def test_probe_complete_cache_advertises_without_scheduling_warm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    _write_complete_cache(source)
    file_id = uuid.uuid4()
    schedule = MagicMock()
    monkeypatch.setattr(
        "miramedia.streams.router.hls_transcode_available", lambda: True
    )
    monkeypatch.setattr("miramedia.streams.router.schedule_hls_warm", schedule)

    url = _probe_hls_playlist_url(
        direct_play=False,
        media_kind="movies",
        media_id="movie-id",
        file_id=file_id,
        source_file=source,
    )

    assert url == f"/api/v1/streams/movies/movie-id/hls/index.m3u8?file_id={file_id}"
    schedule.assert_not_called()


@pytest.mark.usefixtures("hls_cache")
def test_probe_direct_play_skips_hls_even_when_cached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _make_source(tmp_path)
    _write_complete_cache(source)
    schedule = MagicMock()
    monkeypatch.setattr("miramedia.streams.router.schedule_hls_warm", schedule)

    url = _probe_hls_playlist_url(
        direct_play=True,
        media_kind="movies",
        media_id="movie-id",
        file_id=uuid.uuid4(),
        source_file=source,
    )

    assert url is None
    schedule.assert_not_called()


def test_render_hls_playlist_adds_file_id_to_every_media_uri(tmp_path: Path) -> None:
    playlist = tmp_path / "index.m3u8"
    playlist.write_text(
        '#EXTM3U\n#EXTINF:4.0,\nseg_000.ts\n#EXT-X-MAP:URI="init.mp4"\n'
        "nested/seg_001.ts?token=abc\n",
        encoding="utf-8",
    )
    file_id = uuid.uuid4()

    rendered = _render_hls_playlist(playlist, file_id)

    assert f"seg_000.ts?file_id={file_id}" in rendered
    assert f"nested/seg_001.ts?token=abc&file_id={file_id}" in rendered
    assert f'#EXT-X-MAP:URI="init.mp4?file_id={file_id}"' in rendered
