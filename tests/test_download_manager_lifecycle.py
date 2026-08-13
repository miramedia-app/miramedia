"""DB-free tests for DownloadManager singleton lifecycle and constructor I/O."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import httpx
import pytest

from miramedia.config import MiraMediaConfig
from miramedia.torrents.backends.sabnzbd import SabnzbdDownloadClient
from miramedia.torrents.manager import (
    get_download_manager,
    reset_download_manager,
)

pytestmark = pytest.mark.usefixtures("_all_download_clients_disabled")


@pytest.fixture(autouse=True)
def _reset_download_manager_singleton() -> Generator[None]:
    yield
    reset_download_manager()


@pytest.fixture
def _all_download_clients_disabled() -> Generator[None]:
    cfg = MiraMediaConfig()
    cfg.torrents.qbittorrent.enabled = False
    cfg.torrents.transmission.enabled = False
    cfg.torrents.native.enabled = False
    cfg.torrents.sabnzbd.enabled = False
    yield
    reset_download_manager()


def test_sabnzbd_construction_does_no_network_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sabnzbd = MiraMediaConfig().torrents.sabnzbd
    sabnzbd.host = "http://localhost"
    sabnzbd.port = 8080
    sabnzbd.api_key = "test-key"

    def _reject_io(_request: httpx.Request) -> httpx.Response:
        msg = "network I/O during construction"
        raise AssertionError(msg)

    real_httpx_client = httpx.Client

    def _client_factory(*_args: object, **_kwargs: object) -> httpx.Client:
        return real_httpx_client(transport=httpx.MockTransport(_reject_io))

    monkeypatch.setattr(
        "miramedia.torrents.backends.sabnzbd_http.httpx.Client",
        _client_factory,
    )

    client = SabnzbdDownloadClient()
    with pytest.raises(AssertionError, match="network I/O during construction"):
        client.check_connection()


def test_get_download_manager_is_singleton() -> None:
    first = get_download_manager()
    second = get_download_manager()
    assert first is second


def test_reset_download_manager_closes_and_rebuilds() -> None:
    manager = get_download_manager()
    fake = MagicMock()
    fake.name = "fake-usenet"
    manager._usenet_client = fake

    reset_download_manager()
    fake.close.assert_called_once()

    rebuilt = get_download_manager()
    assert rebuilt is not manager


def test_check_connections_never_raises() -> None:
    manager = get_download_manager()
    fake = MagicMock()
    fake.name = "broken"
    fake.check_connection.side_effect = RuntimeError("down")
    manager._torrent_client = fake

    results = manager.check_connections()
    assert results == {"broken": False}
