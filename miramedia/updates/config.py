from __future__ import annotations

import logging

from pydantic import model_validator
from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)

_LEGACY_APPLY_KEYS = frozenset(
    {
        "allow_in_app_apply",
        "docker_socket_path",
        "container_name",
    }
)


class UpdateConfig(BaseSettings):
    enabled: bool = True
    repo: str = "miramedia-app/miramedia"
    check_interval_hours: int = 24
    include_prereleases: bool = False
    cache_ttl_seconds: int = 3600
    request_timeout_seconds: int = 10
    image_repository: str = "ghcr.io/miramedia-app/miramedia"
    image_tag: str = "latest"
    notify_on_new_version: bool = False

    @model_validator(mode="before")
    @classmethod
    def strip_legacy_apply_settings(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        present = sorted(k for k in _LEGACY_APPLY_KEYS if k in data)
        if present:
            log.warning(
                "Legacy in-app apply settings (%s) are ignored; apply updates "
                "on the host with `docker compose pull && docker compose up -d`",
                ", ".join(present),
            )
            for key in _LEGACY_APPLY_KEYS:
                data.pop(key, None)
        return data
