"""Characterization tests for qBittorrent, Transmission, and SABnzbd status mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from miramedia.torrents.backends.qbittorrent import QbittorrentDownloadClient
from miramedia.torrents.backends.sabnzbd import SabnzbdDownloadClient
from miramedia.torrents.backends.transmission import TransmissionDownloadClient
from miramedia.torrents.models import Quality
from miramedia.torrents.schemas import Torrent, TorrentStatus

QB_PROGRESS_INPUT = 0.4567
QB_PROGRESS_ROUNDED = 45.7
QB_PEERS = 3
QB_SEEDS = 5
QB_DL_SPEED = 12345

TR_PROGRESS_INPUT = 45.67
TR_PROGRESS_ROUNDED = 45.7
TR_PEERS = 3
TR_SEEDS = 5
TR_DL_SPEED = 12345

SAB_PROGRESS_INPUT = 45.67
SAB_PROGRESS_ROUNDED = 45.7
SAB_KBPERSEC = 12
SAB_DL_SPEED = SAB_KBPERSEC * 1024


def _sample_torrent() -> Torrent:
    return Torrent(
        status=TorrentStatus.unknown,
        title="Test.Release.1080p",
        quality=Quality.unknown,
        hash="a" * 40,
    )


def _make_qb_client(api: MagicMock) -> QbittorrentDownloadClient:
    client = object.__new__(QbittorrentDownloadClient)
    client.api_client = api
    return client


def _make_transmission_client(rpc: MagicMock) -> TransmissionDownloadClient:
    client = object.__new__(TransmissionDownloadClient)
    client._client = rpc
    return client


def _qb_torrent_info(state: str) -> list[dict[str, object]]:
    return [
        {
            "state": state,
            "progress": QB_PROGRESS_INPUT,
            "num_leechs": QB_PEERS,
            "num_seeds": QB_SEEDS,
            "dlspeed": QB_DL_SPEED,
        }
    ]


def _expected_qb_mapping(
    state: str,
) -> tuple[TorrentStatus, float, int, int, int]:
    if state in QbittorrentDownloadClient.DOWNLOADING_STATE:
        return (
            TorrentStatus.downloading,
            QB_PROGRESS_ROUNDED,
            QB_PEERS,
            QB_SEEDS,
            QB_DL_SPEED,
        )
    if state in QbittorrentDownloadClient.FINISHED_STATE:
        return (TorrentStatus.finished, 100.0, QB_PEERS, QB_SEEDS, 0)
    if state in QbittorrentDownloadClient.ERROR_STATE:
        return (TorrentStatus.error, QB_PROGRESS_ROUNDED, QB_PEERS, QB_SEEDS, 0)
    if state in QbittorrentDownloadClient.UNKNOWN_STATE:
        return (TorrentStatus.unknown, 0.0, 0, 0, 0)
    return (TorrentStatus.error, QB_PROGRESS_ROUNDED, QB_PEERS, QB_SEEDS, 0)


_QB_STATES = sorted(
    {
        *QbittorrentDownloadClient.DOWNLOADING_STATE,
        *QbittorrentDownloadClient.FINISHED_STATE,
        *QbittorrentDownloadClient.ERROR_STATE,
        *QbittorrentDownloadClient.UNKNOWN_STATE,
        "someFutureState",
    }
)


@pytest.mark.parametrize("state", _QB_STATES)
def test_qbittorrent_get_torrent_status_maps_client_state(state: str) -> None:
    api = MagicMock()
    api.torrents_info.return_value = _qb_torrent_info(state)
    client = _make_qb_client(api)
    torrent = _sample_torrent()

    result = client.get_torrent_status(torrent)

    assert result == _expected_qb_mapping(state)
    api.auth_log_in.assert_called_once()
    api.auth_log_out.assert_called_once()
    api.torrents_info.assert_called_once_with(torrent_hashes=torrent.hash)


def test_qbittorrent_get_torrent_status_empty_info_returns_unknown() -> None:
    api = MagicMock()
    api.torrents_info.return_value = []
    client = _make_qb_client(api)

    result = client.get_torrent_status(_sample_torrent())

    assert result == (TorrentStatus.unknown, 0.0, 0, 0, 0)
    api.auth_log_out.assert_called_once()


def test_qbittorrent_get_torrent_status_auth_log_out_on_torrents_info_error() -> None:
    api = MagicMock()
    api.torrents_info.side_effect = RuntimeError("lookup failed")
    client = _make_qb_client(api)

    with pytest.raises(RuntimeError, match="lookup failed"):
        client.get_torrent_status(_sample_torrent())

    api.auth_log_in.assert_called_once()
    api.auth_log_out.assert_called_once()


def _transmission_stub(
    *,
    status: str,
    progress: float = TR_PROGRESS_INPUT,
    peers_connected: int = TR_PEERS,
    peers_sending_to_us: int = TR_SEEDS,
    rate_download: int = TR_DL_SPEED,
    error: int = 0,
    error_string: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        progress=progress,
        peers_connected=peers_connected,
        peers_sending_to_us=peers_sending_to_us,
        rate_download=rate_download,
        error=error,
        error_string=error_string,
    )


_TRANSMISSION_STATUSES = [
    *TransmissionDownloadClient.STATUS_MAPPING.keys(),
    "not-a-real-transmission-status",
]


@pytest.mark.parametrize("status", _TRANSMISSION_STATUSES)
def test_transmission_get_torrent_status_maps_client_status(status: str) -> None:
    rpc = MagicMock()
    rpc.get_torrent.return_value = _transmission_stub(status=status)
    client = _make_transmission_client(rpc)

    result = client.get_torrent_status(_sample_torrent())

    expected_status = TransmissionDownloadClient.STATUS_MAPPING.get(
        status, TorrentStatus.unknown
    )
    assert result == (
        expected_status,
        TR_PROGRESS_ROUNDED,
        TR_PEERS,
        TR_SEEDS,
        TR_DL_SPEED,
    )
    rpc.get_torrent.assert_called_once_with(_sample_torrent().hash)


def test_transmission_get_torrent_status_missing_torrent_returns_unknown() -> None:
    rpc = MagicMock()
    rpc.get_torrent.return_value = None
    client = _make_transmission_client(rpc)

    result = client.get_torrent_status(_sample_torrent())

    assert result == (TorrentStatus.unknown, 0.0, 0, 0, 0)


@pytest.mark.parametrize(
    ("mapped_status", "error_code"),
    [
        ("downloading", 1),
        ("seeding", 2),
        ("stopped", 3),
    ],
)
def test_transmission_get_torrent_status_error_flag_overrides_mapped_status(
    mapped_status: str, error_code: int
) -> None:
    rpc = MagicMock()
    rpc.get_torrent.return_value = _transmission_stub(
        status=mapped_status,
        error=error_code,
        error_string="disk full",
    )
    client = _make_transmission_client(rpc)

    result = client.get_torrent_status(_sample_torrent())

    assert result[0] == TorrentStatus.error
    assert result[1:] == (TR_PROGRESS_ROUNDED, TR_PEERS, TR_SEEDS, TR_DL_SPEED)


def _make_sabnzbd_client(api: MagicMock) -> SabnzbdDownloadClient:
    client = object.__new__(SabnzbdDownloadClient)
    client.client = api
    return client


def _sab_queue(
    status: str,
    *,
    slots: list[dict[str, object]] | None = None,
    kbpersec: object = SAB_KBPERSEC,
) -> dict[str, object]:
    if slots is None:
        slots = [{"percentage": SAB_PROGRESS_INPUT}]
    return {
        "queue": {
            "status": status,
            "slots": slots,
            "kbpersec": kbpersec,
        }
    }


def _expected_sab_mapping(
    state: str,
) -> tuple[TorrentStatus, float, int, int, int]:
    if state in SabnzbdDownloadClient.DOWNLOADING_STATE:
        return (
            TorrentStatus.downloading,
            SAB_PROGRESS_ROUNDED,
            0,
            0,
            SAB_DL_SPEED,
        )
    if state in SabnzbdDownloadClient.FINISHED_STATE:
        return (TorrentStatus.finished, 100.0, 0, 0, 0)
    if state in SabnzbdDownloadClient.ERROR_STATE:
        return (TorrentStatus.error, SAB_PROGRESS_ROUNDED, 0, 0, SAB_DL_SPEED)
    return (TorrentStatus.unknown, SAB_PROGRESS_ROUNDED, 0, 0, SAB_DL_SPEED)


_SAB_STATES = sorted(
    {
        *SabnzbdDownloadClient.DOWNLOADING_STATE,
        *SabnzbdDownloadClient.FINISHED_STATE,
        *SabnzbdDownloadClient.ERROR_STATE,
        *SabnzbdDownloadClient.UNKNOWN_STATE,
        "SomethingNew",
    }
)


@pytest.mark.parametrize("state", _SAB_STATES)
def test_sabnzbd_get_torrent_status_maps_client_state(state: str) -> None:
    api = MagicMock()
    api.get_downloads.return_value = _sab_queue(state)
    client = _make_sabnzbd_client(api)
    torrent = _sample_torrent()

    result = client.get_torrent_status(torrent)

    assert result == _expected_sab_mapping(state)
    api.get_downloads.assert_called_once_with(nzo_ids=torrent.hash)


def test_sabnzbd_get_torrent_status_empty_slots_returns_zero_progress() -> None:
    api = MagicMock()
    api.get_downloads.return_value = _sab_queue("Downloading", slots=[])
    client = _make_sabnzbd_client(api)

    result = client.get_torrent_status(_sample_torrent())

    assert result == (TorrentStatus.downloading, 0.0, 0, 0, SAB_DL_SPEED)


def test_sabnzbd_get_torrent_status_non_numeric_percentage_returns_zero_progress() -> (
    None
):
    api = MagicMock()
    api.get_downloads.return_value = _sab_queue(
        "Downloading", slots=[{"percentage": "N/A"}]
    )
    client = _make_sabnzbd_client(api)

    result = client.get_torrent_status(_sample_torrent())

    assert result == (TorrentStatus.downloading, 0.0, 0, 0, SAB_DL_SPEED)


def test_sabnzbd_get_torrent_status_non_numeric_kbpersec_returns_zero_speed() -> None:
    api = MagicMock()
    api.get_downloads.return_value = _sab_queue("Downloading", kbpersec="N/A")
    client = _make_sabnzbd_client(api)

    result = client.get_torrent_status(_sample_torrent())

    assert result == (TorrentStatus.downloading, SAB_PROGRESS_ROUNDED, 0, 0, 0)


@pytest.mark.parametrize(
    "state",
    ["Downloading", "Completed", "Failed", "Unknown"],
)
def test_sabnzbd_get_torrent_status_peers_and_seeds_always_zero(
    state: str,
) -> None:
    api = MagicMock()
    api.get_downloads.return_value = _sab_queue(state)
    client = _make_sabnzbd_client(api)

    result = client.get_torrent_status(_sample_torrent())

    assert result[2] == 0
    assert result[3] == 0
