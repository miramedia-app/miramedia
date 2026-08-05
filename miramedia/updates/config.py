from __future__ import annotations

import logging
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)


class UpdateConfig(BaseSettings):
    enabled: bool = True
    repo: str = "miramedia-app/miramedia"
    check_interval_hours: int = 24
    include_prereleases: bool = False
    cache_ttl_seconds: int = 3600
    request_timeout_seconds: int = 10

    # Legacy in-app apply settings — accepted for compatibility but ignored.
    allow_in_app_apply: bool = False
    docker_socket_path: str = "/var/run/docker.sock"
    container_name: str = "miramedia"
    image_repository: str = "ghcr.io/miramedia-app/miramedia"
    image_tag: str = "latest"

    notify_on_new_version: bool = False

    @model_validator(mode="after")
    def warn_legacy_apply_settings(self) -> Self:
        if self.allow_in_app_apply:
            log.warning(
                "updates.allow_in_app_apply is set but in-app Docker apply is "
                "disabled; apply updates on the host with "
                "`docker compose pull && docker compose up -d`"
            )
        return self
