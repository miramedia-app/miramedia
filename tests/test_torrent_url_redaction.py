"""Tests for torrent and magnet URL redaction in logs."""

from __future__ import annotations

import logging
import socket
from types import SimpleNamespace

import pytest

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents import fetch, inspection
from miramedia.torrents.fetch import (
    _MAGNET_URL_REDACTED,
    _TORRENT_URL_REDACTED,
    _fetch_torrent_payload,
    _redact_torrent_url,
    follow_redirects_to_final_torrent_url,
)
from miramedia.torrents.inspection import get_torrent_hash

_PASSKEY = "SECRET_PASSKEY_TOKEN"
_INFO_HASH = "0123456789abcdef0123456789abcdef01234567"
_MAGNET = (
    f"magnet:?xt=urn:btih:{_INFO_HASH}"
    f"&dn=Display+Name&tr=http%3A%2F%2Ftracker.example%2Fannounce%3Fpasskey%3D{_PASSKEY}"
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            f"https://user:{_PASSKEY}@tracker.example:8080/path/file.torrent?passkey={_PASSKEY}#frag",
            "https://tracker.example:8080/path/file.torrent",
        ),
        (
            f"http://tracker.example/file.torrent?passkey={_PASSKEY}",
            "http://tracker.example/file.torrent",
        ),
        (
            f"https://tracker.example/file.torrent#secret={_PASSKEY}",
            "https://tracker.example/file.torrent",
        ),
        ("", _TORRENT_URL_REDACTED),
        ("not-a-url", _TORRENT_URL_REDACTED),
        (_MAGNET, f"magnet:?xt=urn:btih:{_INFO_HASH}"),
        ("magnet:broken", _MAGNET_URL_REDACTED),
    ],
)
def test_redact_torrent_url(url: str, expected: str) -> None:
    assert _redact_torrent_url(url) == expected
    assert _PASSKEY not in _redact_torrent_url(url)


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


def _addrinfo_public(_host: str, port: object, *_args: object, **_kwargs: object):
    del port
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _addrinfo_public)


def test_fetch_torrent_payload_logs_redacted_url_on_failure(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fetch,
        "_guarded_fetch_torrent_bytes",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("blocked")),
    )

    with caplog.at_level(logging.DEBUG, logger="miramedia.torrents.fetch"):
        assert (
            _fetch_torrent_payload(
                f"http://tracker.example/file.torrent?passkey={_PASSKEY}",
                "Safe.Title",
            )
            is None
        )

    combined = caplog.text
    assert _PASSKEY not in combined
    assert "http://tracker.example/file.torrent" in combined


def test_get_torrent_hash_logs_redacted_magnet(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    completed = tmp_path / "completed"
    completed.mkdir()

    monkeypatch.setattr(
        inspection,
        "MiraMediaConfig",
        lambda: SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=completed,
                incomplete_torrent_path="",
                torrent_directory=str(completed),
            ),
            indexers=SimpleNamespace(timeout_seconds=5),
        ),
    )

    with caplog.at_level(logging.DEBUG, logger="miramedia.torrents.inspection"):
        get_torrent_hash(_indexer("Safe.Title", _MAGNET))

    combined = caplog.text
    assert _PASSKEY not in combined
    assert "tracker.example" not in combined
    assert f"magnet:?xt=urn:btih:{_INFO_HASH}" in combined


def test_get_torrent_hash_logs_redacted_http_url_on_invalid_schema_path(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    completed = tmp_path / "completed"
    completed.mkdir()
    title = "Safe.Title"
    payload = b"d4:teste"

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 200
            self.headers: dict[str, str] = {}
            self.closed = False

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int = 0):
            del chunk_size
            yield payload

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        inspection,
        "MiraMediaConfig",
        lambda: SimpleNamespace(
            misc=SimpleNamespace(
                effective_completed_path=completed,
                incomplete_torrent_path="",
                torrent_directory=str(completed),
            ),
            indexers=SimpleNamespace(timeout_seconds=5),
        ),
    )
    monkeypatch.setattr(
        inspection,
        "follow_redirects_to_final_torrent_url",
        lambda **_k: f"http://tracker.example/final.torrent?passkey={_PASSKEY}",
    )
    monkeypatch.setattr(fetch.requests, "get", lambda *_a, **_k: FakeResponse())
    monkeypatch.setattr(
        inspection,
        "_parse_torrent_bytes",
        lambda _content: ("a" * 40, title, []),
    )

    download_url = f"custom://tracker.example/start?passkey={_PASSKEY}"
    with caplog.at_level(logging.DEBUG, logger="miramedia.torrents.inspection"):
        get_torrent_hash(_indexer(title, download_url))

    combined = caplog.text
    assert _PASSKEY not in combined
    assert "custom://" not in combined
    assert "parsing torrent file: <redacted>" in combined


def test_follow_redirects_logs_redacted_target(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    class FakeResponse:
        def __init__(self) -> None:
            self.status_code = 302
            self.headers = {
                "Location": (
                    f"http://tracker.example/next.torrent?passkey={_PASSKEY}#frag"
                )
            }
            self.closed = False

        def raise_for_status(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    def fake_get(_url: str, **_kwargs: object) -> FakeResponse:
        calls["count"] += 1
        if calls["count"] == 1:
            return FakeResponse()
        final = FakeResponse()
        final.status_code = 200
        return final

    session = fetch.requests.Session()
    monkeypatch.setattr(session, "get", fake_get)

    with caplog.at_level(logging.DEBUG, logger="miramedia.torrents.fetch"):
        follow_redirects_to_final_torrent_url(
            f"http://tracker.example/start?passkey={_PASSKEY}",
            session=session,
            timeout=1,
        )

    combined = caplog.text
    assert _PASSKEY not in combined
    assert "http://tracker.example/next.torrent" in combined
