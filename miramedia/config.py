from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import ClassVar, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AnyHttpUrl, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from miramedia.auth.config import AuthConfig
from miramedia.cloudflare.config import CloudflareConfig
from miramedia.database.config import DbConfig
from miramedia.indexers.config import IndexerConfig
from miramedia.metadata.config import MetadataProviderConfig
from miramedia.naming_defaults import (
    DEFAULT_EPISODE_FILE_FORMAT,
    DEFAULT_MOVIE_FILE_FORMAT,
    DEFAULT_MOVIE_FOLDER_FORMAT,
    DEFAULT_SEASON_FOLDER_FORMAT,
    DEFAULT_SHOW_FOLDER_FORMAT,
)
from miramedia.notifications.config import NotificationConfig
from miramedia.playback.config import PlaybackConfig
from miramedia.requests.config import RequestsConfig
from miramedia.streams.config import StreamsConfig
from miramedia.subtitles.config import SubtitleConfig
from miramedia.torrents.config import TorrentConfig
from miramedia.updates.config import UpdateConfig
from miramedia.viewing_sync.config import ViewingSyncConfig
from miramedia.watchlists.config import WatchlistsConfig

log = logging.getLogger(__name__)

DEFAULT_TRUSTED_PROXY_HOSTS: list[str] = [
    "127.0.0.1",
    "::1",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
]

config_path = os.getenv("MIRAMEDIA_CONFIG_FILE")
if config_path is None:
    # Default to config folder approach
    config_dir = os.getenv("MIRAMEDIA_CONFIG_DIR", "/app/config")
    config_path = Path(config_dir) / "config.toml"
else:
    config_path = Path(config_path)
log.info("Using config file %s", config_path)


class LibraryItem(BaseSettings):
    name: str
    path: str


class NamingConfig(BaseSettings):
    movie_folder_format: str = DEFAULT_MOVIE_FOLDER_FORMAT
    show_folder_format: str = DEFAULT_SHOW_FOLDER_FORMAT
    season_folder_format: str = DEFAULT_SEASON_FOLDER_FORMAT
    movie_file_format: str = DEFAULT_MOVIE_FILE_FORMAT
    episode_file_format: str = DEFAULT_EPISODE_FILE_FORMAT


class BasicConfig(BaseSettings):
    image_directory: Path = Path(__file__).parent.parent / "images"
    show_directory: Path = Path(__file__).parent.parent / "data" / "shows"
    movie_directory: Path = Path(__file__).parent.parent / "data" / "movies"
    torrent_directory: Path = Path(__file__).parent.parent / "data" / "torrents"
    completed_torrent_path: str = (
        ""  # Where finished downloads live. Empty = same as torrent_directory.
    )
    incomplete_torrent_path: str = "/data/torrents/incomplete"  # Native client downloads here, then moves to completed on finish. Empty = no split.

    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:8000")
    cors_urls: list[str] = []
    # Hosts allowed to set X-Forwarded-* (private/loopback by default). Use "*"
    # to trust all proxies (restores pre-129 behavior).
    trusted_proxy_hosts: list[str] | str = DEFAULT_TRUSTED_PROXY_HOSTS
    development: bool = False

    # Server timezone (IANA name, e.g. "America/New_York") for date math: provider
    # air-date parsing and the upcoming/calendar window. Blank = use the process /
    # container zone (honors the TZ env). Editable in the UI; see configured_timezone.
    timezone: str = ""

    show_libraries: list[LibraryItem] = []
    movie_libraries: list[LibraryItem] = []
    naming: NamingConfig = NamingConfig()

    continuous_download: bool = True
    # Opt-in movie quality upgrades (design 309 Slice A). When off, movies keep
    # today's "stop when any file exists" auto-download behavior.
    quality_upgrades: bool = False
    # Global upgrade-until cutoff (indexer quality option name). Null uses the
    # highest enabled quality option.
    upgrade_until_quality: str | None = None
    # Auto-download Season 0 specials. Off by default — when off, specials are
    # surfaced as "skipped" and excluded from the auto-download sweep.
    download_specials: bool = False
    auto_download_interval_hours: int = 1
    cleanup_after_import: bool = True
    log_retention_days: int = 30
    # Purge cached indexer search rows older than this (nightly cron).
    indexer_query_result_retention_days: int = 7
    # Background import sweep for finished torrents (user-triggered import unchanged).
    import_sweep_interval_minutes: int = 5
    # Observe-only Torznab/Newznab release feeds (design 385 Slice A). Default off.
    release_feeds_enabled: bool = False
    release_feeds_poll_interval_minutes: int = 15
    release_feeds_maxage_days: int = 2

    # Phase 6.5 — opt-in. When enabled, every newly-imported file gets a SHA1
    # stored in its EpisodeFile/MovieFile row, and a periodic audit re-hashes
    # imported files and logs WARNINGs on mismatch. Off by default because
    # hashing every import doubles the disk read cost on import.
    integrity_check_enabled: bool = False
    integrity_check_interval_hours: int = 168

    # Confidence threshold above which the imports page highlights the top
    # candidate as the primary "Pick" action. Below this, Pick is still
    # available but rendered as a secondary action so the user thinks twice.
    auto_pick_confidence_threshold: float = 0.8

    # Prometheus metrics scrape endpoint (/api/v1/metrics).
    # Default: deny — only superusers may scrape. Set to true to allow
    # unauthenticated access (e.g. when your Prometheus scraper has no
    # credentials and the endpoint is firewalled from end-users).
    metrics_public: bool = False

    # Content-Security-Policy rollout. Default: send the policy as
    # Content-Security-Policy-Report-Only so violations surface in the browser
    # console without breaking the SPA. Set csp_enforce = true to promote the
    # same policy to an enforcing Content-Security-Policy header.
    csp_enabled: bool = True
    csp_enforce: bool = False

    @model_validator(mode="before")
    @classmethod
    def strip_legacy_preferences(cls, data: dict) -> dict:
        """Strip old fields that have moved to indexers config."""
        if not isinstance(data, dict):
            return data
        data.pop("preferred_quality", None)
        data.pop("preferred_codec", None)
        data.pop("accepted_qualities", None)
        data.pop("accepted_codecs", None)
        return data

    @property
    def effective_completed_path(self) -> Path:
        """Resolve completed download path, falling back to torrent_directory."""
        completed = (self.completed_torrent_path or "").strip()
        return Path(completed) if completed else self.torrent_directory


class ImportsConfig(BaseSettings):
    """Library-scan / imports behaviour."""

    # When a scanned directory has no strong existing-library match, query the
    # configured metadata provider(s) by the detected name/year and surface the
    # results as pickable candidates on the imports page.
    provider_search_on_scan: bool = True
    provider_search_max_results: int = 5

    # When enabled, the library scan will, without human interaction, create the
    # best-matching media (existing library item OR metadata-provider result)
    # and import the directory into it — but only if the best candidate's
    # confidence is at or above ``auto_import_min_confidence``. The single
    # highest-scoring candidate wins even if several clear the threshold.
    auto_import_on_scan: bool = False
    auto_import_min_confidence: float = 0.9

    # Periodic background library scan. When enabled the scan task runs on
    # ``auto_scan_interval_hours`` and feeds the imports page (and auto-import
    # when ``auto_import_on_scan`` is also on). Does not affect the Movies /
    # Shows pages — results only appear on the Imports page.
    auto_scan_enabled: bool = False
    auto_scan_interval_hours: int = 6


class MiraMediaConfig(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=config_path,
        case_sensitive=False,
        env_nested_delimiter="__",
        env_prefix="MIRAMEDIA_",
    )
    """
    This class is used to load all configurations from the environment variables.
    It combines the BasicConfig with any additional configurations needed.

    Acts as a singleton — repeated calls to ``MiraMediaConfig()`` return the
    same instance so that DB overrides applied at startup are visible everywhere.
    """
    misc: BasicConfig = BasicConfig()
    torrents: TorrentConfig = TorrentConfig()
    notifications: NotificationConfig = NotificationConfig()
    metadata: MetadataProviderConfig = MetadataProviderConfig()
    indexers: IndexerConfig = IndexerConfig()
    database: DbConfig = DbConfig()
    auth: AuthConfig = AuthConfig()
    requests: RequestsConfig = RequestsConfig()
    watchlists: WatchlistsConfig = WatchlistsConfig()
    subtitles: SubtitleConfig = SubtitleConfig()
    updates: UpdateConfig = UpdateConfig()
    cloudflare: CloudflareConfig = CloudflareConfig()
    imports: ImportsConfig = ImportsConfig()
    streams: StreamsConfig = StreamsConfig()
    playback: PlaybackConfig = PlaybackConfig()
    viewing_sync: ViewingSyncConfig = ViewingSyncConfig()

    _instance: ClassVar[MiraMediaConfig | None] = None
    _initialized: ClassVar[bool] = False

    def __new__(cls, *_args: object, **_kwargs: object) -> Self:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cast("Self", cls._instance)

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        if MiraMediaConfig._initialized:
            return
        super().__init__(**kwargs)
        MiraMediaConfig._initialized = True

    @classmethod
    def load_isolated(cls) -> Self:
        """Load TOML/env settings into a fresh instance without touching the singleton."""
        instance = object.__new__(cls)
        BaseSettings.__init__(instance)
        return instance

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


def configured_timezone() -> tzinfo:
    """Effective server timezone for date math (air dates, upcoming/calendar window).

    Resolution order:
      1. The ``misc.timezone`` setting (IANA name, DB-overridable from the UI).
      2. The process / container local zone (honors the ``TZ`` env) when blank.
      3. UTC as a last resort.

    An invalid persisted name falls back the same way with a warning rather than
    raising, so a bad value can never break air-date handling.
    """
    name = (MiraMediaConfig().misc.timezone or "").strip()
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            log.warning(
                "Invalid misc.timezone %r; falling back to the server zone", name
            )
    return datetime.now().astimezone().tzinfo or UTC
