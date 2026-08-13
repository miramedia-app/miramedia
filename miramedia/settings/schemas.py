from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from miramedia.settings.normalize import (
    migrate_native_metadata_enabled,
    migrate_requests_section,
    migrate_subtitles_section,
)


class SettingsSectionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Misc ---
class LibraryItemSchema(SettingsSectionSchema):
    name: str
    path: str


class NamingSettingsSchema(SettingsSectionSchema):
    movie_folder_format: str | None = None
    show_folder_format: str | None = None
    season_folder_format: str | None = None
    movie_file_format: str | None = None
    episode_file_format: str | None = None


class MiscSettingsSchema(SettingsSectionSchema):
    image_directory: str | None = None
    show_directory: str | None = None
    movie_directory: str | None = None
    torrent_directory: str | None = None
    completed_torrent_path: str | None = None
    incomplete_torrent_path: str | None = None
    frontend_url: str | None = None
    cors_urls: list[str] | None = None
    development: bool | None = None
    timezone: str | None = None
    show_libraries: list[LibraryItemSchema] | None = None
    movie_libraries: list[LibraryItemSchema] | None = None
    naming: NamingSettingsSchema | None = None
    continuous_download: bool | None = None
    download_specials: bool | None = None
    auto_download_interval_hours: int | None = None
    cleanup_after_import: bool | None = None
    log_retention_days: int | None = None
    indexer_query_result_retention_days: int | None = None
    import_sweep_interval_minutes: int | None = None
    integrity_check_enabled: bool | None = None
    integrity_check_interval_hours: int | None = None
    auto_pick_confidence_threshold: float | None = None
    trusted_proxy_hosts: list[str] | str | None = None
    metrics_public: bool | None = None
    csp_enabled: bool | None = None
    csp_enforce: bool | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        """Reject unknown zones so the UI surfaces a clear error. Blank = server zone."""
        if not value:
            return value
        from zoneinfo import available_timezones

        if value not in available_timezones():
            msg = (
                f"Unknown timezone {value!r}; use an IANA name like 'America/New_York'."
            )
            raise ValueError(msg)
        return value

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


# --- Auth ---
class OpenIdSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    client_id: str | None = None
    client_secret: str | None = None
    configuration_endpoint: str | None = None
    name: str | None = None


class AuthSettingsSchema(SettingsSectionSchema):
    session_lifetime: int | None = None
    email_password_resets: bool | None = None
    allow_registration: bool | None = None
    cookie_secure: bool | None = None
    openid_connect: OpenIdSettingsSchema | None = None
    # admin_emails intentionally omitted — deprecated, manage superusers via the Users page.

    @field_validator("session_lifetime")
    @classmethod
    def validate_session_lifetime(cls, value: int | None) -> int | None:
        from miramedia.auth.config import validate_session_lifetime_value

        if value is None:
            return None
        return validate_session_lifetime_value(value)


# --- Notifications ---
class SmtpSettingsSchema(SettingsSectionSchema):
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    from_email: str | None = None
    use_tls: bool | None = None


class EmailNotificationsSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    emails: list[str] | None = None


class GotifySettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    api_key: str | None = None
    url: str | None = None


class NtfySettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    url: str | None = None


class PushoverSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    api_key: str | None = None
    user: str | None = None


class NativeNotificationSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    retention_days: int | None = None


class NotificationSettingsSchema(SettingsSectionSchema):
    subject_prefix: str | None = None
    native: NativeNotificationSettingsSchema | None = None
    smtp_config: SmtpSettingsSchema | None = None
    email_notifications: EmailNotificationsSettingsSchema | None = None
    gotify: GotifySettingsSchema | None = None
    ntfy: NtfySettingsSchema | None = None
    pushover: PushoverSettingsSchema | None = None


# --- Torrents ---
class QbittorrentSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    category_name: str | None = None
    category_save_path: str | None = None


class TransmissionSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    path: str | None = None
    https_enabled: bool | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None


class SabnzbdSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    host: str | None = None
    port: int | None = None
    api_key: str | None = None
    base_path: str | None = None
    verify_tls: bool | None = None


class NativeTorrentSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    listen_port_start: int | None = None
    max_download_rate: int | None = None
    max_upload_rate: int | None = None


class TorrentSettingsSchema(SettingsSectionSchema):
    qbittorrent: QbittorrentSettingsSchema | None = None
    transmission: TransmissionSettingsSchema | None = None
    sabnzbd: SabnzbdSettingsSchema | None = None
    native: NativeTorrentSettingsSchema | None = None


# --- Indexers ---
class ProwlarrSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    api_key: str | None = None
    url: str | None = None


class JackettSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    api_key: str | None = None
    url: str | None = None
    indexers: list[str] | None = None


class TorznabSiteSettingsSchema(SettingsSectionSchema):
    name: str | None = None
    url: str | None = None
    api_key: str | None = None
    supports_tv: bool | None = None
    supports_movies: bool | None = None
    categories_tv: str | None = None
    categories_movies: str | None = None
    cloudflare_protected: bool | None = None


class NativeIndexerSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    max_concurrent_searches: int | None = None
    custom_torznab_sites: list[TorznabSiteSettingsSchema] | None = None
    disabled_sites: list[str] | None = None


class CloudflareRemoteSettingsSchema(SettingsSectionSchema):
    endpoint: str | None = None


class CloudflareUrlBackendSettingsSchema(SettingsSectionSchema):
    url: str | None = None


class CloudflareBrowserRunSettingsSchema(SettingsSectionSchema):
    account_id: str | None = None
    api_token: str | None = None


class CloudflareFirecrawlSettingsSchema(SettingsSectionSchema):
    api_key: str | None = None
    base_url: str | None = None


class CloudflareSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    solver: str | None = None
    proxy: str | None = None
    browser_path: str | None = None
    cookie_ttl_seconds: int | None = None
    impersonate_profile: str | None = None
    warmup_on_startup: bool | None = None
    browser_launch_timeout_seconds: float | None = None
    page_load_timeout_seconds: int | None = None
    challenge_wait_seconds: float | None = None
    solve_timeout_seconds: int | None = None
    remote: CloudflareRemoteSettingsSchema | None = None
    byparr: CloudflareUrlBackendSettingsSchema | None = None
    flaresolverr: CloudflareUrlBackendSettingsSchema | None = None
    browser_run: CloudflareBrowserRunSettingsSchema | None = None
    firecrawl: CloudflareFirecrawlSettingsSchema | None = None


class TitleScoringRuleSchema(SettingsSectionSchema):
    name: str
    keywords: list[str]
    score_modifier: int = 0
    enabled: bool = True


class IndexerFlagScoringRuleSchema(SettingsSectionSchema):
    name: str
    flags: list[str]
    score_modifier: int = 0
    enabled: bool = True


class QualityOptionSchema(SettingsSectionSchema):
    name: str
    keywords: list[str]
    score_modifier: int = 0
    enabled: bool = True


class CodecOptionSchema(SettingsSectionSchema):
    name: str
    keywords: list[str]
    score_modifier: int = 0
    enabled: bool = True


class ScoringRuleSetSchema(SettingsSectionSchema):
    name: str
    libraries: list[str] = []
    rule_names: list[str] = []


class IndexerSettingsSchema(SettingsSectionSchema):
    timeout_seconds: int | None = None
    prowlarr: ProwlarrSettingsSchema | None = None
    jackett: JackettSettingsSchema | None = None
    native: NativeIndexerSettingsSchema | None = None
    quality_options: list[QualityOptionSchema] | None = None
    codec_options: list[CodecOptionSchema] | None = None
    title_scoring_rules: list[TitleScoringRuleSchema] | None = None
    indexer_flag_scoring_rules: list[IndexerFlagScoringRuleSchema] | None = None
    scoring_rule_sets: list[ScoringRuleSetSchema] | None = None
    minimum_seeders: int | None = None
    maximum_seeders: int | None = None
    min_size_mb: int | None = None
    max_size_mb: int | None = None
    preferred_languages: list[str] | None = None
    rejected_languages: list[str] | None = None
    recency_bonus: int | None = None
    recency_decay_days: int | None = None


# --- Metadata ---
class TvmazeSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None


class CinemetaSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None


class NativeMetadataSettingsSchema(SettingsSectionSchema):
    tvmaze: TvmazeSettingsSchema | None = None
    cinemeta: CinemetaSettingsSchema | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_enabled(cls, data: dict) -> dict:
        """Legacy `[metadata.native].enabled` → split into tvmaze + cinemeta."""
        if not isinstance(data, dict):
            return data
        return migrate_native_metadata_enabled(data)


class TmdbSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    api_key: str | None = None
    primary_languages: list[str] | None = None
    default_language: str | None = None


class TvdbSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    api_key: str | None = None


class MetadataSettingsSchema(SettingsSectionSchema):
    desired_languages: list[str] | None = None
    check_interval_hours: int | None = None
    failure_backoff_hours: int | None = None
    native: NativeMetadataSettingsSchema | None = None
    tmdb: TmdbSettingsSchema | None = None
    tvdb: TvdbSettingsSchema | None = None


# --- Requests ---
class SeerrSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    url: str | None = None
    api_key: str | None = None


class NativeRequestsSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None


class RequestsSettingsSchema(SettingsSectionSchema):
    auto_approve_users: bool | None = None
    fulfill_interval_hours: int | None = None
    native: NativeRequestsSettingsSchema | None = None
    seerr: SeerrSettingsSchema | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_master_enabled(cls, data: dict) -> dict:
        """Map legacy ``requests.enabled = true`` → ``native.enabled = true``."""
        if not isinstance(data, dict):
            return data
        return migrate_requests_section(data)


# --- Watchlists ---
class NativeWatchlistsSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    custom_lists: bool | None = None
    watch_next: bool | None = None
    watch_next_include_specials: bool | None = None
    upcoming: bool | None = None
    upcoming_default_past_days: int | None = None
    upcoming_default_future_days: int | None = None


class WatchlistsSettingsSchema(SettingsSectionSchema):
    auto_remove_watched: bool | None = None
    continue_watching: bool | None = None
    max_lists_per_user: int | None = None
    max_items_per_list: int | None = None
    native: NativeWatchlistsSettingsSchema | None = None


# --- Subtitles ---
class SubtitleProviderSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None


class NativeSubtitleSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    scan_interval_hours: int | None = None
    gestdown: SubtitleProviderSettingsSchema | None = None
    tvsubtitles: SubtitleProviderSettingsSchema | None = None
    yifysubtitles: SubtitleProviderSettingsSchema | None = None
    subtitlecat: SubtitleProviderSettingsSchema | None = None
    subf2m: SubtitleProviderSettingsSchema | None = None
    isubtitles: SubtitleProviderSettingsSchema | None = None
    my_subs: SubtitleProviderSettingsSchema | None = None
    embeddedsubtitles: SubtitleProviderSettingsSchema | None = None
    opensubtitlescom: SubtitleProviderSettingsSchema | None = None
    addic7ed: SubtitleProviderSettingsSchema | None = None
    subdl: SubtitleProviderSettingsSchema | None = None
    subsource: SubtitleProviderSettingsSchema | None = None
    napiprojekt: SubtitleProviderSettingsSchema | None = None
    subtis: SubtitleProviderSettingsSchema | None = None
    subtitulamos: SubtitleProviderSettingsSchema | None = None


class BazarrSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    url: str | None = None
    api_key: str | None = None
    shim_api_key: str | None = None


class SubtitleSettingsSchema(SettingsSectionSchema):
    desired_languages: list[str] | None = None
    native: NativeSubtitleSettingsSchema | None = None
    bazarr: BazarrSettingsSchema | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_master_enabled(cls, data: dict) -> dict:
        """Legacy ``subtitles.enabled = false`` → flip both backends off."""
        if not isinstance(data, dict):
            return data
        return migrate_subtitles_section(data)


# --- Updates ---
class UpdateSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    repo: str | None = None
    check_interval_hours: int | None = None
    include_prereleases: bool | None = None
    cache_ttl_seconds: int | None = None
    request_timeout_seconds: int | None = None
    notify_on_new_version: bool | None = None
    image_repository: str | None = None
    image_tag: str | None = None


# --- Imports ---
class ImportsSettingsSchema(SettingsSectionSchema):
    provider_search_on_scan: bool | None = None
    provider_search_max_results: int | None = None
    auto_import_on_scan: bool | None = None
    auto_import_min_confidence: float | None = None
    auto_scan_enabled: bool | None = None
    auto_scan_interval_hours: int | None = None


# --- Top-level ---
class SystemSettingsUpdate(SettingsSectionSchema):
    """Partial update — only include sections/fields you want to override."""

    misc: MiscSettingsSchema | None = None
    auth: AuthSettingsSchema | None = None
    notifications: NotificationSettingsSchema | None = None
    torrents: TorrentSettingsSchema | None = None
    indexers: IndexerSettingsSchema | None = None
    metadata: MetadataSettingsSchema | None = None
    requests: RequestsSettingsSchema | None = None
    watchlists: WatchlistsSettingsSchema | None = None
    subtitles: SubtitleSettingsSchema | None = None
    updates: UpdateSettingsSchema | None = None
    cloudflare: CloudflareSettingsSchema | None = None
    imports: ImportsSettingsSchema | None = None


class SystemSettingsRead(BaseModel):
    """Full effective config (TOML + DB overrides merged)."""

    misc: dict
    auth: dict
    notifications: dict
    torrents: dict
    indexers: dict
    metadata: dict
    requests: dict
    watchlists: dict
    subtitles: dict
    updates: dict
    cloudflare: dict
    imports: dict
    overrides: dict  # The raw DB overrides, so the UI can show what's been changed
    defaults: dict | None = (
        None  # TOML-only defaults, used for "Reset to default" tooltips
    )
