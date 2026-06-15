from pydantic_settings import BaseSettings


class UpdateConfig(BaseSettings):
    enabled: bool = True
    repo: str = "miramedia-app/miramedia"
    check_interval_hours: int = 24
    include_prereleases: bool = False
    cache_ttl_seconds: int = 3600
    request_timeout_seconds: int = 10

    allow_in_app_apply: bool = False
    docker_socket_path: str = "/var/run/docker.sock"
    container_name: str = "miramedia"
    image_repository: str = "ghcr.io/miramedia-app/miramedia"
    image_tag: str = "latest"

    notify_on_new_version: bool = False
