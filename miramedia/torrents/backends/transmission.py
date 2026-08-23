import logging
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

import transmission_rpc

from miramedia.config import MiraMediaConfig
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.backends.abstract_download_client import (
    AbstractDownloadClient,
)
from miramedia.torrents.inspection import get_torrent_hash
from miramedia.torrents.paths import (
    torrent_dir_under_root,
    torrent_title_path_component,
)
from miramedia.torrents.schemas import Torrent, TorrentStatus

if TYPE_CHECKING:
    from miramedia.torrents.inspection import TorrentFile

log = logging.getLogger(__name__)


class TransmissionDownloadClient(AbstractDownloadClient):
    name = "transmission"

    # Transmission status mappings
    STATUS_MAPPING = MappingProxyType(
        {
            "stopped": TorrentStatus.unknown,
            "check pending": TorrentStatus.downloading,
            "checking": TorrentStatus.downloading,
            "download pending": TorrentStatus.downloading,
            "downloading": TorrentStatus.downloading,
            "seed pending": TorrentStatus.finished,
            "seeding": TorrentStatus.finished,
        }
    )

    def __init__(self) -> None:
        self.config = MiraMediaConfig().torrents.transmission
        self._client = transmission_rpc.Client(
            host=self.config.host,
            port=self.config.port,
            username=self.config.username,
            password=self.config.password,
            protocol="https" if self.config.https_enabled else "http",
            path=self.config.path,
        )

    def check_connection(self) -> None:
        self._client.session_stats()

    def download_torrent(self, indexer_result: IndexerQueryResult) -> Torrent:
        """
        Add a torrent to the Transmission client and return the torrent object.

        :param indexer_result: The indexer query result of the torrent file to download.
        :return: The torrent object with calculated hash and initial status.
        """
        torrent_title_path_component(indexer_result.title)
        torrent_hash = get_torrent_hash(torrent=indexer_result)
        download_dir = torrent_dir_under_root(
            MiraMediaConfig().misc.effective_completed_path, indexer_result.title
        )
        try:
            self._client.add_torrent(
                torrent=str(indexer_result.download_url),
                download_dir=str(download_dir),
            )

            log.info(
                "Successfully added torrent to Transmission: %s",
                indexer_result.title,
            )

        except Exception:
            log.exception("Failed to add torrent to Transmission")
            raise

        torrent = Torrent(
            status=TorrentStatus.unknown,
            title=indexer_result.title,
            quality=indexer_result.quality,
            hash=torrent_hash,
            usenet=False,
        )

        (
            torrent.status,
            torrent.progress,
            torrent.num_peers,
            torrent.num_seeds,
            torrent.download_speed,
        ) = self.get_torrent_status(torrent)

        return torrent

    def remove_torrent(self, torrent: Torrent, delete_data: bool = False) -> None:
        """
        Remove a torrent from the Transmission client.

        :param torrent: The torrent to remove.
        :param delete_data: Whether to delete the downloaded data.
        """

        try:
            self._client.remove_torrent(torrent.hash, delete_data=delete_data)
        except Exception:
            log.exception("Failed to remove torrent")
            raise

    def get_torrent_status(
        self, torrent: Torrent
    ) -> tuple[TorrentStatus, float, int, int, int]:
        """
        Get the status and progress of a specific torrent.

        :param torrent: The torrent to get the status of.
        :return: A tuple of (status, progress, num_peers, num_seeds, download_speed_bytes).
        """

        try:
            transmission_torrent = self._client.get_torrent(torrent.hash)

            if transmission_torrent is None:
                log.warning("Torrent not found in Transmission: %s", torrent.hash)
                return TorrentStatus.unknown, 0.0, 0, 0, 0

            status = self.STATUS_MAPPING.get(
                transmission_torrent.status, TorrentStatus.unknown
            )
            progress = round(transmission_torrent.progress, 1)
            num_peers = transmission_torrent.peers_connected
            num_seeds = transmission_torrent.peers_sending_to_us
            dl_speed = transmission_torrent.rate_download

            if transmission_torrent.error != 0:
                status = TorrentStatus.error
                log.warning(
                    "Torrent %s has error status: %s",
                    torrent.title,
                    transmission_torrent.error_string,
                )
        except Exception:
            log.exception("Failed to get torrent status")
            return TorrentStatus.error, 0.0, 0, 0, 0

        return status, progress, num_peers, num_seeds, dl_speed

    def pause_torrent(self, torrent: Torrent) -> None:
        """
        Pause a torrent download.

        :param torrent: The torrent to pause.
        """
        try:
            self._client.stop_torrent(torrent.hash)
            log.debug("Successfully paused torrent: %s", torrent.title)

        except Exception:
            log.exception("Failed to pause torrent")
            raise

    def resume_torrent(self, torrent: Torrent) -> None:
        """
        Resume a torrent download.

        :param torrent: The torrent to resume.
        """
        try:
            self._client.start_torrent(torrent.hash)
            log.debug("Successfully resumed torrent: %s", torrent.title)

        except Exception:
            log.exception("Failed to resume torrent")
            raise

    def get_torrent_files(self, torrent: Torrent) -> "list[TorrentFile] | None":
        from miramedia.torrents.inspection import TorrentFile

        try:
            transmission_torrent = self._client.get_torrent(torrent.hash)
        except Exception:
            log.debug(
                "Transmission get_torrent lookup failed for %s",
                torrent.hash,
                exc_info=True,
            )
            return None
        if transmission_torrent is None:
            return None
        try:
            files = transmission_torrent.get_files()
        except Exception:
            log.debug("Transmission files() failed for %s", torrent.hash, exc_info=True)
            return None
        if not files:
            return None
        out: list[TorrentFile] = []
        for f in files:
            name = getattr(f, "name", None)
            if not name:
                continue
            try:
                size = int(getattr(f, "size", 0) or 0)
            except (TypeError, ValueError):
                size = 0
            out.append(TorrentFile(path=Path(name), size=size))
        return out or None
