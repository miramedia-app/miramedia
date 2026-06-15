from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings


class ProviderConfig(BaseSettings):
    enabled: bool = False
    username: str = ""
    password: str = ""
    api_key: str = ""


class NativeSubtitleConfig(BaseSettings):
    enabled: bool = True
    scan_interval_hours: int = 6

    # Free providers (no credentials needed)
    gestdown: ProviderConfig = ProviderConfig(enabled=True)
    tvsubtitles: ProviderConfig = ProviderConfig(enabled=True)
    yifysubtitles: ProviderConfig = ProviderConfig(enabled=True)

    # Keyless plugin providers (vendored from LavX/bazarr-provider-catalog,
    # adapted in miramedia/subtitles/plugins). Pure scrapers, no account.
    subtitlecat: ProviderConfig = ProviderConfig(enabled=True)
    subf2m: ProviderConfig = ProviderConfig(enabled=True)
    isubtitles: ProviderConfig = ProviderConfig(enabled=True)
    my_subs: ProviderConfig = ProviderConfig(enabled=True)
    # Extracts subtitles embedded in the local video file via ffmpeg.
    embeddedsubtitles: ProviderConfig = ProviderConfig(enabled=True)

    # Account-required providers (disabled by default — require credentials)
    opensubtitlescom: ProviderConfig = ProviderConfig()
    addic7ed: ProviderConfig = ProviderConfig()
    subdl: ProviderConfig = ProviderConfig()
    subsource: ProviderConfig = ProviderConfig()

    # Language-specific providers
    napiprojekt: ProviderConfig = ProviderConfig()
    subtis: ProviderConfig = ProviderConfig()
    subtitulamos: ProviderConfig = ProviderConfig()

    @model_validator(mode="before")
    @classmethod
    def drop_removed_providers(cls, data: Any) -> Any:  # noqa: ANN401 — pydantic before-validator accepts/returns arbitrary input
        """Drop config sections for providers we no longer support.

        ``bsplayer`` (dead API servers) and the built-in ``opensubtitles``
        (legacy XML-RPC, anonymous login now returns Unauthorized) were
        removed. Old config.toml files still carry their sections; strip them
        so loading doesn't fail on the now-unknown keys.
        """
        if isinstance(data, dict):
            for removed in ("bsplayer", "opensubtitles"):
                data.pop(removed, None)
        return data


class BazarrConfig(BaseSettings):
    enabled: bool = False
    url: str = ""
    api_key: str = ""


class SubtitleConfig(BaseSettings):
    desired_languages: list[str] = ["en"]
    native: NativeSubtitleConfig = NativeSubtitleConfig()
    bazarr: BazarrConfig = BazarrConfig()

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_master_enabled(cls, data: Any) -> Any:  # noqa: ANN401 — pydantic before-validator accepts/returns arbitrary input
        """Legacy ``subtitles.enabled`` master toggle is now derived from
        the backend sub-flags. Drop it on input; if it was explicitly False,
        flip native off so a legacy 'off' state survives the migration."""
        if not isinstance(data, dict):
            return data
        if "enabled" in data:
            legacy_master = data.pop("enabled")
            if legacy_master is False:
                native = data.get("native")
                if isinstance(native, dict):
                    native.setdefault("enabled", False)
                else:
                    data["native"] = {"enabled": False}
                bazarr = data.get("bazarr")
                if isinstance(bazarr, dict):
                    bazarr.setdefault("enabled", False)
                else:
                    data["bazarr"] = {"enabled": False}
        return data

    @property
    def enabled(self) -> bool:
        """Derived: subtitles are enabled if any backend is enabled."""
        return bool(self.native.enabled or self.bazarr.enabled)
