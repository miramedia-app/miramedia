"""Bounded subtitle download decoding tests."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from babelfish import Language

from miramedia.subtitles.bounded_decode import (
    MAX_SUBTITLE_MEMBER_BYTES,
    MAX_SUBTITLE_RESPONSE_BYTES,
    MAX_SUBTITLE_ZIP_ENTRIES,
    BoundedSubtitleContent,
    _zip_member_unsafe,
    decode_bounded_subtitle_content,
)
from miramedia.subtitles.providers.subdl import SubDLProvider, SubDLSubtitle
from miramedia.subtitles.providers.subsource import SubsourceProvider, SubsourceSubtitle
from miramedia.subtitles.providers.yifysubtitles import (
    YifySubtitle,
    YifySubtitlesProvider,
)

_RAW_SRT = b"1\r\n00:00:01,000 --> 00:00:02,000\r\nHello\r\n\r\n"


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buffer.getvalue()


def _zip_with_info(info: zipfile.ZipInfo, payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(info, payload)
    return buffer.getvalue()


class TestDecodeBoundedSubtitleContent:
    def test_raw_text_within_limit(self) -> None:
        result = decode_bounded_subtitle_content(_RAW_SRT)

        assert result == BoundedSubtitleContent(content=_RAW_SRT, kind="raw")

    def test_normal_zip_selects_first_subtitle_member(self) -> None:
        archive = _zip_bytes(
            {
                "readme.txt": b"ignore",
                "movie.eng.srt": _RAW_SRT,
            }
        )

        result = decode_bounded_subtitle_content(archive)

        assert result.content == _RAW_SRT
        assert result.kind == "zip"

    def test_oversized_response_rejected(self) -> None:
        result = decode_bounded_subtitle_content(
            b"x" * (MAX_SUBTITLE_RESPONSE_BYTES + 1)
        )

        assert result == BoundedSubtitleContent()

    def test_excessive_zip_entries_rejected(self) -> None:
        entries = {f"file{i}.txt": b"x" for i in range(MAX_SUBTITLE_ZIP_ENTRIES + 1)}
        entries["movie.srt"] = _RAW_SRT

        result = decode_bounded_subtitle_content(_zip_bytes(entries))

        assert result == BoundedSubtitleContent(kind="zip", zip_failure="unsafe")

    def test_oversized_member_metadata_rejected(self) -> None:
        archive = _zip_bytes({"movie.srt": _RAW_SRT})

        with patch(
            "miramedia.subtitles.bounded_decode.MAX_SUBTITLE_MEMBER_BYTES",
            len(_RAW_SRT) - 1,
        ):
            result = decode_bounded_subtitle_content(archive)

        assert result == BoundedSubtitleContent(kind="zip", zip_failure="unsafe")

    def test_excessive_compression_ratio_rejected(self) -> None:
        info = zipfile.ZipInfo("movie.srt")
        info.file_size = 200
        info.compress_size = 1

        assert _zip_member_unsafe(info) is True

    def test_corrupt_zip_rejected(self) -> None:
        result = decode_bounded_subtitle_content(b"PK\x03\x04not-a-real-zip")

        assert result == BoundedSubtitleContent(kind="zip", zip_failure="bad")

    def test_zip_without_subtitle_member_rejected(self) -> None:
        archive = _zip_bytes({"readme.txt": b"notes"})

        result = decode_bounded_subtitle_content(archive)

        assert result == BoundedSubtitleContent(kind="zip", zip_failure="no_member")

    def test_metadata_lie_capped_at_runtime(self) -> None:
        info = zipfile.ZipInfo("movie.srt")
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = 1
        archive = _zip_with_info(info, b"B" * (MAX_SUBTITLE_MEMBER_BYTES + 1))

        with patch(
            "miramedia.subtitles.bounded_decode.MAX_SUBTITLE_MEMBER_BYTES",
            32,
        ):
            result = decode_bounded_subtitle_content(archive)

        assert result == BoundedSubtitleContent(kind="zip", zip_failure="unsafe")

    def test_encrypted_zip_member_rejected(self) -> None:
        info = zipfile.ZipInfo("movie.srt")
        info.flag_bits = 0x1

        assert _zip_member_unsafe(info) is True

    def test_directory_subtitle_name_skipped(self) -> None:
        archive = _zip_bytes({"subs.srt/": b"", "movie.srt": _RAW_SRT})

        result = decode_bounded_subtitle_content(archive)

        assert result.content == _RAW_SRT
        assert result.kind == "zip"


def _bytes_response(content: bytes, *, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.raise_for_status = MagicMock()
    return response


class TestProviderDownloadIntegration:
    def test_subdl_download_decodes_zip(self) -> None:
        provider = SubDLProvider(api_key="test-key")
        provider.initialize()
        subtitle = SubDLSubtitle(
            Language("eng"),
            "movie.srt",
            page_link="https://subdl.com/subtitle/1",
            download_link="/subtitle/abc.zip",
            release_names=["Test.Release"],
        )
        provider.session.get = MagicMock(
            return_value=_bytes_response(_zip_bytes({"movie.srt": _RAW_SRT}))
        )

        provider.download_subtitle(subtitle)

        assert subtitle.content is not None
        provider.terminate()

    def test_subdl_download_rejects_oversized_response(self) -> None:
        provider = SubDLProvider(api_key="test-key")
        provider.initialize()
        subtitle = SubDLSubtitle(
            Language("eng"),
            "movie.srt",
            page_link="https://subdl.com/subtitle/1",
            download_link="/subtitle/abc.zip",
            release_names=["Test.Release"],
        )
        provider.session.get = MagicMock(
            return_value=_bytes_response(b"x" * (MAX_SUBTITLE_RESPONSE_BYTES + 1))
        )

        provider.download_subtitle(subtitle)

        assert subtitle.content is None
        provider.terminate()

    def test_subsource_download_decodes_raw_text(self) -> None:
        provider = SubsourceProvider(api_key="test-key")
        provider.initialize()
        subtitle = SubsourceSubtitle(
            Language("eng"),
            subtitle_id=42,
            release_info=["Test.Release"],
            page_link="https://subsource.net/subtitle/42",
        )
        provider.session.get = MagicMock(return_value=_bytes_response(_RAW_SRT))

        provider.download_subtitle(subtitle)

        assert subtitle.content is not None
        provider.terminate()

    def test_subsource_download_logs_corrupt_zip(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider = SubsourceProvider(api_key="test-key")
        provider.initialize()
        subtitle = SubsourceSubtitle(
            Language("eng"),
            subtitle_id=42,
            release_info=["Test.Release"],
            page_link="https://subsource.net/subtitle/42",
        )
        provider.session.get = MagicMock(
            return_value=_bytes_response(b"PK\x03\x04not-a-real-zip")
        )

        with caplog.at_level("WARNING"):
            provider.download_subtitle(subtitle)

        assert subtitle.content is None
        assert "corrupted zip" in caplog.text
        provider.terminate()

    def test_yify_download_decodes_raw_text(self) -> None:
        provider = YifySubtitlesProvider()
        provider.initialize()
        subtitle = YifySubtitle(
            Language("eng"),
            "https://yifysubtitles.ch/subtitles/test",
            page_link="https://yifysubtitles.ch/subtitles/test",
            release="Test.Release",
            rating=8,
        )
        page_html = b'<html><body><a class="download-subtitle" href="/download">dl</a></body></html>'
        provider.session.get = MagicMock(
            side_effect=[
                _bytes_response(page_html),
                _bytes_response(_RAW_SRT),
            ]
        )

        provider.download_subtitle(subtitle)

        assert subtitle.content is not None
        provider.terminate()
