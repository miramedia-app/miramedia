from pydantic import model_validator
from pydantic_settings import BaseSettings

from miramedia.settings.normalize import migrate_requests_section


class SeerrConfig(BaseSettings):
    enabled: bool = False
    url: str = ""
    api_key: str = ""


class NativeRequestsConfig(BaseSettings):
    enabled: bool = False  # Built-in requests fulfillment (no external service)


class RequestsConfig(BaseSettings):
    auto_approve_users: bool = False
    fulfill_interval_hours: int = 2
    native: NativeRequestsConfig = NativeRequestsConfig()
    seerr: SeerrConfig = SeerrConfig()

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, data: dict) -> dict:
        """Strip removed fields and migrate the old master toggle.

        - Drop ``auto_approve_superuser`` (superusers always auto-approve now).
        - Map legacy ``enabled = true`` → ``native.enabled = true`` (legacy
          installs implicitly used the built-in fulfillment path).
        """
        if not isinstance(data, dict):
            return data
        return migrate_requests_section(data)

    @property
    def enabled(self) -> bool:
        """Derived: requests are enabled if any backend is enabled."""
        return bool(self.native.enabled or self.seerr.enabled)
