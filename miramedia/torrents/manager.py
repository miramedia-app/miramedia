import logging
import threading
from enum import Enum
from typing import TYPE_CHECKING

from miramedia.config import MiraMediaConfig
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.backends.abstract_download_client import (
    AbstractDownloadClient,
)

if TYPE_CHECKING:
    from miramedia.torrents.utils import TorrentFile
from miramedia.torrents.backends.native import NativeDownloadClient
from miramedia.torrents.backends.qbittorrent import QbittorrentDownloadClient
from miramedia.torrents.backends.sabnzbd import SabnzbdDownloadClient
from miramedia.torrents.backends.transmission import (
    TransmissionDownloadClient,
)
from miramedia.torrents.schemas import Torrent, TorrentStatus

log = logging.getLogger(__name__)


class DownloadClientType(Enum):
    """Types of download clients supported"""

    TORRENT = "torrent"
    USENET = "usenet"


class DownloadManager:
    """
    Manages download clients and routes downloads to the appropriate client
    based on the content type (torrent vs usenet).
    Only one torrent client and one usenet client are active at a time.
    """

    def __init__(self) -> None:
        self._torrent_client: AbstractDownloadClient | None = None
        self._usenet_client: AbstractDownloadClient | None = None
        self.config = MiraMediaConfig().torrents
        self._initialize_clients()

    def _initialize_clients(self) -> None:
        """Initialize and register the default download clients"""

        # Initialize torrent clients (prioritize qBittorrent, fallback to Transmission)
        if self.config.qbittorrent.enabled:
            try:
                self._torrent_client = QbittorrentDownloadClient()
            except Exception:
                log.exception("Failed to initialize qBittorrent client")

        # If qBittorrent is not available or failed, try Transmission
        if self._torrent_client is None and self.config.transmission.enabled:
            try:
                self._torrent_client = TransmissionDownloadClient()
            except Exception:
                log.exception("Failed to initialize Transmission client")

        # If no external torrent client available, try native client
        if self._torrent_client is None and self.config.native.enabled:
            try:
                self._torrent_client = NativeDownloadClient()
            except Exception:
                log.exception("Failed to initialize native torrent client")

        # Initialize SABnzbd client for usenet
        if self.config.sabnzbd.enabled:
            try:
                self._usenet_client = SabnzbdDownloadClient()
            except Exception:
                log.exception("Failed to initialize SABnzbd client")

        active_clients = []
        if self._torrent_client:
            active_clients.append(f"torrent ({self._torrent_client.name})")
        if self._usenet_client:
            active_clients.append(f"usenet ({self._usenet_client.name})")

    def close(self) -> None:
        for client in (self._torrent_client, self._usenet_client):
            if client is not None:
                try:
                    client.close()
                except Exception:
                    log.exception("Failed to close download client %s", client.name)

    def check_connections(self) -> dict[str, bool]:
        """Probe each configured client; True = reachable. Never raises."""
        results: dict[str, bool] = {}
        for client in (self._torrent_client, self._usenet_client):
            if client is None:
                continue
            try:
                client.check_connection()
                results[client.name] = True
            except Exception:
                log.exception("Connectivity check failed for %s", client.name)
                results[client.name] = False
        return results

    def _get_appropriate_client(
        self, indexer_result: IndexerQueryResult | Torrent
    ) -> AbstractDownloadClient:
        """
        Select the appropriate download client based on the indexer result

        :param indexer_result: The indexer query result to determine client type
        :return: The appropriate download client
        :raises RuntimeError: If no suitable client is available
        """
        # Use the usenet flag from the indexer result to determine the client type
        if indexer_result.usenet:
            if not self._usenet_client:
                msg = "No usenet download client configured"
                raise RuntimeError(msg)
            return self._usenet_client
        if not self._torrent_client:
            msg = "No torrent download client configured"
            raise RuntimeError(msg)
        return self._torrent_client

    def download(self, indexer_result: IndexerQueryResult) -> Torrent:
        """
        Download content using the appropriate client

        :param indexer_result: The indexer query result to download
        :return: The torrent object representing the download
        """
        log.info(f"Processing download request for: {indexer_result.title}")

        client = self._get_appropriate_client(indexer_result)
        return client.download_torrent(indexer_result)

    def remove_torrent(self, torrent: Torrent, delete_data: bool = False) -> None:
        """
        Remove a torrent using the appropriate client

        :param torrent: The torrent to remove
        :param delete_data: Whether to delete the downloaded data
        """
        log.info(f"Removing torrent: {torrent.title}")

        client = self._get_appropriate_client(torrent)
        client.remove_torrent(torrent, delete_data)

    def get_torrent_status(
        self, torrent: Torrent
    ) -> tuple[TorrentStatus, float, int, int, int]:
        """
        Get the status and progress of a torrent using the appropriate client

        :param torrent: The torrent to get status for
        :return: A tuple of (status, progress, num_peers, num_seeds, download_speed_bytes)
        """
        client = self._get_appropriate_client(torrent)
        return client.get_torrent_status(torrent)

    def pause_torrent(self, torrent: Torrent) -> None:
        """
        Pause a torrent using the appropriate client

        :param torrent: The torrent to pause
        """
        log.debug(f"Pausing torrent: {torrent.title}")

        client = self._get_appropriate_client(torrent)
        client.pause_torrent(torrent)

    def resume_torrent(self, torrent: Torrent) -> None:
        """
        Resume a torrent using the appropriate client

        :param torrent: The torrent to resume
        """
        log.debug(f"Resuming torrent: {torrent.title}")

        client = self._get_appropriate_client(torrent)
        client.resume_torrent(torrent)

    def get_torrent_files(self, torrent: Torrent) -> "list[TorrentFile] | None":
        """Return the file list known to the download client, or None."""
        client = self._get_appropriate_client(torrent)
        return client.get_torrent_files(torrent)


_manager_lock = threading.Lock()
_manager: DownloadManager | None = None


def get_download_manager() -> DownloadManager:
    """Process-level lazily-initialized DownloadManager (thread-safe)."""
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = DownloadManager()
    return _manager


def reset_download_manager() -> None:
    """Close and drop the singleton; next access rebuilds from current config.

    Called after runtime settings edits to the [torrents] section and from
    test fixtures.
    """
    global _manager
    with _manager_lock:
        if _manager is not None:
            _manager.close()
            _manager = None
