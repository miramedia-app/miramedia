from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


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


class NativeIndexerSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    max_concurrent_searches: int | None = None


class CloudflareSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    browser_path: str | None = None
    cookie_ttl_seconds: int | None = None
    impersonate_profile: str | None = None
    warmup_on_startup: bool | None = None
    browser_launch_timeout_seconds: float | None = None
    page_load_timeout_seconds: int | None = None
    challenge_wait_seconds: float | None = None
    solve_timeout_seconds: int | None = None


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
        if "enabled" in data:
            legacy = data.pop("enabled")
            if legacy is not None:
                data.setdefault("tvmaze", {}).setdefault("enabled", legacy)
                data.setdefault("cinemeta", {}).setdefault("enabled", legacy)
        return data


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
        if "enabled" in data:
            legacy_master = data.pop("enabled")
            if legacy_master:
                native = data.setdefault("native", {}) or {}
                if isinstance(native, dict):
                    native.setdefault("enabled", True)
                    data["native"] = native
        return data


# --- Subtitles ---
class NativeSubtitleSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    scan_interval_hours: int | None = None


class BazarrSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    url: str | None = None
    api_key: str | None = None


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
        if "enabled" in data:
            legacy_master = data.pop("enabled")
            if legacy_master is False:
                native = data.get("native") or {}
                if isinstance(native, dict):
                    native.setdefault("enabled", False)
                    data["native"] = native
                bazarr = data.get("bazarr") or {}
                if isinstance(bazarr, dict):
                    bazarr.setdefault("enabled", False)
                    data["bazarr"] = bazarr
        return data


# --- Updates ---
class UpdateSettingsSchema(SettingsSectionSchema):
    enabled: bool | None = None
    repo: str | None = None
    check_interval_hours: int | None = None
    include_prereleases: bool | None = None
    cache_ttl_seconds: int | None = None
    notify_on_new_version: bool | None = None
    allow_in_app_apply: bool | None = None
    image_repository: str | None = None
    image_tag: str | None = None
    container_name: str | None = None


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
    subtitles: dict
    updates: dict
    cloudflare: dict
    imports: dict
    overrides: dict  # The raw DB overrides, so the UI can show what's been changed
    defaults: dict | None = (
        None  # TOML-only defaults, used for "Reset to default" tooltips
    )
