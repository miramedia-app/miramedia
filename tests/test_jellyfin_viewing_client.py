"""Jellyfin adapter tests (fixture JSON and mock transport, no live server)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from miramedia.viewing_sync.jellyfin.client import (
    JellyfinClient,
    JellyfinError,
    _auth_header,
    jellyfin_item_to_event,
)
from miramedia.viewing_sync.redact import redact_secret_text

TEST_API_KEY = "test-api-key"
JELLYFIN_BASE = "http://127.0.0.1:8096"


def _json_response(
    payload: object,
    *,
    status_code: int = 200,
    content: bytes | None = None,
) -> httpx.Response:
    request = httpx.Request("GET", JELLYFIN_BASE)
    if content is not None:
        return httpx.Response(status_code, content=content, request=request)
    return httpx.Response(status_code, json=payload, request=request)


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str = TEST_API_KEY,
) -> JellyfinClient:
    client = JellyfinClient(
        url=JELLYFIN_BASE,
        api_key=api_key,
        timeout_seconds=5,
    )
    client._client.close()
    client._client = httpx.Client(
        base_url=JELLYFIN_BASE,
        headers={
            "Accept": "application/json",
            "Authorization": _auth_header(api_key),
        },
        transport=httpx.MockTransport(handler),
        timeout=5,
    )
    return client


def test_auth_header_uses_mediabrowser_token() -> None:
    header = _auth_header("secret-key")
    assert 'Token="secret-key"' in header
    assert 'Client="MiraMedia"' in header


def test_jellyfin_item_to_event_converts_ticks() -> None:
    raw = {
        "Id": "item-1",
        "Type": "Movie",
        "Name": "Example",
        "ProductionYear": 2020,
        "RunTimeTicks": 100_000 * 10_000,
        "ProviderIds": {"Imdb": "tt123"},
        "UserData": {
            "PlaybackPositionTicks": 50_000 * 10_000,
            "Played": True,
            "PlayCount": 1,
            "LastPlayedDate": "2026-01-01T00:00:00Z",
        },
    }
    event = jellyfin_item_to_event(raw, connector_user_id="user-1")
    assert event is not None
    assert event.position_ms == 50_000
    assert event.duration_ms == 100_000
    assert event.payload_digest


def test_jellyfin_item_without_play_signal_is_dropped() -> None:
    raw = {
        "Id": "item-2",
        "Type": "Movie",
        "ProviderIds": {"Imdb": "tt123"},
        "UserData": {"Played": False, "PlayCount": 0, "PlaybackPositionTicks": 0},
    }
    assert jellyfin_item_to_event(raw, connector_user_id="user-1") is None


def test_redact_secret_text_masks_api_key() -> None:
    text = "request failed Authorization: MediaBrowser Token=abc123"
    redacted = redact_secret_text(text, api_key="abc123")
    assert "abc123" not in redacted
    assert "***" in redacted


def test_iter_user_items_paginates_until_total_and_empty_batch() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        start_index = int(request.url.params["startIndex"])
        calls.append(start_index)
        if start_index == 0:
            return _json_response(
                {
                    "Items": [{"Id": "a"}, {"Id": "b"}],
                    "TotalRecordCount": 5,
                }
            )
        if start_index == 2:
            return _json_response(
                {
                    "Items": [{"Id": "c"}],
                    "TotalRecordCount": 5,
                }
            )
        return _json_response({"Items": [], "TotalRecordCount": 5})

    client = _make_client(handler)
    try:
        items = client.iter_user_items("user-1")
    finally:
        client.close()

    assert [item["Id"] for item in items] == ["a", "b", "c"]
    assert calls == [0, 2, 3]


def test_iter_user_items_encodes_min_last_played_date_as_utc_iso() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["minLastPlayedDate"] = request.url.params["minLastPlayedDate"]
        return _json_response({"Items": [], "TotalRecordCount": 0})

    client = _make_client(handler)
    try:
        aware = datetime(2026, 3, 15, 14, 30, tzinfo=UTC)
        client.iter_user_items("user-1", min_last_played_date=aware)
    finally:
        client.close()

    assert seen["minLastPlayedDate"] == "2026-03-15T14:30:00+00:00"


@pytest.mark.parametrize("retry_status", [408, 429, 500, 502, 503, 504])
def test_request_retries_once_on_retryable_status_then_succeeds(
    retry_status: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[int] = []
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return _json_response({"error": "busy"}, status_code=retry_status)
        return _json_response([{"Id": "user-1", "Name": "Alice"}])

    monkeypatch.setattr(
        "miramedia.viewing_sync.jellyfin.client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    client = _make_client(handler)
    try:
        users = client.list_users()
    finally:
        client.close()

    assert len(attempts) == 2
    assert sleeps == [0.5]
    assert users[0].id == "user-1"


def test_request_raises_jellyfin_error_on_terminal_http_status() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response({"Message": "missing"}, status_code=404)

    client = _make_client(handler)
    try:
        with pytest.raises(JellyfinError, match=r"HTTP 404") as exc_info:
            client.list_users()
    finally:
        client.close()

    assert TEST_API_KEY not in str(exc_info.value)


def test_request_raises_redacted_jellyfin_error_after_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        message = f"connect failed token={TEST_API_KEY}"
        raise httpx.ConnectError(message, request=httpx.Request("GET", JELLYFIN_BASE))

    monkeypatch.setattr(
        "miramedia.viewing_sync.jellyfin.client.time.sleep",
        lambda seconds: sleeps.append(seconds),
    )

    client = _make_client(handler)
    try:
        with pytest.raises(JellyfinError) as exc_info:
            client.list_users()
    finally:
        client.close()

    assert TEST_API_KEY not in str(exc_info.value)
    assert sleeps == [0.5, 1.0]


def test_request_returns_none_for_empty_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response({}, content=b"")

    client = _make_client(handler)
    try:
        assert client._request("GET", "/Users") is None
    finally:
        client.close()


def test_request_propagates_json_decode_error_for_malformed_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response({}, content=b"not-json")

    client = _make_client(handler)
    try:
        with pytest.raises(json.JSONDecodeError):
            client._request("GET", "/Users")
    finally:
        client.close()
