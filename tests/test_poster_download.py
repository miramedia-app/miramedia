"""Tests for poster download size limits, decode guards, and URL safety."""

import io
import os
import socket
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import pytest
from PIL import Image

import miramedia.metadata.utils as utils
from miramedia.metadata.utils import (
    _MAX_POSTER_DOWNLOAD_BYTES,
    _MAX_POSTER_REDIRECTS,
    _is_safe_poster_url,
    download_poster_image,
)

POSTER_UUID = UUID("11111111-1111-4111-8111-111111111111")
_PUBLIC_IPV4 = "93.184.216.34"


def _addrinfo_for_ips(*ips: str):
    def fake_getaddrinfo(_host: str, port: object, *_args: object, **_kwargs: object):
        del port
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
            if ":" not in ip
            else (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0))
            for ip in ips
        ]

    return fake_getaddrinfo


@pytest.fixture(autouse=True)
def _public_example_com_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never hit real DNS in poster tests — example.com resolves to a public IP."""

    def fake_getaddrinfo(host: str, port: object, *_args: object, **_kwargs: object):
        del port
        if host == "example.com":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (_PUBLIC_IPV4, 0),
                )
            ]
        raise socket.gaierror("unexpected host in test")  # noqa: TRY003, EM101

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


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

    @property
    def is_redirect(self) -> bool:
        return self.status_code in (302, 303, 307)

    @property
    def is_permanent_redirect(self) -> bool:
        return self.status_code in (301, 308)

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
        is_redirect = False
        is_permanent_redirect = False

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


@pytest.mark.parametrize(
    ("url", "getaddrinfo_ips", "expected"),
    [
        ("https://example.com/p.jpg", (_PUBLIC_IPV4,), True),
        ("http://example.com/p.jpg", (_PUBLIC_IPV4,), True),
        ("file:///etc/passwd", None, False),
        ("ftp://example.com/p.jpg", None, False),
        ("", None, False),
        ("https://", None, False),
        ("http://127.0.0.1/p.jpg", ("127.0.0.1",), False),
        ("http://internal/p.jpg", ("10.0.0.5",), False),
        ("http://metadata/p.jpg", ("169.254.169.254",), False),
        ("http://loopback/p.jpg", ("::1",), False),
        ("http://ula/p.jpg", ("fd00::1",), False),
        ("http://mixed/p.jpg", (_PUBLIC_IPV4, "10.0.0.5"), False),
    ],
)
def test_is_safe_poster_url(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    getaddrinfo_ips: tuple[str, ...] | None,
    expected: bool,
) -> None:
    if getaddrinfo_ips is not None:
        monkeypatch.setattr(socket, "getaddrinfo", _addrinfo_for_ips(*getaddrinfo_ips))
    assert _is_safe_poster_url(url) is expected


def test_is_safe_poster_url_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_gaierror(*_args: object, **_kwargs: object) -> None:
        raise socket.gaierror("name resolution failed")  # noqa: TRY003, EM101

    monkeypatch.setattr(socket, "getaddrinfo", raise_gaierror)
    assert _is_safe_poster_url("https://example.com/p.jpg") is False


def test_download_poster_follows_safe_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jpeg_bytes = _make_small_jpeg_bytes()
    calls: list[str] = []

    def fake_get(url: str, *_args: object, **_kwargs: object) -> FakeResponse:
        calls.append(url)
        if url == "http://example.com/poster.jpg":
            return FakeResponse(
                status_code=302,
                headers={"Location": "http://example.com/final.jpg"},
            )
        return FakeResponse(chunks=[jpeg_bytes])

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        download_poster_image(tmp_path, "http://example.com/poster.jpg", POSTER_UUID)
        is True
    )
    assert calls == [
        "http://example.com/poster.jpg",
        "http://example.com/final.jpg",
    ]
    assert tmp_path.joinpath(str(POSTER_UUID)).with_suffix(".jpg").exists()


def test_download_poster_blocks_unsafe_redirect_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_getaddrinfo(host: str, port: object, *_args: object, **_kwargs: object):
        del port
        if host == "example.com":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (_PUBLIC_IPV4, 0),
                )
            ]
        if host == "internal":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    ("10.0.0.5", 0),
                )
            ]
        raise socket.gaierror("unexpected host in test")  # noqa: TRY003, EM101

    def fake_get(url: str, *_args: object, **_kwargs: object) -> FakeResponse:
        calls.append(url)
        if url == "http://example.com/poster.jpg":
            return FakeResponse(
                status_code=302,
                headers={"Location": "http://internal/poster.jpg"},
            )
        raise AssertionError(  # noqa: TRY003
            f"unsafe redirect target must not be fetched: {url}"  # noqa: EM102
        )

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        download_poster_image(tmp_path, "http://example.com/poster.jpg", POSTER_UUID)
        is False
    )
    assert calls == ["http://example.com/poster.jpg"]
    assert not any(tmp_path.iterdir())


def test_download_poster_rejects_too_many_redirects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_get(url: str, *_args: object, **_kwargs: object) -> FakeResponse:
        calls.append(url)
        return FakeResponse(
            status_code=302,
            headers={"Location": "http://example.com/next.jpg"},
        )

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        download_poster_image(tmp_path, "http://example.com/start.jpg", POSTER_UUID)
        is False
    )
    assert len(calls) == _MAX_POSTER_REDIRECTS + 1
    assert not any(tmp_path.iterdir())


def test_download_poster_rejects_redirect_without_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_get(*_args: object, **_kwargs: object) -> FakeResponse:
        return FakeResponse(status_code=302, headers={})

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        download_poster_image(tmp_path, "http://example.com/poster.jpg", POSTER_UUID)
        is False
    )
    assert not any(tmp_path.iterdir())


def test_download_poster_resolves_relative_redirect_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jpeg_bytes = _make_small_jpeg_bytes()
    calls: list[str] = []

    def fake_get(url: str, *_args: object, **_kwargs: object) -> FakeResponse:
        calls.append(url)
        if url == "http://example.com/dir/poster.jpg":
            return FakeResponse(
                status_code=302,
                headers={"Location": "/img/poster.jpg"},
            )
        if url == "http://example.com/img/poster.jpg":
            return FakeResponse(chunks=[jpeg_bytes])
        raise AssertionError(f"unexpected fetch URL: {url}")  # noqa: TRY003, EM102

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        download_poster_image(
            tmp_path, "http://example.com/dir/poster.jpg", POSTER_UUID
        )
        is True
    )
    assert calls == [
        "http://example.com/dir/poster.jpg",
        "http://example.com/img/poster.jpg",
    ]
    assert tmp_path.joinpath(str(POSTER_UUID)).with_suffix(".jpg").exists()


def test_download_poster_skips_unsafe_url_without_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _addrinfo_for_ips("127.0.0.1"))

    def must_not_get(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(  # noqa: TRY003
            "requests.get must not be called for unsafe poster URLs"  # noqa: EM101
        )

    monkeypatch.setattr(utils.requests, "get", must_not_get)

    assert (
        download_poster_image(tmp_path, "http://127.0.0.1/poster.jpg", POSTER_UUID)
        is False
    )
    assert not any(tmp_path.iterdir())
