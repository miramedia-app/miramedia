import logging
from pathlib import Path
from typing import TYPE_CHECKING

import qbittorrentapi
from qbittorrentapi import Conflict409Error

from miramedia.config import MiraMediaConfig
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.backends.abstract_download_client import (
    AbstractDownloadClient,
)
from miramedia.torrents.schemas import Torrent, TorrentStatus
from miramedia.torrents.utils import get_torrent_hash, torrent_title_path_component

if TYPE_CHECKING:
    from miramedia.torrents.utils import TorrentFile

log = logging.getLogger(__name__)


class QbittorrentDownloadClient(AbstractDownloadClient):
    name = "qbittorrent"

    DOWNLOADING_STATE = (
        "allocating",
        "downloading",
        "metaDL",
        "pausedDL",
        "queuedDL",
        "stalledDL",
        "checkingDL",
        "forcedDL",
        "moving",
        "stoppedDL",
        "forcedMetaDL",
        "metaDL",
    )
    FINISHED_STATE = (
        "uploading",
        "pausedUP",
        "queuedUP",
        "stalledUP",
        "checkingUP",
        "forcedUP",
        "stoppedUP",
    )
    ERROR_STATE = ("missingFiles", "error", "checkingResumeData")
    UNKNOWN_STATE = ("unknown",)

    def __init__(self) -> None:
        self.config = MiraMediaConfig().torrents.qbittorrent
        self.api_client = qbittorrentapi.Client(
            host=self.config.host,
            port=self.config.port,
            password=self.config.password,
            username=self.config.username,
        )
        try:
            self.api_client.auth_log_in()
        except Exception:
            log.exception("Failed to log into qbittorrent")
            raise

        categories = self.api_client.torrents_categories()
        log.debug(f"Found following categories in qBittorrent: {categories}")
        if self.config.category_name in categories:
            category = categories.get(self.config.category_name)
            if category.get("savePath") == self.config.category_save_path:
                log.debug(
                    f"Category '{self.config.category_name}' already exists in qBittorrent with the correct save path."
                )
                return
            # category exists but with a different save path, attempt to update it
            log.debug(
                f"Category '{self.config.category_name}' already exists in qBittorrent but with a different save path. Attempting to update it."
            )
            try:
                self.api_client.torrents_edit_category(
                    name=self.config.category_name,
                    save_path=self.config.category_save_path,
                )
            except Conflict409Error:
                log.exception(
                    f"Attempt to update category '{self.config.category_name}' in qBittorrent with a different save"
                    f" path failed. The configured save path and the save path saved in Qbittorrent differ,"
                    f" manually update it in the qBittorrent WebUI or change the save path in the MiraMedia"
                    f" config to match the one in qBittorrent."
                )
        else:
            # create category if it doesn't exist
            log.debug(
                f"Category '{self.config.category_name}' does not exist in qBittorrent. Attempting to create it."
            )
            try:
                self.api_client.torrents_create_category(
                    name=self.config.category_name,
                    save_path=self.config.category_save_path,
                )
            except Conflict409Error:
                log.exception(
                    f"Attempt to create category '{self.config.category_name}' in qBittorrent failed. The category already exists but was not found in the initial category list, manually check if the category exists in the qBittorrent WebUI or change the category name in the MiraMedia config."
                )

    def download_torrent(self, indexer_result: IndexerQueryResult) -> Torrent:
        """
        Add a torrent to the download client and return the torrent object.

        :param indexer_result: The indexer query result of the torrent file to download.
        :return: The torrent object with calculated hash and initial status.
        """
        torrent_title_path_component(indexer_result.title)
        torrent_hash = get_torrent_hash(torrent=indexer_result)
        answer = None

        try:
            self.api_client.auth_log_in()
            answer = self.api_client.torrents_add(
                category="MiraMedia",
                urls=indexer_result.download_url,
                save_path=torrent_title_path_component(indexer_result.title),
            )
        finally:
            self.api_client.auth_log_out()

        if answer != "Ok.":
            log.error(
                f"Failed to download torrent, API-Answer isn't 'Ok.'; API Answer: {answer}"
            )
            msg = f"Failed to download torrent, API-Answer isn't 'Ok.'; API Answer: {answer}"
            raise RuntimeError(msg)

        log.info(f"Successfully processed torrent: {indexer_result.title}")

        # Create and return torrent object
        torrent = Torrent(
            status=TorrentStatus.unknown,
            title=indexer_result.title,
            quality=indexer_result.quality,
            hash=torrent_hash,
        )

        # Get initial status from download client
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
        Remove a torrent from the download client.

        :param torrent: The torrent to remove.
        :param delete_data: Whether to delete the downloaded data.
        """
        log.info(f"Removing torrent: {torrent.title}")
        try:
            self.api_client.auth_log_in()
            self.api_client.torrents_delete(
                torrent_hashes=torrent.hash, delete_files=delete_data
            )
        finally:
            self.api_client.auth_log_out()

    def get_torrent_status(
        self, torrent: Torrent
    ) -> tuple[TorrentStatus, float, int, int, int]:
        """
        Get the status and progress of a specific torrent.

        :param torrent: The torrent to get the status of.
        :return: A tuple of (status, progress, num_peers, num_seeds, download_speed_bytes).
        """
        try:
            self.api_client.auth_log_in()
            info = self.api_client.torrents_info(torrent_hashes=torrent.hash)
        finally:
            self.api_client.auth_log_out()

        if not info:
            log.warning(f"No information found for torrent: {torrent.id}")
            return TorrentStatus.unknown, 0.0, 0, 0, 0

        torrent_info = info[0]
        state: str = torrent_info["state"]
        progress = round(torrent_info.get("progress", 0.0) * 100, 1)
        num_peers = int(torrent_info.get("num_leechs", 0) or 0)
        num_seeds = int(torrent_info.get("num_seeds", 0) or 0)
        dl_speed = int(torrent_info.get("dlspeed", 0) or 0)

        if state in self.DOWNLOADING_STATE:
            return TorrentStatus.downloading, progress, num_peers, num_seeds, dl_speed
        if state in self.FINISHED_STATE:
            return TorrentStatus.finished, 100.0, num_peers, num_seeds, 0
        if state in self.ERROR_STATE:
            return TorrentStatus.error, progress, num_peers, num_seeds, 0
        if state in self.UNKNOWN_STATE:
            return TorrentStatus.unknown, 0.0, 0, 0, 0
        return TorrentStatus.error, progress, num_peers, num_seeds, 0

    def pause_torrent(self, torrent: Torrent) -> None:
        """
        Pause a torrent download.

        :param torrent: The torrent to pause.
        """
        try:
            self.api_client.auth_log_in()
            self.api_client.torrents_pause(torrent_hashes=torrent.hash)
        finally:
            self.api_client.auth_log_out()

    def resume_torrent(self, torrent: Torrent) -> None:
        """
        Resume a torrent download.

        :param torrent: The torrent to resume.
        """
        try:
            self.api_client.auth_log_in()
            self.api_client.torrents_resume(torrent_hashes=torrent.hash)
        finally:
            self.api_client.auth_log_out()

    def get_torrent_files(self, torrent: Torrent) -> "list[TorrentFile] | None":
        """Fetch the file list from qBittorrent.

        Pre-metadata torrents return an empty list from ``torrents_files`` —
        treat that as "not yet known" so the post-add verifier keeps polling.
        """
        from miramedia.torrents.utils import TorrentFile

        try:
            self.api_client.auth_log_in()
            files = self.api_client.torrents_files(torrent_hash=torrent.hash)
        except Exception:
            log.debug(
                "qBittorrent torrents_files lookup failed for %s",
                torrent.hash,
                exc_info=True,
            )
            return None
        finally:
            try:
                self.api_client.auth_log_out()
            except Exception:  # noqa: S110 — best-effort, non-fatal
                pass
        if not files:
            return None
        out: list[TorrentFile] = []
        for f in files:
            name = f.get("name")
            if not name:
                continue
            try:
                size = int(f.get("size", 0) or 0)
            except (TypeError, ValueError):
                size = 0
            out.append(TorrentFile(path=Path(name), size=size))
        return out or None
