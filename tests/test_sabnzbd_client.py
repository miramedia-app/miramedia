"""Unit tests for the in-tree SABnzbd HTTP client."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from miramedia.torrents.backends.sabnzbd_http import SabnzbdApiError, SabnzbdHttpClient

API_URL = "http://localhost:8080/api"
TEST_API_KEY = "test-api-key"


def _json_response(payload: dict[str, Any]) -> httpx.Response:
    request = httpx.Request("GET", API_URL)
    return httpx.Response(200, json=payload, request=request)


def _make_client(handler: httpx.MockTransport) -> SabnzbdHttpClient:
    http = httpx.Client(transport=handler, base_url=API_URL)
    return SabnzbdHttpClient(
        host="http://localhost",
        port=8080,
        api_key=TEST_API_KEY,
        base_path="/api",
        http_client=http,
    )


def test_version_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "version"
        assert request.url.params["apikey"] == TEST_API_KEY
        assert request.url.params["output"] == "json"
        return _json_response({"version": "4.2.2"})

    client = _make_client(httpx.MockTransport(handler))
    assert client.version() == {"version": "4.2.2"}


def test_add_uri_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "addurl"
        assert request.url.params["name"] == "https://example.test/nzb"
        assert request.url.params["nzbname"] == "Example.Release"
        return _json_response({"status": True, "nzo_ids": ["SABnzbd_nzo_test"]})

    client = _make_client(httpx.MockTransport(handler))
    response = client.add_uri(url="https://example.test/nzb", nzbname="Example.Release")
    assert response["status"] is True
    assert response["nzo_ids"] == ["SABnzbd_nzo_test"]


def test_get_downloads_list_params_joined() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "queue"
        assert request.url.params["nzo_ids"] == "a,b"
        assert request.url.params["status"] == "Paused,Queued"
        return _json_response({"queue": {"status": "Downloading", "slots": []}})

    client = _make_client(httpx.MockTransport(handler))
    client.get_downloads(nzo_ids=["a", "b"], status=["Paused", "Queued"])


def test_history_list_params_joined() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["mode"] == "history"
        assert request.url.params["nzo_ids"] == "a,b"
        return _json_response(
            {
                "history": {
                    "slots": [
                        {"nzo_id": "a", "status": "Completed"},
                        {"nzo_id": "b", "status": "Failed"},
                    ]
                }
            }
        )

    client = _make_client(httpx.MockTransport(handler))
    response = client.history(nzo_ids=["a", "b"])
    assert response == {
        "history": {
            "slots": [
                {"nzo_id": "a", "status": "Completed"},
                {"nzo_id": "b", "status": "Failed"},
            ]
        }
    }


def test_get_downloads_omits_none_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert params["mode"] == "queue"
        assert params["nzo_ids"] == "x"
        assert "start" not in params
        assert "limit" not in params
        assert "search" not in params
        assert "category" not in params
        assert "priority" not in params
        assert "status" not in params
        return _json_response({"queue": {"status": "Downloading", "slots": []}})

    client = _make_client(httpx.MockTransport(handler))
    client.get_downloads(nzo_ids="x")


def test_http_status_error_raises_sabnzbd_api_error_without_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(SabnzbdApiError) as exc_info:
        client.version()
    assert TEST_API_KEY not in str(exc_info.value)


def test_version_connection_failure_raises() -> None:
    connect_error = "connection refused"

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(connect_error)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(httpx.ConnectError, match=connect_error):
        client.version()


def test_redirect_refused_without_following() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(
            301,
            headers={"location": "https://elsewhere.example/api"},
            request=request,
        )

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(SabnzbdApiError) as exc_info:
        client.version()
    message = str(exc_info.value)
    assert "redirect" in message.lower()
    assert TEST_API_KEY not in message
    assert "elsewhere.example" not in message
    assert call_count == 1


def test_status_false_envelope_raises_sabnzbd_api_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response({"status": False, "error": "API Key Incorrect"})

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(SabnzbdApiError) as exc_info:
        client.version()
    message = str(exc_info.value)
    assert "API Key Incorrect" in message
    assert TEST_API_KEY not in message


def test_non_json_body_raises_sabnzbd_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>login</html>", request=request)

    client = _make_client(httpx.MockTransport(handler))
    with pytest.raises(SabnzbdApiError) as exc_info:
        client.version()
    assert TEST_API_KEY not in str(exc_info.value)


def test_schemeless_host_rejected() -> None:
    with pytest.raises(ValueError, match="scheme") as exc_info:
        SabnzbdHttpClient(
            host="localhost",
            port=8080,
            api_key=TEST_API_KEY,
            http_client=httpx.Client(),
        )
    assert "scheme" in str(exc_info.value).lower()


def test_status_true_passes_through() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return _json_response({"status": True, "nzo_ids": ["x"]})

    client = _make_client(httpx.MockTransport(handler))
    assert client.add_uri(url="https://example.test/nzb") == {
        "status": True,
        "nzo_ids": ["x"],
    }
