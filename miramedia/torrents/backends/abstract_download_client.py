from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.schemas import Torrent, TorrentStatus

if TYPE_CHECKING:
    from miramedia.torrents.utils import TorrentFile


class AbstractDownloadClient(ABC):
    """
    Abstract base class for download clients.
    Defines the interface that all download clients must implement.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def download_torrent(self, indexer_result: IndexerQueryResult) -> Torrent:
        """
        Add a torrent to the download client and return the torrent object.

        :param indexer_result: The indexer query result of the torrent file to download.
        :return: The torrent object with calculated hash and initial status.
        """

    @abstractmethod
    def remove_torrent(self, torrent: Torrent, delete_data: bool = False) -> None:
        """
        Remove a torrent from the download client.

        :param torrent: The torrent to remove.
        :param delete_data: Whether to delete the downloaded data.
        """

    @abstractmethod
    def get_torrent_status(
        self, torrent: Torrent
    ) -> tuple[TorrentStatus, float, int, int, int]:
        """
        Get the status and download progress of a specific torrent.

        :param torrent: The torrent to get the status of.
        :return: A tuple of (status, progress, num_peers, num_seeds, download_speed_bytes).
        """

    @abstractmethod
    def pause_torrent(self, torrent: Torrent) -> None:
        """
        Pause a torrent download.

        :param torrent: The torrent to pause.
        """

    @abstractmethod
    def resume_torrent(self, torrent: Torrent) -> None:
        """
        Resume a torrent download.

        :param torrent: The torrent to resume.
        """

    def get_torrent_files(self, torrent: Torrent) -> "list[TorrentFile] | None":  # noqa: ARG002 — abstract interface method, overridden by subclasses
        """Return the files the client has discovered for this torrent.

        Returns ``None`` when the file list is not yet known — e.g. a magnet
        whose swarm metadata hasn't arrived. Each entry pairs the relative
        file path with the file's size in bytes (``0`` when the backend
        can't report a size). The default implementation returns ``None`` so
        backends without a file-listing API stay backward-compatible.
        """
        return None
