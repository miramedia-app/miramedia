"""Adapter-level bounds for vendored subtitle plugins (no live network)."""

from __future__ import annotations

import io
import urllib.request
import zipfile
from unittest.mock import MagicMock

import pytest

from miramedia.subtitles.bounded_decode import (
    MAX_SUBTITLE_MEMBER_BYTES,
    MAX_SUBTITLE_RESPONSE_BYTES,
    ResponseReadLimitError,
    decode_bounded_zip_with_selector,
    read_bounded_stream,
)
from miramedia.subtitles.plugins.adapter import (
    _apply_plugin_http_bounds,
    install_bounded_vendored_extract_patches,
)
from miramedia.subtitles.plugins.vendored import subf2m

_RAW_SRT = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n\r\n"


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buffer.getvalue()


class _FakeHttpPlugin:
    def _http_get(
        self, url: str, timeout: float = 10, referer: str | None = None
    ) -> bytes:
        del url, timeout, referer
        with urllib.request.urlopen("http://example.com/subtitle.srt") as response:
            return response.read()


class TestVendoredHttpBounds:
    def test_bounded_http_get_rejects_oversized_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        impl = _FakeHttpPlugin()
        _apply_plugin_http_bounds(impl)
        response = MagicMock()
        response.read = MagicMock(
            side_effect=lambda _amt=-1: b"x" * (MAX_SUBTITLE_RESPONSE_BYTES + 1)
        )
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(urllib.request, "urlopen", MagicMock(return_value=response))

        with pytest.raises(ResponseReadLimitError):
            impl._http_get("http://example.com/subtitle.zip")

    def test_bounded_http_get_accepts_small_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        impl = _FakeHttpPlugin()
        _apply_plugin_http_bounds(impl)
        payload = _RAW_SRT
        response = MagicMock()
        response.read = MagicMock(side_effect=[payload, b""])
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(urllib.request, "urlopen", MagicMock(return_value=response))

        assert impl._http_get("http://example.com/subtitle.srt") == payload


class TestVendoredZipExtractPatches:
    def test_subf2m_extract_rejects_oversized_response(self) -> None:
        install_bounded_vendored_extract_patches()
        oversized = b"x" * (MAX_SUBTITLE_RESPONSE_BYTES + 1)
        result = subf2m.extract_download(oversized, {})

        assert result.get("empty") is True

    def test_subf2m_extract_decodes_zip(self) -> None:
        install_bounded_vendored_extract_patches()
        archive = _zip_bytes({"movie.eng.srt": _RAW_SRT})
        result = subf2m.extract_download(archive, {})

        assert result.get("empty") is False
        assert result.get("format") == "srt"

    def test_subf2m_extract_rejects_oversized_member_at_runtime(self) -> None:
        install_bounded_vendored_extract_patches()
        info = zipfile.ZipInfo("movie.srt")
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = 1
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(info, b"B" * (MAX_SUBTITLE_MEMBER_BYTES + 1))
        result = subf2m.extract_download(buffer.getvalue(), {})

        assert result.get("empty") is True

    def test_selector_preserves_season_episode_pick(self) -> None:
        archive = _zip_bytes(
            {
                "other.srt": b"wrong",
                "show.s01e02.eng.srt": _RAW_SRT,
            }
        )
        decoded = decode_bounded_zip_with_selector(
            archive,
            subf2m.select_subtitle_file,
            {"season": 1, "episode": 2},
        )

        assert decoded.content == _RAW_SRT
        assert decoded.member_name == "show.s01e02.eng.srt"


class TestReadBoundedStream:
    def test_read_bounded_stream_rejects_overflow(self) -> None:
        stream = MagicMock()
        stream.read = MagicMock(side_effect=lambda _amt: b"x" * 1024)

        with pytest.raises(ResponseReadLimitError):
            read_bounded_stream(stream, max_bytes=512)

    def test_read_bounded_stream_accepts_payload(self) -> None:
        stream = MagicMock()
        stream.read = MagicMock(side_effect=[_RAW_SRT, b""])

        assert (
            read_bounded_stream(stream, max_bytes=MAX_SUBTITLE_MEMBER_BYTES) == _RAW_SRT
        )
