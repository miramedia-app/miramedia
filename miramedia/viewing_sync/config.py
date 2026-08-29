from __future__ import annotations

from uuid import UUID

from pydantic import model_validator
from pydantic_settings import BaseSettings


class JellyfinViewingSyncConfig(BaseSettings):
    url: str = ""
    api_key: str = ""
    allow_private_network: bool = True
    allow_insecure_transport: bool = True
    timeout_seconds: int = 30
    # Jellyfin user GUID -> MiraMedia user.id (UUID string).
    user_map: dict[str, str] = {}


class ViewingSyncConfig(BaseSettings):
    """Connector-neutral viewing-state dry-run settings (design 386 Slice A)."""

    enabled: bool = False
    poll_interval_minutes: int = 60
    retention_days: int = 14
    retention_min_rows: int = 5000
    jellyfin: JellyfinViewingSyncConfig = JellyfinViewingSyncConfig()

    @staticmethod
    def validate_user_map(user_map: dict[str, str]) -> dict[str, UUID]:
        """Return jellyfin_user_id -> miramedia user UUID; abort on duplicate targets."""
        mapped: dict[str, UUID] = {}
        seen_targets: dict[UUID, str] = {}
        for jellyfin_id, miramedia_id in user_map.items():
            jellyfin_key = jellyfin_id.strip()
            if not jellyfin_key:
                continue
            try:
                target = UUID(str(miramedia_id).strip())
            except ValueError as exc:
                msg = (
                    f"invalid MiraMedia user id in jellyfin user_map: {miramedia_id!r}"
                )
                raise ValueError(msg) from exc
            if target in seen_targets:
                msg = (
                    "duplicate MiraMedia user in jellyfin user_map: "
                    f"{target} mapped from {seen_targets[target]!r} and {jellyfin_key!r}"
                )
                raise ValueError(msg)
            seen_targets[target] = jellyfin_key
            mapped[jellyfin_key] = target
        return mapped

    @model_validator(mode="after")
    def _check_user_map_when_enabled(self) -> ViewingSyncConfig:
        if self.enabled and self.jellyfin.user_map:
            self.validate_user_map(self.jellyfin.user_map)
        return self
