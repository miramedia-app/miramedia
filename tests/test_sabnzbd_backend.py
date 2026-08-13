"""Unit tests for the SABnzbd download client backend."""

from __future__ import annotations

from typing import Any, NoReturn

import httpx

from miramedia.torrents.backends.sabnzbd import SabnzbdDownloadClient
from miramedia.torrents.backends.sabnzbd_http import SabnzbdHttpClient
from miramedia.torrents.schemas import Quality, Torrent, TorrentStatus

NZO_ID = "SABnzbd_nzo_x"
API_URL = "http://localhost:8080/api"
TEST_API_KEY = "test-api-key"


def _json_response(payload: dict[str, Any]) -> httpx.Response:
    request = httpx.Request("GET", API_URL)
    return httpx.Response(200, json=payload, request=request)


def _make_backend(handler: httpx.MockTransport) -> SabnzbdDownloadClient:
    backend = SabnzbdDownloadClient.__new__(SabnzbdDownloadClient)
    backend.client = SabnzbdHttpClient(
        host="http://localhost",
        port=8080,
        api_key=TEST_API_KEY,
        base_path="/api",
        http_client=httpx.Client(transport=handler),
    )
    return backend


def _make_torrent() -> Torrent:
    return Torrent(
        status=TorrentStatus.unknown,
        title="Example.Release",
        quality=Quality.hd,
        hash=NZO_ID,
        usenet=True,
    )


def _unexpected_mode(mode: str) -> NoReturn:
    msg = f"unexpected mode={mode!r}"
    raise AssertionError(msg)


def test_queue_hit_downloading() -> None:
    history_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal history_called
        mode = request.url.params["mode"]
        if mode == "queue":
            return _json_response(
                {
                    "queue": {
                        "status": "Downloading",
                        "kbpersec": "100",
                        "slots": [
                            {
                                "nzo_id": NZO_ID,
                                "status": "Downloading",
                                "percentage": "42",
                            }
                        ],
                    }
                }
            )
        if mode == "history":
            history_called = True
            return _json_response({"history": {"slots": []}})
        _unexpected_mode(mode)

    backend = _make_backend(httpx.MockTransport(handler))
    status, progress, num_peers, num_seeds, dl_speed = backend.get_torrent_status(
        _make_torrent()
    )

    assert status == TorrentStatus.downloading
    assert progress == 42.0
    assert num_peers == 0
    assert num_seeds == 0
    assert dl_speed == 102400
    assert history_called is False


def test_queue_hit_extracting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["mode"]
        if mode == "queue":
            return _json_response(
                {
                    "queue": {
                        "status": "Downloading",
                        "kbpersec": "0",
                        "slots": [
                            {
                                "nzo_id": NZO_ID,
                                "status": "Extracting",
                                "percentage": "99",
                            }
                        ],
                    }
                }
            )
        _unexpected_mode(mode)

    backend = _make_backend(httpx.MockTransport(handler))
    status, progress, *_ = backend.get_torrent_status(_make_torrent())

    assert status == TorrentStatus.downloading
    assert progress == 99.0


def test_queue_miss_history_completed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["mode"]
        if mode == "queue":
            return _json_response({"queue": {"status": "Idle", "slots": []}})
        if mode == "history":
            assert request.url.params["nzo_ids"] == NZO_ID
            return _json_response(
                {"history": {"slots": [{"nzo_id": NZO_ID, "status": "Completed"}]}}
            )
        _unexpected_mode(mode)

    backend = _make_backend(httpx.MockTransport(handler))
    status, progress, num_peers, num_seeds, dl_speed = backend.get_torrent_status(
        _make_torrent()
    )

    assert status == TorrentStatus.finished
    assert progress == 100.0
    assert num_peers == 0
    assert num_seeds == 0
    assert dl_speed == 0


def test_queue_miss_history_failed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["mode"]
        if mode == "queue":
            return _json_response({"queue": {"status": "Idle", "slots": []}})
        if mode == "history":
            return _json_response(
                {"history": {"slots": [{"nzo_id": NZO_ID, "status": "Failed"}]}}
            )
        _unexpected_mode(mode)

    backend = _make_backend(httpx.MockTransport(handler))
    status, progress, *_ = backend.get_torrent_status(_make_torrent())

    assert status == TorrentStatus.error
    assert progress == 0.0


def test_both_miss_returns_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["mode"]
        if mode == "queue":
            return _json_response({"queue": {"status": "Idle", "slots": []}})
        if mode == "history":
            return _json_response({"history": {"slots": []}})
        _unexpected_mode(mode)

    backend = _make_backend(httpx.MockTransport(handler))
    status, progress, *_ = backend.get_torrent_status(_make_torrent())

    assert status == TorrentStatus.unknown
    assert progress == 0.0


def test_history_error_returns_unknown() -> None:
    connect_error = "connection refused"

    def handler(request: httpx.Request) -> httpx.Response:
        mode = request.url.params["mode"]
        if mode == "queue":
            return _json_response({"queue": {"status": "Idle", "slots": []}})
        if mode == "history":
            raise httpx.ConnectError(connect_error)
        _unexpected_mode(mode)

    backend = _make_backend(httpx.MockTransport(handler))
    status, progress, *_ = backend.get_torrent_status(_make_torrent())

    assert status == TorrentStatus.unknown
    assert progress == 0.0
