"""Tests for guarded torrent payload HTTP retrieval."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents import utils
from miramedia.torrents.utils import (
    _MAX_TORRENT_PAYLOAD_BYTES,
    _MAX_TORRENT_PAYLOAD_REDIRECTS,
    _fetch_torrent_payload,
    _guarded_fetch_torrent_bytes,
    get_torrent_hash,
    inspect_torrent,
    torrent_sidecar_under_root,
)

_PUBLIC_IPV4 = "93.184.216.34"
_PASSKEY = "SECRET_PASSKEY_TOKEN"


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
        msg = "unexpected host in test: " + host
        raise socket.gaierror(msg)

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture
def torrent_config(monkeypatch: pytest.MonkeyPatch, tmp_path):
    completed = tmp_path / "completed"
    completed.mkdir()

    def config():
        return SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=completed,
                incomplete_torrent_path="",
                torrent_directory=str(completed),
            ),
            indexers=SimpleNamespace(timeout_seconds=5),
        )

    monkeypatch.setattr(utils, "MiraMediaConfig", config)
    return completed


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

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)

    def iter_content(self, chunk_size: int = 0):
        del chunk_size
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


def _indexer(title: str, download_url: str) -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url=download_url,
        flags=[],
        size=1,
        usenet=False,
        age=0,
        indexer="test",
    )


def test_guarded_fetch_public_destination_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"d4:teste"
    seen: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> FakeResponse:
        seen["url"] = url
        seen["kwargs"] = kwargs
        return FakeResponse(chunks=[payload])

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        _guarded_fetch_torrent_bytes(
            f"https://example.com/files/safe.torrent?passkey={_PASSKEY}",
            timeout=5,
        )
        == payload
    )
    assert seen["kwargs"] == {
        "stream": True,
        "timeout": 5,
        "allow_redirects": False,
    }


@pytest.mark.parametrize(
    ("url", "ips"),
    [
        ("http://127.0.0.1/a.torrent", ("127.0.0.1",)),
        ("http://localhost/a.torrent", ("127.0.0.1",)),
        ("http://[::1]/a.torrent", ("::1",)),
        ("http://10.0.0.1/a.torrent", ("10.0.0.1",)),
        ("http://169.254.0.1/a.torrent", ("169.254.0.1",)),
        ("http://224.0.0.1/a.torrent", ("224.0.0.1",)),
        ("http://0.0.0.0/a.torrent", ("0.0.0.0",)),  # noqa: S104
    ],
)
def test_guarded_fetch_rejects_blocked_addresses(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    ips: tuple[str, ...],
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _addrinfo_for_ips(*ips))
    with pytest.raises(ValueError, match="Blocked resolved address"):
        _guarded_fetch_torrent_bytes(url, timeout=5)


def test_guarded_fetch_rejects_mixed_dns_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _addrinfo_for_ips(_PUBLIC_IPV4, "10.0.0.1"),
    )
    with pytest.raises(ValueError, match="Blocked resolved address"):
        _guarded_fetch_torrent_bytes("http://example.com/a.torrent", timeout=5)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/a.torrent",
        "file:///etc/passwd",
        f"http://user:{_PASSKEY}@example.com/a.torrent",
        "http://",
    ],
)
def test_guarded_fetch_rejects_invalid_schemes_and_userinfo(url: str) -> None:
    with pytest.raises(ValueError, match=r"."):
        _guarded_fetch_torrent_bytes(url, timeout=5)


def test_guarded_fetch_rejects_redirect_to_private_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    def fake_get(_url: str, **_kwargs: object) -> FakeResponse:
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(
                status_code=302,
                headers={"Location": "http://127.0.0.1/evil.torrent"},
            )
        return FakeResponse(chunks=[b"x"])

    monkeypatch.setattr(utils.requests, "get", fake_get)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *_a, **_k: _addrinfo_for_ips(
            "127.0.0.1" if host in {"127.0.0.1", "localhost"} else _PUBLIC_IPV4
        )(host, None),
    )

    with pytest.raises(ValueError, match="Blocked resolved address"):
        _guarded_fetch_torrent_bytes("http://example.com/a.torrent", timeout=5)


def test_guarded_fetch_rejects_redirect_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(_url: str, **_kwargs: object) -> FakeResponse:
        return FakeResponse(
            status_code=302,
            headers={"Location": "/loop.torrent"},
        )

    monkeypatch.setattr(utils.requests, "get", fake_get)

    with pytest.raises(
        ValueError, match="Exceeded maximum number of torrent payload redirects"
    ):
        _guarded_fetch_torrent_bytes("http://example.com/a.torrent", timeout=5)


def test_guarded_fetch_follows_relative_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}
    payload = b"d4:done"

    def fake_get(url: str, **_kwargs: object) -> FakeResponse:
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse(status_code=302, headers={"Location": "/final.torrent"})
        assert url == "http://example.com/final.torrent"
        return FakeResponse(chunks=[payload])

    monkeypatch.setattr(utils.requests, "get", fake_get)

    assert (
        _guarded_fetch_torrent_bytes("http://example.com/a.torrent", timeout=5)
        == payload
    )


def test_guarded_fetch_rejects_oversized_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        utils.requests,
        "get",
        lambda *_a, **_k: FakeResponse(
            headers={"Content-Length": str(_MAX_TORRENT_PAYLOAD_BYTES + 1)}
        ),
    )

    with pytest.raises(ValueError, match="Content-Length"):
        _guarded_fetch_torrent_bytes("http://example.com/a.torrent", timeout=5)


def test_guarded_fetch_rejects_oversized_streamed_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = b"x" * (1 << 16)
    chunks = [chunk] * ((_MAX_TORRENT_PAYLOAD_BYTES // len(chunk)) + 2)

    monkeypatch.setattr(
        utils.requests,
        "get",
        lambda *_a, **_k: FakeResponse(chunks=chunks),
    )

    with pytest.raises(ValueError, match="streamed body exceeds"):
        _guarded_fetch_torrent_bytes("http://example.com/a.torrent", timeout=5)


def test_guarded_fetch_accepts_boundary_size_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"x" * _MAX_TORRENT_PAYLOAD_BYTES
    response = FakeResponse(chunks=[payload])

    monkeypatch.setattr(utils.requests, "get", lambda *_a, **_k: response)

    assert (
        _guarded_fetch_torrent_bytes("http://example.com/a.torrent", timeout=5)
        == payload
    )
    assert response.closed is True


def test_guarded_fetch_closes_response_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(status_code=500)

    monkeypatch.setattr(utils.requests, "get", lambda *_a, **_k: response)

    with pytest.raises(requests.HTTPError):
        _guarded_fetch_torrent_bytes("http://example.com/a.torrent", timeout=5)
    assert response.closed is True


def test_fetch_torrent_payload_returns_none_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        utils,
        "_guarded_fetch_torrent_bytes",
        MagicMock(side_effect=ValueError("blocked")),
    )

    result = _fetch_torrent_payload(
        f"http://example.com/a.torrent?passkey={_PASSKEY}",
        "Safe.Title",
    )
    assert result is None


def test_inspect_torrent_returns_none_when_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(utils, "_fetch_torrent_payload", lambda *_a, **_k: None)

    inspection = inspect_torrent(
        _indexer("Safe.Title", f"http://example.com/a.torrent?passkey={_PASSKEY}")
    )
    assert inspection == utils.TorrentInspection(info_hash=None, files=None)


def test_get_torrent_hash_does_not_leave_partial_sidecar_on_oversized_body(
    torrent_config,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    title = "Safe.Release.1080p"
    chunk = b"x" * (1 << 16)
    chunks = [chunk] * ((_MAX_TORRENT_PAYLOAD_BYTES // len(chunk)) + 2)
    monkeypatch.setattr(
        utils.requests,
        "get",
        lambda *_a, **_k: FakeResponse(chunks=chunks),
    )
    monkeypatch.setattr(
        utils,
        "_parse_torrent_bytes",
        lambda _content: ("a" * 40, title, []),
    )

    with pytest.raises(ValueError, match="streamed body exceeds"):
        get_torrent_hash(
            _indexer(
                title,
                download_url=f"http://example.com/safe.torrent?passkey={_PASSKEY}",
            )
        )

    sidecar = torrent_sidecar_under_root(torrent_config, title)
    assert not sidecar.exists()


def test_redirect_limit_constant_matches_follow_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = requests.Session()

    def fake_get(_url: str, **_kwargs: object) -> FakeResponse:
        return FakeResponse(status_code=302, headers={"Location": "/next"})

    monkeypatch.setattr(session, "get", fake_get)

    with pytest.raises(RuntimeError, match="Exceeded maximum number of redirects"):
        utils.follow_redirects_to_final_torrent_url(
            "http://example.com/start",
            session=session,
            timeout=1,
        )

    assert _MAX_TORRENT_PAYLOAD_REDIRECTS == 5
