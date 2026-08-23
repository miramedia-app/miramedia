from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Self, cast
from uuid import UUID

import libtorrent

from miramedia.config import MiraMediaConfig
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.backends.abstract_download_client import (
    AbstractDownloadClient,
)
from miramedia.torrents.inspection import get_torrent_hash
from miramedia.torrents.paths import (
    _application_control_dir_paths,
    _configured_torrent_roots,
    _is_safe_deletion_target,
    exact_save_dirs_for_title,
    torrent_dir_under_root,
    torrent_sidecar_under_root,
)
from miramedia.torrents.schemas import Torrent, TorrentStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from miramedia.torrents.inspection import TorrentFile

log = logging.getLogger(__name__)

_RESUME_RECONCILE_IN_CHUNK = 500


def _chunked[T](items: list[T], size: int) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _resume_hashes_to_drop(
    resume_hashes: list[str],
    torrent_id_by_hash: dict[str, UUID],
    states_by_torrent_id: dict[UUID, list],
) -> set[str]:
    """Return resume info-hashes that should be removed from libtorrent + disk."""
    from miramedia.file_status import ImportOutcome

    to_drop: set[str] = set()
    for hash_str in resume_hashes:
        torrent_id = torrent_id_by_hash.get(hash_str)
        if torrent_id is None:
            to_drop.add(hash_str)
            continue
        states = states_by_torrent_id.get(torrent_id, [])
        if states and all(s == ImportOutcome.imported for s in states):
            to_drop.add(hash_str)
    return to_drop


class NativeDownloadClient(AbstractDownloadClient):
    """
    A native BitTorrent download client using libtorrent.
    First-class alternative to external clients like qBittorrent or Transmission.
    """

    name = "native"

    _instance: NativeDownloadClient | None = None
    _lock = threading.Lock()

    def __new__(cls) -> Self:
        with cls._lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instance = instance
            return cast("Self", cls._instance)

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        self.config = MiraMediaConfig().torrents.native
        self._session = libtorrent.session()
        # Hashes already moved from incomplete → completed, to avoid re-triggering move_storage
        self._moved_hashes: set[str] = set()

        settings = {
            "listen_interfaces": f"0.0.0.0:{self.config.listen_port_start}",
            "alert_mask": libtorrent.alert.category_t.status_notification
            | libtorrent.alert.category_t.error_notification,
            "enable_dht": True,
            "enable_lsd": True,
        }

        if self.config.max_download_rate > 0:
            settings["download_rate_limit"] = self.config.max_download_rate * 1024
        if self.config.max_upload_rate > 0:
            settings["upload_rate_limit"] = self.config.max_upload_rate * 1024

        self._session.apply_settings(settings)

        # Resume data lives next to the completed torrent directory.
        self._resume_data_dir = (
            Path(MiraMediaConfig().misc.torrent_directory) / ".resume_data"
        )
        self._resume_data_dir.mkdir(parents=True, exist_ok=True)

        self._load_resume_data()

        log.info(
            "Native torrent client initialized, listening on port %s",
            self.config.listen_port_start,
        )

    def _resolve_paths(self, title: str) -> tuple[Path, Path]:
        """Return (initial_save_path, completed_path) for a given torrent title.

        When ``incomplete_torrent_path`` is set, downloads start there and are
        moved to ``torrent_directory`` on finish. Otherwise the torrent saves
        directly to ``torrent_directory`` and no move is performed.
        """
        misc = MiraMediaConfig().misc
        completed = torrent_dir_under_root(misc.effective_completed_path, title)
        incomplete_root = (misc.incomplete_torrent_path or "").strip()
        if incomplete_root:
            return torrent_dir_under_root(Path(incomplete_root), title), completed
        return completed, completed

    async def _load_resume_reconcile_context(
        self,
        db: AsyncSession,
        resume_hashes: list[str],
    ) -> tuple[dict[str, UUID], dict[UUID, list]]:
        """Batch-load torrent rows and linked import states for resume hashes."""
        from sqlalchemy import select

        from miramedia.movies.models import MovieFile
        from miramedia.shows.models import EpisodeFile
        from miramedia.torrents.models import Torrent as TorrentModel

        torrent_id_by_hash: dict[str, UUID] = {}
        states_by_torrent_id: dict[UUID, list] = defaultdict(list)

        if not resume_hashes:
            return torrent_id_by_hash, dict(states_by_torrent_id)

        unique_hashes = list(dict.fromkeys(resume_hashes))
        for chunk in _chunked(unique_hashes, _RESUME_RECONCILE_IN_CHUNK):
            rows = (
                await db.execute(
                    select(TorrentModel.id, TorrentModel.hash).where(
                        TorrentModel.hash.in_(chunk)
                    )
                )
            ).all()
            torrent_id_by_hash.update(
                {hash_str: torrent_id for torrent_id, hash_str in rows}
            )

        torrent_ids = list(torrent_id_by_hash.values())
        if not torrent_ids:
            return torrent_id_by_hash, dict(states_by_torrent_id)

        for chunk in _chunked(torrent_ids, _RESUME_RECONCILE_IN_CHUNK):
            ep_rows = (
                await db.execute(
                    select(EpisodeFile.torrent_id, EpisodeFile.import_status).where(
                        EpisodeFile.torrent_id.in_(chunk)
                    )
                )
            ).all()
            for torrent_id, status in ep_rows:
                states_by_torrent_id[torrent_id].append(status)

        for chunk in _chunked(torrent_ids, _RESUME_RECONCILE_IN_CHUNK):
            mv_rows = (
                await db.execute(
                    select(MovieFile.torrent_id, MovieFile.import_status).where(
                        MovieFile.torrent_id.in_(chunk)
                    )
                )
            ).all()
            for torrent_id, status in mv_rows:
                states_by_torrent_id[torrent_id].append(status)

        return torrent_id_by_hash, dict(states_by_torrent_id)

    def _snapshot_handle_map(self) -> dict[str, libtorrent.torrent_handle]:
        """Build a single info-hash → handle map from one session enumeration."""
        return {
            str(handle.info_hash()): handle for handle in self._session.get_torrents()
        }

    async def reconcile_resume_data(self) -> int:
        """Drop fastresume + libtorrent state for finished/missing torrents.

        Catches three drift cases that ``cleanup_after_import`` doesn't
        cover: manual-map imports, auto-import-on-scan, and legacy torrents
        added before per-import cleanup existed. For each ``*.fastresume``
        file, if the DB has no matching torrent OR every linked file is
        already imported, remove the torrent from libtorrent (keeping the
        media files on disk — they were imported via hardlink/move) and
        delete the resume file.
        """
        from miramedia.database import SessionLocal, release_session_before_external_io

        reclaimed = 0
        files = list(self._resume_data_dir.glob("*.fastresume"))
        if not files:
            return 0

        resume_by_hash = {resume_file.stem: resume_file for resume_file in files}
        resume_hashes = list(resume_by_hash.keys())

        try:
            async with SessionLocal() as db:
                (
                    torrent_id_by_hash,
                    states_by_torrent_id,
                ) = await self._load_resume_reconcile_context(db, resume_hashes)
                to_drop = _resume_hashes_to_drop(
                    resume_hashes,
                    torrent_id_by_hash,
                    states_by_torrent_id,
                )
                await release_session_before_external_io(db)
        except Exception:
            log.exception("Resume reconcile: failed to load DB context; skipping sweep")
            return 0

        handle_map = self._snapshot_handle_map()

        for hash_str in to_drop:
            resume_file = resume_by_hash[hash_str]
            try:
                handle = handle_map.get(hash_str)
                if handle is not None:
                    try:
                        self._session.remove_torrent(handle)
                    except Exception:
                        log.debug(
                            "Could not remove %s from libtorrent session",
                            hash_str,
                            exc_info=True,
                        )

                try:
                    resume_file.unlink()
                except Exception:
                    log.debug(
                        "Could not delete resume file %s",
                        resume_file,
                        exc_info=True,
                    )
                    continue

                reclaimed += 1
            except Exception:
                log.exception("Failed to reconcile resume data for %s", hash_str)

        log.debug(
            "Native torrent client: resume-data reconcile checked %d "
            "file(s), reaped %d (imported / orphaned)",
            len(files),
            reclaimed,
        )
        return reclaimed

    def _load_resume_data(self) -> None:
        """Load resume data for previously added torrents.

        One ``.fastresume`` file per torrent libtorrent still tracks — either
        actively downloading or seeding. Files for imported torrents are
        deleted by ``remove_torrent`` when ``cleanup_after_import=True``.
        """
        loaded = 0
        failed = 0
        for resume_file in self._resume_data_dir.glob("*.fastresume"):
            try:
                resume_data = resume_file.read_bytes()
                params = libtorrent.read_resume_data(resume_data)
                # Resume data is serialized at shutdown *after* the session is
                # paused, so the saved torrent flags carry ``paused``. Clear it
                # (and ``auto_managed``, which we manage explicitly) so a
                # reloaded torrent actually resumes downloading instead of
                # sitting idle forever.
                params.flags &= ~libtorrent.torrent_flags.auto_managed
                params.flags &= ~libtorrent.torrent_flags.paused
                self._session.async_add_torrent(params)
                loaded += 1
            except Exception:
                failed += 1
                log.exception("Failed to load resume data from %s", resume_file)
        if loaded or failed:
            log.info(
                "Native torrent client: loaded %d resume file(s)%s",
                loaded,
                f" ({failed} failed)" if failed else "",
            )

    def save_resume_data(self) -> None:
        """Save resume data for all active torrents to disk.

        We must wait for one ``save_resume_data_alert`` (or its *failed*
        counterpart) per handle we asked. The previous implementation broke
        out of the alert loop on the first *empty* ``pop_alerts()`` batch —
        but libtorrent generates the alert asynchronously, so the first poll
        is almost always empty and we'd exit having written zero files. The
        net effect was that on restart there was no resume data to load, the
        torrents were never re-added to the session, and every later
        pause/resume/remove logged "Torrent not found". Track the pending set
        and only stop once it drains or the deadline elapses.
        """
        pending: set[str] = set()
        for handle in self._session.get_torrents():
            if not handle.is_valid():
                continue
            try:
                pending.add(str(handle.info_hash()))
                handle.save_resume_data(libtorrent.save_resume_flags_t.save_info_dict)
            except Exception:
                log.exception("Failed to request resume data save")

        if not pending:
            return

        # Process alerts to actually write the data. Keep polling until every
        # requested handle has reported back (success or failure) or we hit
        # the deadline — do NOT break on an empty batch.
        deadline = time.monotonic() + 10
        saved = 0
        while pending and time.monotonic() < deadline:
            alerts = self._session.pop_alerts()
            for alert in alerts:
                if isinstance(alert, libtorrent.save_resume_data_alert):
                    info_hash = str(alert.handle.info_hash())
                    resume_file = self._resume_data_dir / f"{info_hash}.fastresume"
                    resume_file.write_bytes(
                        libtorrent.write_resume_data_buf(alert.params)
                    )
                    pending.discard(info_hash)
                    saved += 1
                    log.debug("Saved resume data for %s", info_hash)
                elif isinstance(alert, libtorrent.save_resume_data_failed_alert):
                    # Torrents without metadata yet can't produce resume data;
                    # drop them from the wait set so we don't block the full
                    # deadline on them.
                    info_hash = str(alert.handle.info_hash())
                    pending.discard(info_hash)
                    log.debug(
                        "Resume data save failed for %s: %s",
                        info_hash,
                        getattr(alert, "message", lambda: "")(),
                    )
            if pending:
                time.sleep(0.1)
        if pending:
            log.warning(
                "Resume data save timed out for %d torrent(s); they may "
                "re-check on next start",
                len(pending),
            )
        log.info("Native torrent client: saved %d resume file(s)", saved)

    def _get_handle_by_hash(
        self, torrent_hash: str
    ) -> libtorrent.torrent_handle | None:
        """Find a torrent handle by its info hash."""
        for handle in self._session.get_torrents():
            if str(handle.info_hash()) == torrent_hash:
                return handle
        return None

    def download_torrent(self, indexer_result: IndexerQueryResult) -> Torrent:
        initial_path, _completed_path = self._resolve_paths(indexer_result.title)
        torrent_hash = get_torrent_hash(torrent=indexer_result)
        save_path = str(initial_path)
        initial_path.mkdir(parents=True, exist_ok=True)

        params = libtorrent.add_torrent_params()
        params.save_path = save_path

        if indexer_result.download_url.startswith("magnet:"):
            params = libtorrent.parse_magnet_uri(indexer_result.download_url)
            params.save_path = save_path
        else:
            # For .torrent file URLs, the file should already be downloaded by get_torrent_hash
            torrent_file_path = torrent_sidecar_under_root(
                MiraMediaConfig().misc.effective_completed_path,
                indexer_result.title,
            )
            if torrent_file_path.exists():
                info = libtorrent.torrent_info(str(torrent_file_path))
                params.ti = info
            else:
                # Fall back to magnet-style add using the URL
                params = libtorrent.parse_magnet_uri(indexer_result.download_url)
                params.save_path = save_path

        # Disable auto-management so libtorrent doesn't override our pause/resume
        params.flags &= ~libtorrent.torrent_flags.auto_managed
        # Guard against re-adding an infohash already in the session — libtorrent
        # raises on a duplicate add, which would abort the download flow. A
        # continuous-download re-trigger or a manual re-add of an in-flight
        # torrent hits this; reuse the existing handle instead.
        existing = self._get_handle_by_hash(torrent_hash)
        if existing is not None and existing.is_valid():
            log.info(
                "Torrent already in native client, reusing handle: %s",
                indexer_result.title,
            )
        else:
            self._session.add_torrent(params)
            log.info("Added torrent to native client: %s", indexer_result.title)

        torrent = Torrent(
            status=TorrentStatus.downloading,
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
        handle = self._get_handle_by_hash(torrent.hash)
        if handle is None:
            log.warning("Torrent not found in native client: %s", torrent.hash)
            resume_file = self._resume_data_dir / f"{torrent.hash}.fastresume"
            if resume_file.exists():
                resume_file.unlink()
            return

        if delete_data:
            self._session.remove_torrent(handle, libtorrent.options_t.delete_files)
        else:
            self._session.remove_torrent(handle)

        resume_file = self._resume_data_dir / f"{torrent.hash}.fastresume"
        resume_file.unlink(missing_ok=True)

        if delete_data:
            self._try_rmdir_empty_save_dirs(torrent)

        log.info("Removed torrent from native client: %s", torrent.title)

    def _try_rmdir_empty_save_dirs(self, torrent: Torrent) -> None:
        """Remove only empty exact save dirs after libtorrent deleted payload files.

        Title/hash alone does not prove payload ownership — never ``rmtree()``
        torrent data directories. Without a live handle libtorrent already
        skipped payload deletion, so this is a no-op on orphaned rows.
        """
        cfg = MiraMediaConfig().misc
        roots = _configured_torrent_roots(cfg)
        forbidden = _application_control_dir_paths(cfg)
        for path in exact_save_dirs_for_title(torrent.title):
            if not _is_safe_deletion_target(path, roots, forbidden=forbidden):
                log.warning(
                    "Refusing to remove torrent save dir outside configured roots: %s",
                    path,
                )
                continue
            try:
                if not path.is_dir():
                    continue
                if any(path.iterdir()):
                    log.debug("Skipping non-empty torrent save dir %s", path)
                    continue
                path.rmdir()
                log.info("Removed empty torrent save dir %s", path)
            except OSError:
                log.debug(
                    "Could not remove torrent save dir %s",
                    path,
                    exc_info=True,
                )

    def _status_not_found(self) -> tuple[TorrentStatus, float, int, int, int]:
        return TorrentStatus.unknown, 0.0, 0, 0, 0

    def _status_from_handle(
        self, handle: libtorrent.torrent_handle, torrent: Torrent
    ) -> tuple[TorrentStatus, float, int, int, int]:
        status = handle.status()
        state = status.state
        progress = round(status.progress * 100, 1)
        num_peers = status.num_peers
        num_seeds = status.num_seeds
        dl_speed = int(status.download_rate)

        if state in (
            libtorrent.torrent_status.states.seeding,
            libtorrent.torrent_status.states.finished,
        ):
            self._maybe_move_to_completed(handle, torrent)
            return TorrentStatus.finished, 100.0, num_peers, num_seeds, 0

        if state in (
            libtorrent.torrent_status.states.downloading,
            libtorrent.torrent_status.states.downloading_metadata,
            libtorrent.torrent_status.states.checking_files,
            libtorrent.torrent_status.states.checking_resume_data,
            libtorrent.torrent_status.states.allocating,
        ):
            return TorrentStatus.downloading, progress, num_peers, num_seeds, dl_speed

        if status.errc.value() != 0:
            return TorrentStatus.error, progress, num_peers, num_seeds, 0

        return TorrentStatus.unknown, progress, num_peers, num_seeds, 0

    def get_torrent_status(
        self, torrent: Torrent
    ) -> tuple[TorrentStatus, float, int, int, int]:
        handle = self._get_handle_by_hash(torrent.hash)
        if handle is None:
            return self._status_not_found()
        return self._status_from_handle(handle, torrent)

    def get_torrent_statuses_bulk(
        self, torrents: list[Torrent]
    ) -> dict[str, tuple[TorrentStatus, float, int, int, int]]:
        """One session enumeration, then status per requested info hash."""
        if not torrents:
            return {}
        handle_map = self._snapshot_handle_map()
        not_found = self._status_not_found()
        return {
            torrent.hash: (
                self._status_from_handle(handle_map[torrent.hash], torrent)
                if torrent.hash in handle_map
                else not_found
            )
            for torrent in torrents
        }

    def _maybe_move_to_completed(
        self, handle: libtorrent.torrent_handle, torrent: Torrent
    ) -> None:
        """Once-per-torrent move from incomplete dir to torrent_directory.

        Idempotent: tracks already-moved hashes so repeated polls don't re-trigger
        move_storage. No-op when no incomplete path is configured (initial path
        already equals completed path).
        """
        if torrent.hash in self._moved_hashes:
            return
        initial_path, completed_path = self._resolve_paths(torrent.title)
        if initial_path == completed_path:
            self._moved_hashes.add(torrent.hash)
            return
        try:
            completed_path.mkdir(parents=True, exist_ok=True)
            handle.move_storage(
                str(completed_path), libtorrent.move_flags_t.always_replace_files
            )
            self._moved_hashes.add(torrent.hash)
            log.info(
                "Native client: moving '%s' from '%s' to '%s'",
                torrent.title,
                initial_path,
                completed_path,
            )
        except Exception:
            log.exception("Failed to move torrent '%s' to completed dir", torrent.title)

    def pause_torrent(self, torrent: Torrent) -> None:
        handle = self._get_handle_by_hash(torrent.hash)
        if handle is None:
            log.warning("Torrent not found for pause: %s", torrent.hash)
            return
        handle.pause()
        log.debug("Paused torrent: %s", torrent.title)

    def resume_torrent(self, torrent: Torrent) -> None:
        handle = self._get_handle_by_hash(torrent.hash)
        if handle is None:
            log.warning("Torrent not found for resume: %s", torrent.hash)
            return
        handle.resume()
        log.debug("Resumed torrent: %s", torrent.title)

    def get_torrent_files(self, torrent: Torrent) -> list[TorrentFile] | None:
        from miramedia.torrents.inspection import TorrentFile

        handle = self._get_handle_by_hash(torrent.hash)
        if handle is None:
            return None
        try:
            status = handle.status()
        except Exception:
            return None
        if not getattr(status, "has_metadata", False):
            return None
        try:
            ti = handle.torrent_file()
        except Exception:
            return None
        if ti is None:
            return None
        try:
            storage = ti.files()
            return [
                TorrentFile(
                    path=Path(storage.file_path(i)),
                    size=int(storage.file_size(i) or 0),
                )
                for i in range(storage.num_files())
            ]
        except Exception:
            log.exception(
                "Failed to enumerate files for native torrent %s", torrent.hash
            )
            return None

    def shutdown(self) -> None:
        """Gracefully shut down the session, saving resume data.

        Pause the session first so in-flight piece writes quiesce before we
        snapshot resume data — otherwise libtorrent can serialize a state
        that's mid-write and the next start re-checks the whole torrent.
        """
        log.info("Shutting down native torrent client...")
        try:
            self._session.pause()
            # Brief settle so libtorrent flushes piece writes triggered by
            # the pause before we serialize resume data.
            time.sleep(0.5)
        except Exception:
            log.exception("session.pause() failed during shutdown (non-fatal)")
        self.save_resume_data()
        log.info("Native torrent client shut down complete.")
