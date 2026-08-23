"""Import, torrent, metadata, and media-request scheduler task implementations."""

from __future__ import annotations

import asyncio
import logging

from miramedia.config import MiraMediaConfig
from miramedia.metadata.dependencies import resolve_metadata_provider
from miramedia.requests.dependencies import build_seerr_client
from miramedia.requests.schemas import RequestStatus
from miramedia.requests.sync import SeerrSyncService
from miramedia.scheduler_tasks import dispatch as dispatch_tasks
from miramedia.scheduler_tasks.locks import import_sweep_lock

log = logging.getLogger(__name__)


async def import_all_movie_torrents() -> None:
    lock = import_sweep_lock("movie")
    if lock.locked():
        log.debug("Movie import sweep already running; skipping overlapping tick")
        return
    async with lock:
        from miramedia.background_services import bg_movie_service

        async with bg_movie_service() as movie_service:
            await movie_service.import_all_torrents()
    from miramedia.events.bus import Event, get_event_bus

    get_event_bus().publish(Event(type="torrent.refresh"))


async def import_all_show_torrents() -> None:
    lock = import_sweep_lock("show")
    if lock.locked():
        log.debug("Show import sweep already running; skipping overlapping tick")
        return
    async with lock:
        from miramedia.background_services import bg_show_service

        async with bg_show_service() as show_service:
            await show_service.import_all_torrents()
    from miramedia.events.bus import Event, get_event_bus

    get_event_bus().publish(Event(type="torrent.refresh"))


async def detect_finished_downloads() -> None:
    """Promptly notice downloads that just finished and trigger their import."""
    from miramedia.background_services import bg_torrent_service
    from miramedia.database import release_session_before_external_io
    from miramedia.torrents.schemas import TorrentStatus

    async with bg_torrent_service() as svc:
        torrents = await svc.torrent_repository.get_active_torrents()
        if not torrents:
            return
        await release_session_before_external_io(svc.torrent_repository.db)
        live = await svc._fetch_live_torrent_statuses(torrents)
        newly_finished = any(t.status == TorrentStatus.finished for t in live)

    if newly_finished:
        enqueue = dispatch_tasks.enqueue_import_all
        if enqueue is None:
            log.warning(
                "Finished downloads detected but import-all dispatch is unset; "
                "skipping enqueue (scheduler not registered?)"
            )
            return
        await enqueue()


async def update_all_movies_metadata() -> None:
    from miramedia.movies.service import (
        _auto_download_missing_movies_impl,
        _update_all_movies_metadata_impl,
    )

    await _update_all_movies_metadata_impl()
    await _auto_download_missing_movies_impl()


async def update_all_shows_metadata() -> None:
    from miramedia.shows.service import (
        _auto_download_missing_episodes_impl,
        _update_all_shows_metadata_impl,
    )

    await _update_all_shows_metadata_impl()
    await _auto_download_missing_episodes_impl()


async def auto_download_missing_episodes() -> None:
    from miramedia.shows.service import _auto_download_missing_episodes_impl

    log.info("Running auto-download for shows with continuous download enabled")
    await _auto_download_missing_episodes_impl()


async def auto_download_missing_movies() -> None:
    from miramedia.movies.service import _auto_download_missing_movies_impl

    log.info("Running auto-download for movies with continuous download enabled")
    await _auto_download_missing_movies_impl()


async def add_show(
    external_id: str,
    metadata_provider_name: str,
    language: str | None = None,
) -> None:
    """Background add-show: fetches metadata, persists, triggers auto-download."""
    from miramedia.background_services import bg_show_service
    from miramedia.exceptions import MediaAlreadyExistsError
    from miramedia.metadata.dependencies import get_metadata_provider
    from miramedia.shows.service import _try_auto_download_show_id_impl

    saved_id = None
    should_auto_download = False
    try:
        provider = get_metadata_provider(metadata_provider_name)
        async with bg_show_service() as show_service:
            saved = await show_service.add_show(
                external_id=external_id,
                metadata_provider=provider,
                language=language,
            )
            saved_id = saved.id
            global_cd = MiraMediaConfig().misc.continuous_download
            effective_cd = (
                saved.continuous_download
                if saved.continuous_download is not None
                else global_cd
            )
            should_auto_download = bool(effective_cd) and not saved.skipped
    except MediaAlreadyExistsError:
        log.info(
            "Show %s already exists in library; add was a no-op",
            external_id,
        )
    except Exception as exc:
        log.exception(
            "Failed to add show %s via %s", external_id, metadata_provider_name
        )
        notify_add_failure("show", external_id, exc)
        return

    if saved_id is not None and should_auto_download:
        await _try_auto_download_show_id_impl(saved_id)


async def add_movie(
    external_id: str,
    metadata_provider_name: str,
    language: str | None = None,
) -> None:
    """Background add-movie. Mirror of add_show."""
    from miramedia.background_services import bg_movie_service
    from miramedia.exceptions import ConflictError
    from miramedia.metadata.dependencies import get_metadata_provider
    from miramedia.movies.service import _try_auto_download_movie_id_impl

    saved_id = None
    should_auto_download = False
    try:
        provider = get_metadata_provider(metadata_provider_name)
        async with bg_movie_service() as movie_service:
            saved = await movie_service.add_movie(
                external_id=external_id,
                metadata_provider=provider,
                language=language,
            )
            saved_id = saved.id
            global_cd = MiraMediaConfig().misc.continuous_download
            effective_cd = (
                saved.continuous_download
                if saved.continuous_download is not None
                else global_cd
            )
            should_auto_download = bool(effective_cd) and not saved.skipped
    except ConflictError:
        log.info(
            "Movie %s already exists in library; add was a no-op",
            external_id,
        )
    except Exception as exc:
        log.exception(
            "Failed to add movie %s via %s", external_id, metadata_provider_name
        )
        notify_add_failure("movie", external_id, exc)
        return

    if saved_id is not None and should_auto_download:
        await _try_auto_download_movie_id_impl(saved_id)


def notify_add_failure(kind: str, external_id: str, exc: Exception) -> None:
    """Surface a background add failure through the in-app notification system."""
    try:
        from miramedia.notifications.manager import notification_manager

        notification_manager.send_notification(
            title=f"Could not add {kind}",
            message=f"{external_id}: {exc}",
        )
    except Exception:
        log.debug("Failed to surface add-failure notification", exc_info=True)


async def check_for_updates() -> None:
    cfg = MiraMediaConfig().updates
    if not cfg.enabled:
        return
    from miramedia.updates.service import UpdateService

    svc = UpdateService()
    info = await asyncio.to_thread(svc.get_update_info, True)
    if info.update_available:
        log.info(
            "update available: %s -> %s (%s)",
            info.current_version,
            info.latest_version,
            info.release_url,
        )
        if cfg.notify_on_new_version:
            notify_update_available(info)


def notify_update_available(info) -> None:  # noqa: ANN001
    try:
        from miramedia.notifications.manager import notification_manager

        title = "MiraMedia update available"
        message = (
            f"New version {info.latest_version} available "
            f"(current {info.current_version}). {info.release_url or ''}"
        ).strip()
        notification_manager.send_notification(title=title, message=message)
    except Exception:
        log.exception("failed to dispatch update-available notification")


async def scan_missing_subtitles() -> None:
    from miramedia.background_services import bg_subtitle_service

    cfg = MiraMediaConfig().subtitles
    if not (cfg.enabled and cfg.native.enabled):
        return
    log.info("Running scheduled subtitle scan")
    async with bg_subtitle_service() as subtitle_service:
        await subtitle_service.scan_all_missing_subtitles()


async def scheduled_library_scan() -> None:
    """Library scan task."""
    if not MiraMediaConfig().imports.auto_scan_enabled:
        return
    log.info("Running scheduled library scan")
    from miramedia.imports.tasks import _scan_and_cache

    await _scan_and_cache()


async def fulfill_approved_requests() -> None:
    """Fulfil approved media requests."""
    from miramedia.background_services import (
        bg_movie_service,
        bg_request_service,
        bg_show_service,
    )

    if not MiraMediaConfig().requests.enabled:
        return

    seerr_client = build_seerr_client()
    if seerr_client is not None:
        try:
            async with bg_request_service() as (_, request_repository):
                await SeerrSyncService(request_repository, seerr_client).reconcile()
        except Exception:
            log.exception("Seerr reconcile failed")
        finally:
            await seerr_client.aclose()

    log.info("Checking for approved requests to download")
    async with bg_request_service() as (request_service, _):
        approved = await request_service.get_approved_not_downloaded()
    if not approved:
        return
    log.info("Found %s approved requests not yet downloaded", len(approved))

    for request in approved:
        try:
            is_fresh = request.status == RequestStatus.approved
            provider_name = request.metadata_provider or "native"
            metadata_provider = resolve_metadata_provider(provider_name)
            if metadata_provider is None:
                log.warning(
                    "No metadata provider available for request: %s",
                    request.title,
                )
                continue

            effective_id = request.external_id
            if metadata_provider.name == "native" and not effective_id.startswith("tt"):
                if not request.imdb_id:
                    async with bg_request_service() as (request_service, _):
                        request = await request_service.heal_missing_imdb_id(request)
                if request.imdb_id:
                    effective_id = request.imdb_id
                else:
                    log.warning(
                        "Cannot fulfill request %s: native provider requires IMDb ID but request has external_id=%s and no imdb_id stored",
                        request.title,
                        request.external_id,
                    )
                    continue

            if request.media_type.value == "movie":
                from miramedia.movies.service import (
                    _try_auto_download_movie_id_impl,
                )

                async with bg_movie_service() as movie_service:
                    movie = await movie_service.add_movie(
                        external_id=effective_id,
                        metadata_provider=metadata_provider,
                    )
                if is_fresh:
                    await _try_auto_download_movie_id_impl(movie.id)
                    async with bg_request_service() as (request_service, _):
                        await request_service.mark_downloading(request.id)
                async with bg_movie_service() as movie_service:
                    is_downloaded = await movie_service.is_movie_downloaded(movie=movie)
                if is_downloaded:
                    async with bg_request_service() as (request_service, _):
                        await request_service.mark_downloaded(request.id)
                    log.info("Downloaded movie request: %s", request.title)
                else:
                    log.info("Movie added but not yet downloaded: %s", request.title)

            elif request.media_type.value == "show":
                from miramedia.shows.service import (
                    _try_auto_download_show_id_impl,
                )

                async with bg_show_service() as show_service:
                    show = await show_service.add_show(
                        external_id=effective_id,
                        metadata_provider=metadata_provider,
                    )
                if is_fresh:
                    await _try_auto_download_show_id_impl(show.id)
                    async with bg_request_service() as (request_service, _):
                        await request_service.mark_downloading(request.id)
                has_downloaded = False
                async with bg_show_service() as show_service:
                    for season in show.seasons:
                        season_dir = show_service.get_root_season_directory(
                            show, season.number
                        )
                        season_files = await asyncio.to_thread(
                            show_service._scan_season_video_files, season_dir
                        )
                        for episode in season.episodes:
                            if show_service._episode_downloaded_from_cache(
                                episode=episode,
                                season_number=season.number,
                                season_files=season_files,
                            ):
                                has_downloaded = True
                                break
                        if has_downloaded:
                            break
                if has_downloaded:
                    async with bg_request_service() as (request_service, _):
                        await request_service.mark_downloaded(request.id)
                    log.info("Downloaded show request: %s", request.title)
                else:
                    log.info("Show added but not yet downloaded: %s", request.title)

        except Exception:
            log.exception("Failed to fulfill request: %s", request.title)
