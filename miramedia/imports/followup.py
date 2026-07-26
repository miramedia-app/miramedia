"""Post-scan-import follow-up.

After a scanned directory is imported into a (possibly newly created) show or
movie, run native subtitle search for the imported media (the subtitle service
self-gates on ``subtitles.native.enabled``, so this is a no-op when off).

Scan-import is an *import existing files* operation, NOT a *fetch missing
parts* one. It deliberately does NOT trigger continuous-download: the import
matcher and the continuous-download "is this present?" check are different
matchers, so any on-disk file the matcher fails to link (anime absolute
numbering, multi-episode files, odd release names, movie title/year/quality
stem drift) would be seen as missing and re-downloaded — searching for a
torrent instead of using the file the user already has on disk. Missing parts
are still filled by the normal periodic continuous-download sweep, which the
user controls per-media / via the global default.
"""

from __future__ import annotations

import logging
from uuid import UUID

from miramedia.file_status import ImportOutcome
from miramedia.shows.schemas import EpisodeId
from miramedia.torrents.schemas import MediaType

log = logging.getLogger(__name__)


async def run_post_import_completion(
    *,
    db,  # noqa: ANN001 - SQLAlchemy session
    media_type: MediaType,
    media,  # noqa: ANN001 - Show | Movie schema
    show_service,  # noqa: ANN001
    movie_service,  # noqa: ANN001
) -> None:
    # Re-fetch so subtitle search sees the just-imported episode/movie files
    # (the passed-in object predates the import).
    try:
        if media_type == MediaType.show:
            media = await show_service.get_show_by_id(show_id=media.id)
        else:
            media = await movie_service.get_movie_by_id(media.id)
    except Exception:
        log.exception("Post-import re-fetch failed for %s", media.name)
        return

    # Native subtitle search (search_* methods self-gate on config).
    try:
        from miramedia.subtitles.repository import SubtitleRepository
        from miramedia.subtitles.service import SubtitleService

        subtitle_service = SubtitleService(
            subtitle_repository=SubtitleRepository(db),
            show_service=show_service,
            movie_service=movie_service,
        )
        if media_type == MediaType.show:
            # (episode_file_id, episode_id) for every file this show actually
            # imported — pushed to Bazarr as ONE webhook below, so a season
            # pack costs one POST instead of one per file.
            bazarr_pairs: list[tuple[UUID, EpisodeId]] = []
            for season in media.seasons:
                for episode in season.episodes:
                    # Only search episodes that actually have an imported file.
                    # Scan-import touches a handful of episodes, but ``media``
                    # is the whole show — iterating every episode of a large
                    # series (hundreds of rows) fired a subtitle search per
                    # episode, each resolving show/season + scanning disk for a
                    # video that isn't there ("No video file found" warning) and
                    # blocking the shared event loop long enough to starve
                    # concurrent request sessions. ``episode_files`` is
                    # eager-loaded by ``get_show_by_id``.
                    if not episode.episode_files:
                        continue
                    try:
                        await subtitle_service.search_episode_subtitles(episode.id)
                    except Exception:
                        log.exception(
                            "Subtitle search failed for episode %s", episode.id
                        )
                    bazarr_pairs.extend(
                        (episode_file.id, episode.id)
                        for episode_file in episode.episode_files
                        if episode_file.import_status == ImportOutcome.imported
                    )
            await subtitle_service.notify_bazarr_episodes_imported(db, bazarr_pairs)
        else:
            await subtitle_service.search_movie_subtitles(media.id)
            movie_files = (
                await movie_service.movie_repository.get_movie_files_by_movie_id(
                    media.id
                )
            )
            for movie_file in movie_files:
                if movie_file.import_status != ImportOutcome.imported:
                    continue
                await subtitle_service.notify_bazarr_movie_imported(
                    db, movie_file.id, media.id
                )
    except Exception:
        log.exception("Post-import subtitle follow-up failed for %s", media.name)
