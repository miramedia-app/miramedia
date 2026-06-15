from __future__ import annotations

import logging

import requests
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout

log = logging.getLogger(__name__)


class BazarrClient:
    """Simple client for the Bazarr API."""

    def __init__(self, url: str, api_key: str) -> None:
        self.base_url = url.rstrip("/")
        self.api_key = api_key
        self.session = requests.Session()
        self.session.trust_env = False  # ignore HTTP_PROXY/HTTPS_PROXY env
        self.session.headers["X-API-KEY"] = api_key

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

    def search_episode_subtitles(self, episode_id: str) -> bool:
        """Trigger Bazarr to search subtitles for an episode."""
        return self._post(f"/episodes/{episode_id}/subtitles")

    def search_movie_subtitles(self, movie_id: str) -> bool:
        """Trigger Bazarr to search subtitles for a movie."""
        return self._post(f"/movies/{movie_id}/subtitles")

    def test_connection(self) -> bool:
        """Test if Bazarr is reachable."""
        result = self._get("/system/status")
        return result is not None
