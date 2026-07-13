import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from miramedia.streams.router import (
    _MAX_SRT_BYTES,
    _convert_srt_to_vtt,
    _validate_media_path,
)


def test_path_inside_allowed_root_ok(tmp_path: Path) -> None:
    (tmp_path / "dir").mkdir()
    file_path = tmp_path / "dir" / "file.mkv"
    file_path.touch()
    _validate_media_path(file_path, [tmp_path])


def test_dotdot_traversal_rejected(tmp_path: Path) -> None:
    (tmp_path / "subdir").mkdir()
    with pytest.raises(HTTPException) as exc_info:
        _validate_media_path(
            tmp_path / "subdir" / ".." / ".." / "outside.txt", [tmp_path]
        )
    assert exc_info.value.status_code == 404


def test_absolute_path_outside_roots_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-target"
    outside.touch()
    with pytest.raises(HTTPException) as exc_info:
        _validate_media_path(outside, [tmp_path])
    assert exc_info.value.status_code == 404


def test_symlink_escape_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-target"
    outside.touch()
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(HTTPException) as exc_info:
        _validate_media_path(tmp_path / "link", [tmp_path])
    assert exc_info.value.status_code == 404


def test_empty_allowed_roots_rejected(tmp_path: Path) -> None:
    (tmp_path / "file.mkv").touch()
    with pytest.raises(HTTPException) as exc_info:
        _validate_media_path(tmp_path / "file.mkv", [])
    assert exc_info.value.status_code == 404


def test_symlink_allowed_root_resolves_to_real_location(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    (tmp_path / "root_link").symlink_to(real_root)
    file_path = real_root / "sub" / "file.srt"
    file_path.parent.mkdir(parents=True)
    file_path.touch()
    _validate_media_path(file_path, [tmp_path / "root_link"])


def test_oversized_srt_rejected(tmp_path: Path) -> None:
    srt_path = tmp_path / "big.srt"
    srt_path.touch()
    os.truncate(srt_path, _MAX_SRT_BYTES + 1)
    with pytest.raises(HTTPException) as exc_info:
        _convert_srt_to_vtt(srt_path)
    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == "Subtitle file too large"


def test_small_srt_converts_to_vtt(tmp_path: Path) -> None:
    srt_path = tmp_path / "small.srt"
    srt_path.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    vtt = _convert_srt_to_vtt(srt_path)
    assert vtt.startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:02.000" in vtt
