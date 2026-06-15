from pydantic import model_validator
from pydantic_settings import BaseSettings


class TvmazeConfig(BaseSettings):
    enabled: bool = True


class CinemetaConfig(BaseSettings):
    enabled: bool = True


class NativeMetadataConfig(BaseSettings):
    tvmaze: TvmazeConfig = TvmazeConfig()
    cinemeta: CinemetaConfig = CinemetaConfig()

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_enabled(cls, data: object) -> object:
        """Legacy TOML/init: `[metadata.native] enabled = X` → split into tvmaze + cinemeta."""
        if not isinstance(data, dict):
            return data
        if "enabled" in data:
            legacy = data.pop("enabled")
            if legacy is not None:
                data.setdefault("tvmaze", {}).setdefault("enabled", legacy)
                data.setdefault("cinemeta", {}).setdefault("enabled", legacy)
        return data

    @property
    def enabled(self) -> bool:
        return self.tvmaze.enabled or self.cinemeta.enabled


class TmdbConfig(BaseSettings):
    enabled: bool = False
    api_key: str = ""
    primary_languages: list[str] = []  # ISO 639-1 language codes
    default_language: str = "en"  # ISO 639-1 language codes


class TvdbConfig(BaseSettings):
    enabled: bool = False
    api_key: str = ""


class MetadataProviderConfig(BaseSettings):
    desired_languages: list[str] = ["en"]  # ISO 639-1 language codes
    check_interval_hours: int = 24
    # Transient provider/DNS failures are stamped and skipped for this long so
    # the scheduler does not hammer the same unreachable endpoint in a tight loop.
    failure_backoff_hours: int = 6
    native: NativeMetadataConfig = NativeMetadataConfig()
    tmdb: TmdbConfig = TmdbConfig()
    tvdb: TvdbConfig = TvdbConfig()
