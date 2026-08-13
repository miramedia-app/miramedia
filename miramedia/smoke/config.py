"""Minimal config.toml writer for the disposable smoke stack."""

from __future__ import annotations

from pathlib import Path


def write_smoke_config(
    *,
    config_dir: Path,
    frontend_url: str,
    database_host: str,
    database_port: int,
    database_user: str,
    database_password: str,
    database_name: str,
    data_root: Path,
    token_secret: str,
) -> Path:
    """Write a throwaway config that never enables bootstrap admin emails."""
    config_dir.mkdir(parents=True, exist_ok=True)
    for subdir in ("images", "shows", "movies", "torrents"):
        (data_root / subdir).mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.toml"
    config_path.write_text(
        f"""\
[misc]
frontend_url = "{frontend_url}"
cors_urls = ["{frontend_url}"]
development = true
image_directory = "{data_root / "images"}"
show_directory = "{data_root / "shows"}"
movie_directory = "{data_root / "movies"}"
torrent_directory = "{data_root / "torrents"}"
completed_torrent_path = ""
incomplete_torrent_path = ""

[database]
host = "{database_host}"
port = {database_port}
user = "{database_user}"
password = "{database_password}"
dbname = "{database_name}"

[auth]
token_secret = "{token_secret}"
email_password_resets = false

[torrents.native]
enabled = false

[indexers.native]
enabled = false

[metadata.native]
enabled = false

[cloudflare]
enabled = false

[notifications.native]
enabled = false
""",
        encoding="utf-8",
    )
    return config_path
