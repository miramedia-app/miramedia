from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from miramedia.config import MiraMediaConfig
from miramedia.file_status import ImportOutcome
from miramedia.movies.schemas import MovieId
from miramedia.movies.service import MovieService
from miramedia.naming import (
    episode_file_stem_candidates,
    movie_file_stem_candidates,
)
from miramedia.shows.schemas import EpisodeId, ShowId
from miramedia.shows.service import ShowService
from miramedia.subtitles.bazarr_client import BazarrClient
from miramedia.subtitles.repository import SubtitleRepository
from miramedia.subtitles.schemas import (
    EpisodeSubtitleStatus,
    ShowSubtitleStatus,
    SubtitleFile,
    SubtitleStatus,
)
from miramedia.torrents.quality_naming import NameParts
from miramedia.torrents.schemas import Quality

if TYPE_CHECKING:
    from miramedia.subtitles.models import SubtitleRecord

log = logging.getLogger(__name__)

SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass", ".ssa", ".sub"}

# Maps config field name → subliminal provider registry name
PROVIDER_MAP: dict[str, str] = {
    "gestdown": "gestdown",
    # tvsubtitles is the catalog replacement registered under the same name
    # (subliminal's dead built-in is evicted in miramedia.subtitles.plugins).
    "tvsubtitles": "tvsubtitles",
    "yifysubtitles": "yifysubtitles",
    "subsource": "subsource",
    "opensubtitlescom": "opensubtitlescom",
    "addic7ed": "addic7ed",
    "subdl": "subdl",
    "napiprojekt": "napiprojekt",
    "subtis": "subtis",
    "subtitulamos": "subtitulamos",
    # Keyless plugin providers (LavX/bazarr-provider-catalog), see
    # miramedia.subtitles.plugins.
    "subtitlecat": "subtitlecat",
    "subf2m": "subf2m",
    "isubtitles": "isubtitles",
    "my_subs": "my_subs",
    "embeddedsubtitles": "embeddedsubtitles",
}

# Providers that accept username/password credentials
CREDENTIALED_PROVIDERS = {"opensubtitlescom", "addic7ed"}

# Providers that accept an api_key credential
API_KEY_PROVIDERS = {"subdl", "subsource"}


class SubtitleService:
    def __init__(
        self,
        subtitle_repository: SubtitleRepository,
        show_service: ShowService | None = None,
        movie_service: MovieService | None = None,
    ) -> None:
        self.subtitle_repository = subtitle_repository
        self.show_service = show_service
        self.movie_service = movie_service

    def _get_config(self) -> MiraMediaConfig:
        return MiraMediaConfig()

    def _get_desired_languages(self) -> list[str]:
        return self._get_config().subtitles.desired_languages

    async def _release_session_before_external_io(self) -> None:
        """Thin wrapper around :func:`release_session_before_external_io`."""
        from miramedia.database import release_session_before_external_io

        await release_session_before_external_io(self.subtitle_repository.db)

    # --- Disk scanning ---

    def get_existing_subtitle_languages(
        self, directory: Path, file_stem: str
    ) -> list[SubtitleFile]:
        """Scan a directory for subtitle files matching the given file stem."""
        if not directory.exists():
            return []

        results: list[SubtitleFile] = []
        # Match patterns like "filename.en.srt", "filename - 1080P.en.srt", "filename.eng.srt"
        pattern = re.compile(
            re.escape(file_stem) + r".*\.([a-zA-Z]{2,3})\.\w+$", re.IGNORECASE
        )

        for f in directory.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue
            match = pattern.match(f.name)
            if match:
                lang = match.group(1).lower()
                results.append(SubtitleFile(language=lang, file_name=f.name))
        return results

    def get_existing_subtitle_languages_for_stems(
        self, directory: Path, file_stems: list[str]
    ) -> list[SubtitleFile]:
        seen: set[str] = set()
        results: list[SubtitleFile] = []
        for stem in file_stems:
            for subtitle in self.get_existing_subtitle_languages(directory, stem):
                if subtitle.file_name in seen:
                    continue
                seen.add(subtitle.file_name)
                results.append(subtitle)
        return results

    async def get_episode_subtitle_status(
        self, episode_id: EpisodeId
    ) -> SubtitleStatus:
        """Get subtitle status for a specific episode."""
        try:
            episode = await self.show_service.get_episode(episode_id=episode_id)
            season = await self.show_service.get_season_by_episode(
                episode_id=episode_id
            )
            show = await self.show_service.show_repository.get_show_by_season_id(
                season_id=season.id
            )
        except Exception:
            return SubtitleStatus(
                media_type="episode",
                media_id=episode_id,
                desired_languages=self._get_desired_languages(),
                available_languages=[],
                missing_languages=self._get_desired_languages(),
            )

        season_dir = self.show_service.get_root_season_directory(
            show=show, season_number=season.number
        )
        file_stems = episode_file_stem_candidates(
            show,
            season_number=season.number,
            episode_number=episode.number,
            quality=Quality.unknown,
            parts=NameParts(),
        )

        existing = self.get_existing_subtitle_languages_for_stems(
            season_dir, file_stems
        )
        available = sorted({s.language for s in existing})
        desired = self._get_desired_languages()
        missing = [lang for lang in desired if lang not in available]

        return SubtitleStatus(
            media_type="episode",
            media_id=episode_id,
            desired_languages=desired,
            available_languages=available,
            missing_languages=missing,
        )

    async def get_show_subtitle_status(
        self,
        show_id: ShowId,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> ShowSubtitleStatus:
        """Subtitle status for every episode of a show.

        Optionally filtered to a single season and/or episode number. One call
        replaces per-episode status fetches. Disk scans are batched per season
        (one for video files + one for subtitles) and dispatched concurrently
        so the dashboard doesn't pay per-episode iterdir cost.
        """
        desired = self._get_desired_languages()
        try:
            show = await self.show_service.get_show_by_id(show_id=show_id)
        except Exception:
            return ShowSubtitleStatus(
                show_id=show_id, desired_languages=desired, episodes=[]
            )

        seasons_in_scope = [
            s
            for s in show.seasons
            if season_number is None or s.number == season_number
        ]
        season_dirs = {
            s.id: self.show_service.get_root_season_directory(
                show=show, season_number=s.number
            )
            for s in seasons_in_scope
        }
        # One iterdir per season for videos + one for subtitle files, all run
        # off the event loop in parallel.
        scans = await asyncio.gather(
            *(
                asyncio.gather(
                    asyncio.to_thread(ShowService._scan_season_video_files, d),
                    asyncio.to_thread(self._scan_season_subtitle_files, d),
                )
                for d in season_dirs.values()
            )
        )
        season_video_files = {
            sid: vids for sid, (vids, _) in zip(season_dirs.keys(), scans, strict=True)
        }
        season_subtitle_files = {
            sid: subs for sid, (_, subs) in zip(season_dirs.keys(), scans, strict=True)
        }

        episodes_out: list[EpisodeSubtitleStatus] = []
        for season in seasons_in_scope:
            sub_files = season_subtitle_files.get(season.id, [])
            video_set = season_video_files.get(season.id, set())
            for episode in season.episodes:
                if episode_number is not None and episode.number != episode_number:
                    continue
                file_stems = episode_file_stem_candidates(
                    show,
                    season_number=season.number,
                    episode_number=episode.number,
                    quality=Quality.unknown,
                    parts=NameParts(),
                )
                existing = self._match_subtitles_from_cache(file_stems, sub_files)
                available = sorted({s.language for s in existing})
                missing = [lang for lang in desired if lang not in available]
                downloaded = ShowService._episode_downloaded_from_cache(
                    episode=episode,
                    season_number=season.number,
                    season_files=video_set,
                )
                episodes_out.append(
                    EpisodeSubtitleStatus(
                        episode_id=episode.id,
                        season_id=season.id,
                        season_number=season.number,
                        episode_number=episode.number,
                        title=episode.title,
                        downloaded=downloaded,
                        status=SubtitleStatus(
                            media_type="episode",
                            media_id=episode.id,
                            desired_languages=desired,
                            available_languages=available,
                            missing_languages=missing,
                        ),
                    )
                )

        return ShowSubtitleStatus(
            show_id=show_id, desired_languages=desired, episodes=episodes_out
        )

    @staticmethod
    def _scan_season_subtitle_files(directory: Path) -> list[tuple[str, str]]:
        """Sync helper: list (filename, suffix-lower) for subtitle files."""
        if not directory.exists():
            return []
        return [
            (f.name, f.suffix.lower())
            for f in directory.iterdir()
            if f.is_file() and f.suffix.lower() in SUBTITLE_EXTENSIONS
        ]

    @staticmethod
    def _match_subtitles_from_cache(
        file_stems: list[str], sub_files: list[tuple[str, str]]
    ) -> list[SubtitleFile]:
        """Match cached subtitle files against episode stem candidates."""
        seen: set[str] = set()
        results: list[SubtitleFile] = []
        patterns = [
            re.compile(re.escape(stem) + r".*\.([a-zA-Z]{2,3})\.\w+$", re.IGNORECASE)
            for stem in file_stems
        ]
        for name, _suffix in sub_files:
            if name in seen:
                continue
            for pattern in patterns:
                match = pattern.match(name)
                if match:
                    seen.add(name)
                    results.append(
                        SubtitleFile(language=match.group(1).lower(), file_name=name)
                    )
                    break
        return results

    async def get_movie_subtitle_status(self, movie_id: MovieId) -> SubtitleStatus:
        """Get subtitle status for a specific movie."""
        try:
            movie = await self.movie_service.get_movie_by_id(movie_id=movie_id)
        except Exception:
            return SubtitleStatus(
                media_type="movie",
                media_id=movie_id,
                desired_languages=self._get_desired_languages(),
                available_languages=[],
                missing_languages=self._get_desired_languages(),
            )

        movie_root = self.movie_service.get_movie_root_path(movie=movie)
        file_stems = movie_file_stem_candidates(movie, Quality.unknown, NameParts())

        existing = self.get_existing_subtitle_languages_for_stems(
            movie_root, file_stems
        )
        available = sorted({s.language for s in existing})
        desired = self._get_desired_languages()
        missing = [lang for lang in desired if lang not in available]

        return SubtitleStatus(
            media_type="movie",
            media_id=movie_id,
            desired_languages=desired,
            available_languages=available,
            missing_languages=missing,
        )

    async def get_episode_subtitle_files(
        self, episode_id: EpisodeId
    ) -> list[SubtitleFile]:
        """List all subtitle files for an episode."""
        try:
            episode = await self.show_service.get_episode(episode_id=episode_id)
            season = await self.show_service.get_season_by_episode(
                episode_id=episode_id
            )
            show = await self.show_service.show_repository.get_show_by_season_id(
                season_id=season.id
            )
        except Exception:
            return []

        season_dir = self.show_service.get_root_season_directory(
            show=show, season_number=season.number
        )
        file_stems = episode_file_stem_candidates(
            show,
            season_number=season.number,
            episode_number=episode.number,
            quality=Quality.unknown,
            parts=NameParts(),
        )
        return self.get_existing_subtitle_languages_for_stems(season_dir, file_stems)

    async def get_show_subtitle_files(
        self, show_id: ShowId
    ) -> dict[str, list[SubtitleFile]]:
        """Subtitle files for every episode of a show, keyed by episode id.

        One call replaces the per-episode ``/files`` fan-out. Disk is scanned
        once per season (off the event loop, in parallel) and matched per
        episode from that cache. Episodes with no subtitle files are omitted.
        """
        try:
            show = await self.show_service.get_show_by_id(show_id=show_id)
        except Exception:
            return {}

        season_dirs = {
            s.id: self.show_service.get_root_season_directory(
                show=show, season_number=s.number
            )
            for s in show.seasons
        }
        scans = await asyncio.gather(
            *(
                asyncio.to_thread(self._scan_season_subtitle_files, d)
                for d in season_dirs.values()
            )
        )
        season_subtitle_files = dict(zip(season_dirs.keys(), scans, strict=True))

        out: dict[str, list[SubtitleFile]] = {}
        for season in show.seasons:
            sub_files = season_subtitle_files.get(season.id, [])
            if not sub_files:
                continue
            for episode in season.episodes:
                file_stems = episode_file_stem_candidates(
                    show,
                    season_number=season.number,
                    episode_number=episode.number,
                    quality=Quality.unknown,
                    parts=NameParts(),
                )
                matched = self._match_subtitles_from_cache(file_stems, sub_files)
                if matched:
                    out[str(episode.id)] = matched
        return out

    async def delete_episode_subtitle_file(
        self, episode_id: EpisodeId, file_name: str
    ) -> None:
        """Delete a specific subtitle file for an episode."""
        try:
            season = await self.show_service.get_season_by_episode(
                episode_id=episode_id
            )
            show = await self.show_service.show_repository.get_show_by_season_id(
                season_id=season.id
            )
        except Exception:
            log.warning(f"Could not resolve episode {episode_id} for subtitle deletion")
            return
        if Path(file_name).name != file_name:
            # file_name is a user-supplied query param joined to the media dir
            # and unlinked. Reject anything that isn't a bare basename so
            # "../../config.toml" can't escape the season dir and delete an
            # arbitrary file.
            log.warning("Rejected subtitle delete with unsafe file_name: %r", file_name)
            return
        season_dir = self.show_service.get_root_season_directory(
            show=show, season_number=season.number
        )
        target = season_dir / file_name

        def _unlink() -> bool:
            if target.exists() and target.is_file():
                target.unlink()
                return True
            return False

        if await asyncio.to_thread(_unlink):
            log.info(f"Deleted subtitle file: {target}")
        else:
            log.warning(f"Subtitle file not found: {target}")

    async def get_movie_subtitle_files(self, movie_id: MovieId) -> list[SubtitleFile]:
        """List all subtitle files for a movie."""
        try:
            movie = await self.movie_service.get_movie_by_id(movie_id=movie_id)
        except Exception:
            return []

        movie_root = self.movie_service.get_movie_root_path(movie=movie)
        file_stems = movie_file_stem_candidates(movie, Quality.unknown, NameParts())
        return self.get_existing_subtitle_languages_for_stems(movie_root, file_stems)

    async def delete_movie_subtitle_file(
        self, movie_id: MovieId, file_name: str
    ) -> None:
        """Delete a specific subtitle file for a movie."""
        try:
            movie = await self.movie_service.get_movie_by_id(movie_id=movie_id)
        except Exception:
            log.warning(f"Could not resolve movie {movie_id} for subtitle deletion")
            return
        if Path(file_name).name != file_name:
            # Reject non-basename input — see delete_episode_subtitle_file.
            log.warning("Rejected subtitle delete with unsafe file_name: %r", file_name)
            return
        movie_root = self.movie_service.get_movie_root_path(movie=movie)
        target = movie_root / file_name

        def _unlink() -> bool:
            if target.exists() and target.is_file():
                target.unlink()
                return True
            return False

        if await asyncio.to_thread(_unlink):
            log.info(f"Deleted subtitle file: {target}")
        else:
            log.warning(f"Subtitle file not found: {target}")

    # --- Native subtitle search (subliminal) ---

    async def search_episode_subtitles(self, episode_id: EpisodeId) -> list[str]:
        """Search and download missing subtitles for an episode using subliminal.

        The session bound to this service instance is held only across the
        pre-flight DB reads (episode/season/show + status). It is COMMITTED
        and released before the slow ``asyncio.to_thread`` subliminal call so
        we don't sit ``idle in transaction`` for the multi-minute provider
        round-trip. The post-download record-save uses the same session (still
        valid — the AsyncSession survives across awaits as long as the
        underlying connection can be checked back out).

        Callers that need stricter isolation per-episode (e.g. bulk scans)
        should construct a fresh ``bg_subtitle_service()`` per call instead.
        """
        config = self._get_config()
        if not config.subtitles.native.enabled:
            log.warning("Native subtitle search is disabled")
            return []

        status = await self.get_episode_subtitle_status(episode_id)
        if not status.missing_languages:
            log.debug(f"No missing subtitles for episode {episode_id}")
            return []

        try:
            episode = await self.show_service.get_episode(episode_id=episode_id)
            season = await self.show_service.get_season_by_episode(
                episode_id=episode_id
            )
            show = await self.show_service.show_repository.get_show_by_season_id(
                season_id=season.id
            )
        except Exception:
            log.exception(f"Failed to resolve episode {episode_id}")
            return []

        season_dir = self.show_service.get_root_season_directory(
            show=show, season_number=season.number
        )
        file_stems = episode_file_stem_candidates(
            show,
            season_number=season.number,
            episode_number=episode.number,
            quality=Quality.unknown,
            parts=NameParts(),
        )

        # Find the video file to scan
        video_path = self._find_first_video_file(season_dir, file_stems)
        if not video_path:
            log.warning(f"No video file found for episode {episode_id}")
            return []

        # Commit + release the connection before the slow subliminal call so
        # the pool stays healthy under provider timeouts / auth retries. The
        # AsyncSession remains usable after commit — SQLAlchemy will check
        # out a fresh connection for the post-download record save.
        await self._release_session_before_external_io()

        downloaded = await asyncio.to_thread(
            self._download_subtitles_subliminal,
            video_path,
            status.missing_languages,
            config,
            year=show.year,
            series_imdb_id=show.imdb_id,
        )

        # Record downloads
        for lang in downloaded:
            await self.subtitle_repository.save_record(
                _make_subtitle_record_model(
                    media_type="episode",
                    episode_id=episode_id,
                    language=lang,
                    source="native",
                )
            )

        return downloaded

    async def search_movie_subtitles(self, movie_id: MovieId) -> list[str]:
        """Search and download missing subtitles for a movie using subliminal."""
        config = self._get_config()
        if not config.subtitles.native.enabled:
            log.warning("Native subtitle search is disabled")
            return []

        status = await self.get_movie_subtitle_status(movie_id)
        if not status.missing_languages:
            log.debug(f"No missing subtitles for movie {movie_id}")
            return []

        try:
            movie = await self.movie_service.get_movie_by_id(movie_id=movie_id)
        except Exception:
            log.exception(f"Failed to resolve movie {movie_id}")
            return []

        movie_root = self.movie_service.get_movie_root_path(movie=movie)
        file_stems = movie_file_stem_candidates(movie, Quality.unknown, NameParts())

        video_path = self._find_first_video_file(movie_root, file_stems)
        if not video_path:
            log.warning(f"No video file found for movie {movie_id}")
            return []

        # See ``search_episode_subtitles`` for the rationale: release the
        # connection before the slow subliminal call.
        await self._release_session_before_external_io()

        downloaded = await asyncio.to_thread(
            self._download_subtitles_subliminal,
            video_path,
            status.missing_languages,
            config,
            year=movie.year,
            imdb_id=movie.imdb_id,
        )

        for lang in downloaded:
            await self.subtitle_repository.save_record(
                _make_subtitle_record_model(
                    media_type="movie",
                    movie_id=movie_id,
                    language=lang,
                    source="native",
                )
            )

        return downloaded

    def _download_subtitles_subliminal(
        self,
        video_path: Path,
        languages: list[str],
        config: MiraMediaConfig,
        *,
        year: int | None = None,
        imdb_id: str | None = None,
        series_imdb_id: str | None = None,
    ) -> list[str]:
        """Use subliminal to download subtitles. Returns list of downloaded language codes."""
        try:
            import subliminal
            from babelfish import Language

            # Registers keyless plugin providers (subtitlecat, subf2m,
            # isubtitles, my_subs, tvsubtitles, embeddedsubtitles) — see
            # miramedia.subtitles.plugins.
            import miramedia.subtitles.plugins

            # Registers custom providers (yifysubtitles, subdl, subsource)
            import miramedia.subtitles.providers  # noqa: F401

            # Configure subliminal's cache region if not already done.
            # Required by providers that use dogpile.cache (tvsubtitles,
            # addic7ed, opensubtitlescom, subtitulamos, yifysubtitles).
            if not subliminal.region.is_configured:
                subliminal.region.configure("dogpile.cache.memory")
        except ImportError:
            log.exception(
                "subliminal or babelfish not installed. "
                "Install with: pip install subliminal"
            )
            return []

        lang_set = set()
        for lang_code in languages:
            try:
                lang_set.add(Language.fromalpha2(lang_code))
            except Exception:
                try:
                    lang_set.add(Language(lang_code))
                except Exception:
                    log.warning(f"Invalid language code: {lang_code}")

        if not lang_set:
            return []

        try:
            video = subliminal.scan_video(str(video_path))
        except Exception:
            log.exception(f"Failed to scan video: {video_path}")
            return []

        # Enrich video with database metadata for better provider matching
        if year and not video.year:
            video.year = year
        if imdb_id and not video.imdb_id:
            video.imdb_id = imdb_id
        if (
            series_imdb_id
            and hasattr(video, "series_imdb_id")
            and not video.series_imdb_id
        ):
            video.series_imdb_id = series_imdb_id

        # Build provider list dynamically from config
        providers: list[str] = []
        provider_configs: dict[str, dict[str, str]] = {}
        native_config = config.subtitles.native

        for config_name, subliminal_name in PROVIDER_MAP.items():
            prov_config = getattr(native_config, config_name, None)
            if prov_config is None or not prov_config.enabled:
                continue
            # API key providers need a key to function
            if config_name in API_KEY_PROVIDERS:
                if not prov_config.api_key:
                    continue
                provider_configs[subliminal_name] = {
                    "api_key": prov_config.api_key,
                }
            if config_name in CREDENTIALED_PROVIDERS:
                if not (prov_config.username and prov_config.password):
                    continue
                provider_configs[subliminal_name] = {
                    "username": prov_config.username,
                    "password": prov_config.password,
                }
            providers.append(subliminal_name)

        if not providers:
            log.warning("No subtitle providers enabled")
            return []

        log.info(f"Searching subtitles with providers: {providers}")

        try:
            subtitles = subliminal.download_best_subtitles(
                {video},
                lang_set,
                providers=providers,
                provider_configs=provider_configs,
            )
        except Exception:
            log.exception("Failed to search/download subtitles")
            return []

        if not subtitles.get(video):
            # Retry with relaxed metadata (no year) for broader results
            log.info(
                f"No subtitles found for {video_path.name} with full metadata, retrying without year"
            )
            original_year = video.year
            video.year = None
            try:
                subtitles = subliminal.download_best_subtitles(
                    {video},
                    lang_set,
                    providers=providers,
                    provider_configs=provider_configs,
                )
            except Exception:
                log.exception("Failed to search/download subtitles (relaxed retry)")
                return []
            finally:
                video.year = original_year

        if not subtitles.get(video):
            log.info(f"No subtitles found for {video_path.name}")
            return []

        try:
            subliminal.save_subtitles(video, subtitles[video])
        except Exception:
            log.exception("Failed to save subtitles")
            return []

        downloaded = [str(sub.language) for sub in subtitles[video]]
        log.info(
            f"Downloaded {len(downloaded)} subtitle(s) for {video_path.name}: {downloaded}"
        )
        return downloaded

    def _find_video_file(self, directory: Path, file_stem: str) -> Path | None:
        """Find a video file in directory matching the stem (exact or with suffix)."""
        if not directory.exists():
            return None
        video_exts = {".mp4", ".mkv", ".avi", ".webm", ".mov", ".m4v", ".ts", ".wmv"}
        # Try exact match first
        for ext in video_exts:
            candidate = directory / f"{file_stem}{ext}"
            if candidate.exists():
                return candidate
        # Try glob match for files with a suffix (e.g. "Name (2024) - 720P.mp4")
        for f in directory.iterdir():
            if (
                f.is_file()
                and f.suffix.lower() in video_exts
                and f.name.lower().startswith(file_stem.lower())
            ):
                return f
        return None

    def _find_first_video_file(
        self, directory: Path, file_stems: list[str]
    ) -> Path | None:
        for stem in file_stems:
            match = self._find_video_file(directory, stem)
            if match is not None:
                return match
        return None

    # --- Bulk scanning ---

    async def scan_all_missing_subtitles(self) -> None:
        """Scan all media for missing subtitles and download them.

        NOTE: this used to call ``self.search_episode_subtitles`` /
        ``self.search_movie_subtitles`` directly per item, which meant the
        SubtitleService's bound DB session stayed open the entire walk while
        each item paused on subliminal HTTP. Under provider auth errors /
        timeouts the per-episode wall could climb into the minutes,
        accumulating ``idle in transaction`` sessions and draining the pool.
        Now: fetch the work-list once with a short-lived session, then for
        EACH item open a fresh ``bg_subtitle_service()`` so the DB connection
        is released for the duration of the subliminal call.
        """
        from miramedia.database import bg_subtitle_service

        config = self._get_config()
        if not config.subtitles.enabled or not config.subtitles.native.enabled:
            return

        log.info("Starting bulk subtitle scan")

        # Snapshot work-list with a short-lived session (the SubtitleService
        # instance we're called on may or may not carry a background session;
        # we keep DB churn here to a single short session of our own).
        episode_targets: list[EpisodeId] = []
        movie_targets: list[MovieId] = []
        try:
            async with bg_subtitle_service() as svc:
                shows = await svc.show_service.get_all_shows()
                for show in shows:
                    for season in show.seasons:
                        for episode in season.episodes:
                            episode_files = await svc.show_service.show_repository.get_episode_files_by_episode_id(
                                episode_id=episode.id
                            )
                            if not any(
                                f.import_status == ImportOutcome.imported
                                for f in episode_files
                            ):
                                continue
                            episode_targets.append(episode.id)
                movies = await svc.movie_service.get_all_movies()
                for movie in movies:
                    if await svc.movie_service.is_movie_downloaded(movie=movie):
                        movie_targets.append(movie.id)  # noqa: PERF401 — async guard, not comprehensible
        except Exception:
            log.exception("Failed to enumerate subtitle scan targets")
            return

        for episode_id in episode_targets:
            try:
                async with bg_subtitle_service() as svc:
                    await svc.search_episode_subtitles(episode_id)
            except Exception:
                log.exception("Failed to scan subtitles for episode %s", episode_id)

        for movie_id in movie_targets:
            try:
                async with bg_subtitle_service() as svc:
                    await svc.search_movie_subtitles(movie_id)
            except Exception:
                log.exception("Failed to scan subtitles for movie %s", movie_id)

        log.info("Finished bulk subtitle scan")

    # --- Bazarr integration ---

    async def search_episode_subtitles_bazarr(self, episode_id: EpisodeId) -> bool:
        """Trigger subtitle search via Bazarr for an episode."""
        config = self._get_config()
        if not config.subtitles.bazarr.enabled:
            log.warning("Bazarr integration is disabled")
            return False

        client = BazarrClient(
            url=config.subtitles.bazarr.url,
            api_key=config.subtitles.bazarr.api_key,
        )
        return await asyncio.to_thread(client.search_episode_subtitles, str(episode_id))

    async def search_movie_subtitles_bazarr(self, movie_id: MovieId) -> bool:
        """Trigger subtitle search via Bazarr for a movie."""
        config = self._get_config()
        if not config.subtitles.bazarr.enabled:
            log.warning("Bazarr integration is disabled")
            return False

        client = BazarrClient(
            url=config.subtitles.bazarr.url,
            api_key=config.subtitles.bazarr.api_key,
        )
        return await asyncio.to_thread(client.search_movie_subtitles, str(movie_id))


def _make_subtitle_record_model(
    media_type: str,
    language: str,
    source: str,
    episode_id: EpisodeId | None = None,
    movie_id: MovieId | None = None,
    provider: str | None = None,
) -> SubtitleRecord:
    """Create a SubtitleRecord ORM model instance."""
    from miramedia.subtitles.models import SubtitleRecord as SubtitleRecordModel

    return SubtitleRecordModel(
        id=uuid.uuid4(),
        media_type=media_type,
        episode_id=episode_id,
        movie_id=movie_id,
        language=language,
        source=source,
        provider=provider,
    )
