from __future__ import annotations

import logging
from typing import Self

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

log = logging.getLogger(__name__)

# Inbound webhook payload shapes verified against Bazarr master (2026-07):
# - Sonarr: https://github.com/morpheus65535/bazarr/blob/master/bazarr/api/webhooks/sonarr.py
# - Radarr: https://github.com/morpheus65535/bazarr/blob/master/bazarr/api/webhooks/radarr.py


class BazarrClient:
    """Simple client for the Bazarr API."""

    def __init__(self, url: str, api_key: str) -> None:
        self.base_url = url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.trust_env = False  # ignore HTTP_PROXY/HTTPS_PROXY env
        self.session.headers["X-API-KEY"] = api_key

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _get(self, path: str) -> dict | None:
        try:
            resp = self.session.get(f"{self.base_url}/api{path}", timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (RequestsConnectionError, Timeout) as e:
            log.warning(f"Bazarr unreachable for GET {path}: {e}")
            return None
        except Exception:
            log.exception(f"Bazarr API GET {path} failed")
            return None

    def _post(self, path: str, json: dict | None = None) -> bool:
        try:
            resp = self.session.post(
                f"{self.base_url}/api{path}", json=json or {}, timeout=30
            )
            resp.raise_for_status()
        except (RequestsConnectionError, Timeout) as e:
            log.warning(f"Bazarr unreachable for POST {path}: {e}")
            return False
        except Exception:
            log.exception(f"Bazarr API POST {path} failed")
            return False
        else:
            return True

    def notify_episode_files_imported(
        self, episode_file_ids: list[int], episode_ids: list[int]
    ) -> bool:
        """Push Bazarr's inbound Sonarr ``Download`` webhook for episode files."""
        return self._post(
            "/webhooks/sonarr",
            json={
                "eventType": "Download",
                "episodeFiles": [{"id": i} for i in episode_file_ids],
                "episodes": [{"id": i} for i in episode_ids],
            },
        )

    def notify_movie_file_imported(self, movie_file_id: int, movie_id: int) -> bool:
        """Push Bazarr's inbound Radarr ``Download`` webhook for a movie file."""
        return self._post(
            "/webhooks/radarr",
            json={
                "eventType": "Download",
                "movieFile": {"id": movie_file_id},
                "movie": {"id": movie_id},
            },
        )

    def test_connection(self) -> bool:
        """Test if Bazarr is reachable."""
        result = self._get("/system/status")
        return result is not None
