"""Shared base for ShowService and MovieService.

Design (plan 031): ``MediaService[TMedia, TMediaId]`` holds identical algorithms;
media-specific wiring is expressed as overridable hooks, not ``if is_show`` branches.

Base (shared verbatim):
  - ``_get_effective_preferences``
  - ``_library_parent`` / ``get_root_media_directory`` (via folder-name hooks)
  - preference setters (``_set_preferred_quality`` etc.)
  - ``_bridge_native_added_status`` (via imdb-index hooks)
  - ``annotate_search_results`` / ``_annotate_added_status`` (via identifier hooks)
  - ``_link_tree`` (static helper for move-library)
  - ``move_media_library`` (via set-library hook)
  - ``reconcile_orphaned_failed_imports`` (via orphan hooks)
  - ``_mark_torrent_import_failed`` (via bg-session + file hooks)
  - ``import_all_torrents`` (via bg-session + import hooks)
  - ``refresh_metadata_with_fallback`` (via metadata update hooks)
  - ``download_torrent`` (via media-type hook; show adds ``episode_target``)

Hooks (subclass overrides):
  - ``media_repository``, ``_configured_libraries``, ``_default_media_directory``
  - ``_media_library_name``, ``_warn_library_not_found``
  - ``_primary_folder_name``, ``_fallback_folder_names``
  - ``_update_media_attributes``, ``_media_id``
  - ``_existing_by_identifiers``, ``_native_imdb_index``, ``_provider_imdb_lookup``
  - ``_valid_library_names``, ``_unknown_library_message``, ``_set_media_library``
  - ``_move_library_log_label``
  - ``_get_orphaned_failed_files``, ``_resolve_media_file_path``
  - ``_update_media_file_import_status``, ``_reconcile_orphan_log_noun``
  - ``_bg_service``, ``_iter_torrent_import_files``, ``_stamp_file_import_failed``
  - ``_get_media_of_torrent``, ``_import_media_from_torrent``
  - ``_import_all_success_log``, ``_log_import_all_failure``
  - ``_invalidate_disk_scan_cache``
  - ``_refresh_update_metadata``, ``_search_provider``, ``_metadata_by_imdb``
  - ``_fetch_metadata``, ``_refresh_not_found_message``
  - ``_torrent_media_type``, ``_torrent_repository_kwargs``
  - ``download_torrent`` media-type / repository kwargs (show keeps episode_target locally)

Show-only (NOT in base): season/episode tree, specials, ``get_root_season_directory``,
``import_episode_from_file``, ``import_show_from_torrent``, episode targeting, etc.
"""

# ruff: noqa: ANN401, UP046, UP047 — generic base uses duck-typed hooks across show/movie repos

from __future__ import annotations

import asyncio
import logging
import shutil
from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Generic, Literal, Protocol, TypeVar, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import LibraryItem
from miramedia.exceptions import BadRequestError
from miramedia.file_status import ImportOutcome
from miramedia.imports.files import (
    DiskSpaceError,
    ImportConflictError,
    ensure_free_space,
    import_file,
)
from miramedia.indexers.schemas import IndexerQueryResultId
from miramedia.indexers.service import IndexerService
from miramedia.metadata.backends.generic import AbstractMetadataProvider
from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.movies.schemas import Movie, MovieId
from miramedia.notifications.service import NotificationService
from miramedia.shows.schemas import Show, ShowId
from miramedia.torrents.schemas import MediaType, Torrent, TorrentId, TorrentStatus
from miramedia.torrents.service import TorrentService

log = logging.getLogger(__name__)

MetadataProviderName = Literal["native", "tmdb", "tvdb"]


class MediaModelProtocol(Protocol):
    id: ShowId | MovieId
    name: str
    year: int | None
    library: str
    metadata_provider: str
    imdb_id: str | None
    preferred_quality: list[str] | None
    preferred_codec: list[str] | None


class MediaRepositoryProtocol(Protocol):
    db: AsyncSession


class MediaFileRowProtocol(Protocol):
    id: UUID
    import_status: ImportOutcome


class BgMediaSessionProtocol(Protocol):
    torrent_service: TorrentService

    async def reconcile_orphaned_failed_imports(self) -> int: ...


TMedia = TypeVar("TMedia", Show, Movie)
TMediaId = TypeVar("TMediaId", ShowId, MovieId)


def _metadata_provider_for(name: str) -> AbstractMetadataProvider:
    from miramedia.metadata.dependencies import get_metadata_provider

    return get_metadata_provider(cast(MetadataProviderName, name))


def _assign_search_result_id(
    copy: MetaDataProviderSearchResult, media_id: TMediaId | None
) -> None:
    if media_id is not None:
        copy.id = media_id


class MediaService(ABC, Generic[TMedia, TMediaId]):
    """Generic base for show/movie service twins."""

    torrent_service: TorrentService
    indexer_service: IndexerService
    notification_service: NotificationService

    # --- repository / config hooks ---

    @property
    @abstractmethod
    def media_repository(self) -> MediaRepositoryProtocol: ...

    @abstractmethod
    def _configured_libraries(self) -> list[LibraryItem]: ...

    @abstractmethod
    def _default_media_directory(self) -> Path: ...

    @abstractmethod
    def _media_library_name(self, media: TMedia) -> str | None: ...

    @abstractmethod
    def _warn_library_not_found(self, media: TMedia) -> None: ...

    @abstractmethod
    def _primary_folder_name(self, media: TMedia) -> str: ...

    @abstractmethod
    def _fallback_folder_names(self, media: TMedia) -> tuple[str, str]: ...

    @abstractmethod
    def _media_id(self, media: TMedia) -> TMediaId: ...

    @abstractmethod
    async def _update_media_attributes(
        self, media_id: TMediaId, **kwargs: Any
    ) -> tuple[TMedia, Any]: ...

    @abstractmethod
    async def _native_imdb_index(self) -> dict[str, TMediaId]: ...

    @abstractmethod
    def _provider_imdb_lookup(
        self, provider: AbstractMetadataProvider, external_id: str
    ) -> str | None: ...

    @abstractmethod
    async def _existing_by_identifiers(
        self, imdb_ids: list[str], provider_keys: list[tuple[str, str]]
    ) -> list[tuple[str | None, str, str, TMediaId]]: ...

    @abstractmethod
    def _valid_library_names(self) -> set[str]: ...

    @abstractmethod
    def _unknown_library_message(self, target_library: str) -> str: ...

    @abstractmethod
    async def _set_media_library(self, media_id: TMediaId, library: str) -> None: ...

    @abstractmethod
    def _move_library_log_label(self) -> str: ...

    @abstractmethod
    async def _get_orphaned_failed_files(self) -> list[MediaFileRowProtocol]: ...

    @abstractmethod
    async def _resolve_media_file_path(
        self, file_row: MediaFileRowProtocol
    ) -> Path | None: ...

    @abstractmethod
    async def _update_media_file_import_status(
        self, file_id: UUID, status: ImportOutcome, error: str | None
    ) -> None: ...

    @abstractmethod
    def _reconcile_orphan_log_noun(self) -> str: ...

    @abstractmethod
    def _bg_service(self) -> AbstractAsyncContextManager[BgMediaSessionProtocol]: ...

    @abstractmethod
    async def _iter_torrent_import_files(
        self, svc: BgMediaSessionProtocol, torrent_id: TorrentId
    ) -> list[MediaFileRowProtocol]: ...

    @abstractmethod
    async def _stamp_file_import_failed(
        self, svc: BgMediaSessionProtocol, file_id: UUID, error: str
    ) -> None: ...

    @abstractmethod
    async def _get_media_of_torrent(
        self, svc: BgMediaSessionProtocol, torrent: Torrent
    ) -> TMedia | None: ...

    @abstractmethod
    async def _import_media_from_torrent(
        self, svc: BgMediaSessionProtocol, torrent: Torrent, media: TMedia
    ) -> None: ...

    @abstractmethod
    def _import_all_success_log(self, count: int) -> None: ...

    @abstractmethod
    def _log_import_all_failure(
        self, torrent_title: str, media: TMedia | None, exc: BaseException
    ) -> None: ...

    @abstractmethod
    def _invalidate_disk_scan_cache(self) -> None: ...

    @abstractmethod
    async def _refresh_update_metadata(
        self,
        media: TMedia,
        metadata_provider: AbstractMetadataProvider,
        *,
        fresh_data: TMedia | None = None,
    ) -> None: ...

    @abstractmethod
    def _metadata_by_imdb(
        self, provider: AbstractMetadataProvider, imdb_id: str
    ) -> TMedia | None: ...

    @abstractmethod
    def _search_provider(
        self, provider: AbstractMetadataProvider, query: str
    ) -> list[MetaDataProviderSearchResult]: ...

    @abstractmethod
    def _fetch_metadata(
        self, provider: AbstractMetadataProvider, external_id: str
    ) -> TMedia | None: ...

    @abstractmethod
    def _refresh_not_found_message(self, media: TMedia) -> str: ...

    @abstractmethod
    def _torrent_media_type(self) -> MediaType: ...

    @abstractmethod
    def _torrent_repository_kwargs(self) -> dict[str, Any]: ...

    def _get_effective_preferences(
        self, media: TMedia
    ) -> tuple[list[str] | None, list[str] | None]:
        """Return per-title quality/codec preferences (tri-state list semantics)."""
        preferred_quality = media.preferred_quality
        preferred_codec = media.preferred_codec
        q = list(preferred_quality) if preferred_quality is not None else None
        c = list(preferred_codec) if preferred_codec is not None else None
        return q, c

    def _library_parent(self, media: TMedia) -> Path:
        library_name = self._media_library_name(media)
        if library_name and library_name != "Default":
            for library in self._configured_libraries():
                if library.name == library_name:
                    return Path(library.path)
            self._warn_library_not_found(media)
        return self._default_media_directory()

    def get_root_media_directory(self, media: TMedia, *, write: bool = False) -> Path:
        dir_name = self._primary_folder_name(media)
        parent = self._library_parent(media)
        new_path = parent / dir_name
        if write or new_path.exists():
            return new_path

        default_dir_name, old_dir_name = self._fallback_folder_names(media)
        for fallback_name in (default_dir_name, old_dir_name):
            if fallback_name == dir_name:
                continue
            old_path = parent / fallback_name
            if old_path.exists():
                return old_path
        return new_path

    async def _set_continuous_download(
        self, media: TMedia, continuous_download: bool | None
    ) -> TMedia:
        media, _ = await self._update_media_attributes(
            media_id=self._media_id(media), continuous_download=continuous_download
        )
        return media

    async def _set_preferred_quality(
        self, media: TMedia, preferred_quality: list[str] | None
    ) -> TMedia:
        media, _ = await self._update_media_attributes(
            media_id=self._media_id(media), preferred_quality=preferred_quality
        )
        return media

    async def _set_preferred_codec(
        self, media: TMedia, preferred_codec: list[str] | None
    ) -> TMedia:
        media, _ = await self._update_media_attributes(
            media_id=self._media_id(media), preferred_codec=preferred_codec
        )
        return media

    async def _set_subtitle_languages(
        self, media: TMedia, subtitle_languages: list[str] | None
    ) -> TMedia:
        media, _ = await self._update_media_attributes(
            media_id=self._media_id(media), subtitle_languages=subtitle_languages
        )
        return media

    async def _bridge_native_added_status(
        self, annotated: list[MetaDataProviderSearchResult]
    ) -> None:
        """Second-pass match for IMDb-keyed (native/scan) library rows."""

        unresolved = [c for c in annotated if not c.added and not c.imdb_id]
        if not unresolved:
            return
        native_index = await self._native_imdb_index()
        if not native_index:
            return

        async def _resolve(copy: MetaDataProviderSearchResult) -> None:
            try:
                provider = _metadata_provider_for(copy.metadata_provider)
            except Exception:
                return
            imdb = await asyncio.to_thread(
                self._provider_imdb_lookup, provider, copy.external_id
            )
            media_id = native_index.get(imdb) if imdb else None
            if media_id is not None:
                copy.added = True
                _assign_search_result_id(copy, media_id)

        await asyncio.gather(*(_resolve(c) for c in unresolved))

    async def annotate_search_results(
        self, results: list[MetaDataProviderSearchResult]
    ) -> list[MetaDataProviderSearchResult]:
        """Public re-annotation entrypoint for cached search results."""
        return await self._annotate_added_status(results)

    async def _annotate_added_status(
        self, results: list[MetaDataProviderSearchResult]
    ) -> list[MetaDataProviderSearchResult]:
        """Mark which search results are already tracked in the DB."""
        imdb_ids = [str(r.imdb_id) for r in results if r.imdb_id]
        provider_keys = [(r.external_id, r.metadata_provider) for r in results]
        rows = await self._existing_by_identifiers(imdb_ids, provider_keys)
        by_imdb = {imdb: mid for imdb, _ext, _prov, mid in rows if imdb is not None}
        by_external = {ext: mid for _imdb, ext, _prov, mid in rows}
        by_provider = {(ext, prov): mid for _imdb, ext, prov, mid in rows}
        annotated: list[MetaDataProviderSearchResult] = []
        for result in results:
            media_id = None
            if result.imdb_id:
                imdb = str(result.imdb_id)
                media_id = by_imdb.get(imdb) or by_external.get(imdb)
            if media_id is None:
                media_id = by_provider.get(
                    (result.external_id, result.metadata_provider)
                )
            copy = result.model_copy()
            copy.added = media_id is not None
            _assign_search_result_id(copy, media_id)
            annotated.append(copy)

        await self._bridge_native_added_status(annotated)
        return annotated

    @staticmethod
    def _link_tree(src_root: Path, dst_root: Path) -> tuple[int, list[str]]:
        local_moved = 0
        local_errors: list[str] = []
        for source in src_root.rglob("*"):
            if not source.is_file():
                continue
            rel = source.relative_to(src_root)
            target = dst_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                ensure_free_space(target.parent, source.stat().st_size)
                import_file(target_file=target, source_file=source)
                local_moved += 1
            except (DiskSpaceError, ImportConflictError, OSError) as exc:
                local_errors.append(f"{rel}: {exc}")
        return local_moved, local_errors

    async def move_media_library(
        self,
        media: TMedia,
        target_library: str,
        *,
        delete_source: bool = True,
    ) -> dict:
        """Re-home a title's directory under a different configured library."""
        if target_library not in self._valid_library_names():
            raise ValueError(self._unknown_library_message(target_library))

        if self._media_library_name(media) == target_library:
            return {"moved": 0, "skipped": True, "reason": "already in target library"}

        old_root = self.get_root_media_directory(media)
        original_library = media.library
        media.library = target_library
        try:
            new_root = self.get_root_media_directory(media)
        finally:
            media.library = original_library

        if not old_root.exists():
            await self._set_media_library(self._media_id(media), target_library)
            return {"moved": 0, "skipped": True, "reason": "source directory missing"}

        new_root.mkdir(parents=True, exist_ok=True)

        from miramedia.database import release_session_before_external_io

        await release_session_before_external_io(self.media_repository.db)
        moved, errors = await asyncio.to_thread(self._link_tree, old_root, new_root)

        if errors:
            log.warning(
                "%s partial: %d errors", self._move_library_log_label(), len(errors)
            )
            return {
                "moved": moved,
                "errors": errors,
                "old_root": str(old_root),
                "new_root": str(new_root),
                "library_changed": False,
            }

        await self._set_media_library(self._media_id(media), target_library)

        if delete_source:
            try:
                await asyncio.to_thread(shutil.rmtree, old_root)
            except OSError as exc:
                errors.append(f"remove source: {exc}")

        return {
            "moved": moved,
            "errors": errors,
            "old_root": str(old_root),
            "new_root": str(new_root),
            "library_changed": True,
        }

    async def reconcile_orphaned_failed_imports(self) -> int:
        """Heal ghost failed file rows whose library file is already on disk."""
        orphans = await self._get_orphaned_failed_files()
        healed = 0
        for file_row in orphans:
            if await self._resolve_media_file_path(file_row) is None:
                continue
            await self._update_media_file_import_status(
                file_id=file_row.id, status=ImportOutcome.imported, error=None
            )
            healed += 1
        if healed:
            log.info(
                "Reconciled %d ghost failed %s import(s) to imported",
                healed,
                self._reconcile_orphan_log_noun(),
            )
        return healed

    async def _mark_torrent_import_failed(
        self, torrent_id: TorrentId, error: str
    ) -> None:
        """Stamp every file of ``torrent_id`` failed_io in a fresh session."""
        try:
            async with self._bg_service() as svc:
                files = await self._iter_torrent_import_files(svc, torrent_id)
                for f in files:
                    if f.import_status == ImportOutcome.imported:
                        continue
                    await self._stamp_file_import_failed(svc, f.id, error)
        except Exception:
            log.exception("Failed to mark torrent %s import failed", torrent_id)

    async def import_all_torrents(self) -> None:
        """Iterate ready torrents and import each in a fresh bg session."""
        async with self._bg_service() as svc:
            await svc.reconcile_orphaned_failed_imports()

        torrents = await self.torrent_service.get_all_torrents()
        finished = [t for t in torrents if t.status == TorrentStatus.finished]
        ready_ids: list[tuple[TorrentId, str]] = []
        if finished:
            imported_map = await self.torrent_service.bulk_check_torrents_imported(
                [t.id for t in finished]
            )
            for t in finished:
                if imported_map.get(t.id, False):
                    continue
                if not await self.torrent_service.is_due_for_retry(t):
                    continue
                ready_ids.append((t.id, t.title))

        imported_count = 0
        for torrent_id, torrent_title in ready_ids:
            media = None
            try:
                async with self._bg_service() as fresh_svc:
                    t = await fresh_svc.torrent_service.torrent_repository.get_torrent_by_id(
                        torrent_id=torrent_id
                    )
                    if t is None:
                        continue
                    media = await self._get_media_of_torrent(fresh_svc, t)
                    if media is None:
                        continue
                    await self._import_media_from_torrent(fresh_svc, t, media)
                imported_count += 1
            except Exception as exc:
                self._log_import_all_failure(torrent_title, media, exc)
                await self._mark_torrent_import_failed(
                    torrent_id, "Import raised; see logs."
                )
        if imported_count:
            self._invalidate_disk_scan_cache()
            self._import_all_success_log(imported_count)

    async def refresh_metadata_with_fallback(self, media: TMedia) -> None:
        """Refresh metadata using the best enabled provider."""
        from miramedia.database import release_session_before_external_io
        from miramedia.metadata.dependencies import get_all_enabled_providers

        try:
            provider = _metadata_provider_for(media.metadata_provider)
        except Exception:
            provider = None

        if provider is not None:
            await self._refresh_update_metadata(media, provider)
            return

        imdb_id = media.imdb_id
        if imdb_id:
            for p in get_all_enabled_providers():
                await release_session_before_external_io(self.media_repository.db)
                fresh = await asyncio.to_thread(self._metadata_by_imdb, p, imdb_id)
                if fresh:
                    await self._refresh_update_metadata(media, p, fresh_data=fresh)
                    return

        target_name = media.name.lower().strip()
        year = media.year
        for p in get_all_enabled_providers():
            try:
                await release_session_before_external_io(self.media_repository.db)
                results = await asyncio.to_thread(self._search_provider, p, media.name)
            except Exception:  # noqa: S112
                continue
            for result in results:
                if result.name.lower().strip() != target_name:
                    continue
                if year and result.year and result.year != year:
                    continue
                await release_session_before_external_io(self.media_repository.db)
                fresh = await asyncio.to_thread(
                    self._fetch_metadata, p, result.external_id
                )
                if fresh:
                    await self._refresh_update_metadata(media, p, fresh_data=fresh)
                    return

        raise BadRequestError(self._refresh_not_found_message(media))

    async def _download_and_link_torrent(
        self,
        public_indexer_result_id: IndexerQueryResultId,
        media_id: TMediaId,
        override_variant: str = "",
        **link_kwargs: Any,
    ) -> Torrent:
        indexer_result = await self.indexer_service.get_result(
            result_id=public_indexer_result_id
        )
        return await self.torrent_service.download_and_link(
            indexer_result=indexer_result,
            media_type=self._torrent_media_type(),
            media_id=media_id,
            variant=override_variant,
            **self._torrent_repository_kwargs(),
            **link_kwargs,
        )
