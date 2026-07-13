"""Tests for poster download size limits and decode guards."""

import io
import os
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import pytest
from PIL import Image

import miramedia.metadata.utils as utils
from miramedia.metadata.utils import (
    _MAX_POSTER_DOWNLOAD_BYTES,
    download_poster_image,
)

POSTER_UUID = UUID("11111111-1111-4111-8111-111111111111")


def _make_small_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="JPEG")
    return buf.getvalue()


class FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []
        self.closed = False

    def iter_content(self, chunk_size: int = 0):
        del chunk_size
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


def test_download_poster_happy_path_no_content_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jpeg_bytes = _make_small_jpeg_bytes()

    def fake_get(*_args: object, **_kwargs: object) -> FakeResponse:
        return FakeResponse(chunks=[jpeg_bytes])

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        download_poster_image(tmp_path, "http://example.com/poster.jpg", POSTER_UUID)
        is True
    )

    base = tmp_path.joinpath(str(POSTER_UUID))
    assert base.with_suffix(".jpg").exists()
    assert base.with_suffix(".avif").exists()
    assert base.with_suffix(".webp").exists()


def test_download_poster_oversized_stream_no_content_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_bytes = 1 << 20
    max_chunks = 60
    consumed = {"count": 0}

    class OversizedResponse:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {}
        closed = False

        def iter_content(self, chunk_size: int = 0):
            del chunk_size
            for _ in range(max_chunks):
                consumed["count"] += 1
                yield b"x" * chunk_bytes

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        utils.requests,
        "get",
        lambda *_args, **_kwargs: OversizedResponse(),
    )

    assert (
        download_poster_image(tmp_path, "http://example.com/huge.jpg", POSTER_UUID)
        is False
    )

    assert not tmp_path.joinpath(str(POSTER_UUID)).with_suffix(".jpg").exists()
    assert consumed["count"] <= 51


def test_download_poster_content_length_over_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    over_cap = str(_MAX_POSTER_DOWNLOAD_BYTES + 1)

    def fake_get(*_args: object, **_kwargs: object) -> FakeResponse:
        return FakeResponse(headers={"Content-Length": over_cap})

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        download_poster_image(tmp_path, "http://example.com/poster.jpg", POSTER_UUID)
        is False
    )

    assert not any(tmp_path.iterdir())


def test_download_poster_corrupt_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corrupt = os.urandom(1024)

    def fake_get(*_args: object, **_kwargs: object) -> FakeResponse:
        return FakeResponse(chunks=[corrupt])

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        download_poster_image(tmp_path, "http://example.com/bad.jpg", POSTER_UUID)
        is False
    )

    assert not tmp_path.joinpath(str(POSTER_UUID)).with_suffix(".jpg").exists()
