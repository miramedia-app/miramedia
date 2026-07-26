"""Unit tests for miramedia.subtitles.bazarr_client."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

from miramedia.subtitles.bazarr_client import BazarrClient


@pytest.fixture
def client() -> BazarrClient:
    return BazarrClient(url="http://bazarr:6767", api_key="secret-key")


def test_notify_episode_files_imported_payload(client: BazarrClient) -> None:
    with patch.object(client.session, "post", return_value=_ok_response()) as post:
        ok = client.notify_episode_files_imported([42], [7])

    assert ok is True
    post.assert_called_once_with(
        "http://bazarr:6767/api/webhooks/sonarr",
        json={
            "eventType": "Download",
            "episodeFiles": [{"id": 42}],
            "episodes": [{"id": 7}],
        },
        timeout=30,
    )


def test_notify_movie_file_imported_payload(client: BazarrClient) -> None:
    with patch.object(client.session, "post", return_value=_ok_response()) as post:
        ok = client.notify_movie_file_imported(99, 3)

    assert ok is True
    post.assert_called_once_with(
        "http://bazarr:6767/api/webhooks/radarr",
        json={
            "eventType": "Download",
            "movieFile": {"id": 99},
            "movie": {"id": 3},
        },
        timeout=30,
    )


def test_client_sets_x_api_key_header() -> None:
    client = BazarrClient(url="http://bazarr:6767", api_key="secret-key")
    assert client.session.headers["X-API-KEY"] == "secret-key"


def test_post_connection_error_returns_false(client: BazarrClient) -> None:
    with patch.object(
        client.session,
        "post",
        side_effect=RequestsConnectionError("down"),
    ):
        assert client.notify_episode_files_imported([1], [2]) is False


def test_post_timeout_returns_false(client: BazarrClient) -> None:
    with patch.object(client.session, "post", side_effect=Timeout("slow")):
        assert client.notify_movie_file_imported(1, 2) is False


def test_post_http_error_returns_false(client: BazarrClient) -> None:
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("500")
    with patch.object(client.session, "post", return_value=response):
        assert client.notify_episode_files_imported([1], [2]) is False


def test_test_connection_success(client: BazarrClient) -> None:
    with patch.object(client.session, "get", return_value=_ok_response({"ok": True})):
        assert client.test_connection() is True


def _ok_response(json_data: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = json_data or {}
    return response
