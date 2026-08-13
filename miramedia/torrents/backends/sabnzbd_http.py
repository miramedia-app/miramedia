"""Thin synchronous HTTP client for the SABnzbd API endpoints MiraMedia uses."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=None)


class SabnzbdApiError(RuntimeError):
    """SABnzbd API returned an error status (message excludes the request URL)."""


class SabnzbdHttpClient:
    """Sync SABnzbd API client for queue/add/version operations."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        api_key: str,
        base_path: str = "/api",
        verify_tls: bool = True,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not host.startswith(("http://", "https://")):
            msg = (
                "SABnzbd host must include a scheme (http:// or https://), "
                f'got {host!r} — set e.g. host = "http://localhost" in [torrents.sabnzbd]'
            )
            raise ValueError(msg)
        parsed = urllib.parse.urlsplit(f"{host.rstrip('/')}:{port}")
        if parsed.scheme == "http" and parsed.hostname not in (
            "localhost",
            "127.0.0.1",
            "::1",
        ):
            log.warning(
                "SABnzbd is configured over plaintext http to a non-loopback host; "
                "the API key is sent in the clear — prefer https"
            )
        self._api_url = f"{host.rstrip('/')}:{port}{base_path}"
        self._api_key = api_key
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            verify=verify_tls,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=False,
            transport=httpx.HTTPTransport(retries=1),
        )

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        query = {
            "apikey": self._api_key,
            "output": "json",
            **params,
        }
        query = {k: v for k, v in query.items() if v is not None}
        response = self._http.get(self._api_url, params=query)
        if response.is_redirect:
            message = (
                f"SABnzbd API returned a redirect ({response.status_code}) "
                f"for mode={params.get('mode')!r}; refusing to follow because the "
                "request carries the API key. Point [torrents.sabnzbd] host directly "
                "at SABnzbd (check scheme/port/base_path)."
            )
            raise SabnzbdApiError(message)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = (
                f"SABnzbd API error {exc.response.status_code} "
                f"for mode={params.get('mode')!r}"
            )
            raise SabnzbdApiError(message) from None
        try:
            payload = response.json()
        except ValueError:
            message = (
                f"SABnzbd API returned a non-JSON response for mode={params.get('mode')!r} "
                "(is the host/port/base_path pointing at SABnzbd's API?)"
            )
            raise SabnzbdApiError(message) from None
        if isinstance(payload, dict) and payload.get("status") is False:
            error_text = str(payload.get("error", "unknown error"))[:200]
            message = f"SABnzbd API rejected mode={params.get('mode')!r}: {error_text}"
            raise SabnzbdApiError(message)
        return payload

    def version(self) -> dict[str, Any]:
        return self._call({"mode": "version"})

    def add_uri(
        self,
        *,
        url: str = "",
        nzbname: str = "",
        password: str = "",
        cat: str = "*",
        script: list[str] | None = None,
        priority: int = 0,
        pp: int = 1,
    ) -> dict[str, Any]:
        return self._call(
            {
                "mode": "addurl",
                "name": url,
                "nzbname": nzbname,
                "password": password,
                "cat": cat,
                "script": script,
                "priority": priority,
                "pp": pp,
            }
        )

    def get_downloads(
        self,
        *,
        start: int | None = None,
        limit: int | None = None,
        search: str | None = None,
        category: str | list[str] | None = None,
        priority: int | str | list[str] | None = None,
        status: str | list[str] | None = None,
        nzo_ids: str | list[str] | None = None,
    ) -> dict[str, Any]:
        if isinstance(nzo_ids, list):
            nzo_ids = ",".join(nzo_ids)
        if isinstance(status, list):
            status = ",".join(status)
        if isinstance(category, list):
            category = ",".join(category)
        if isinstance(priority, list):
            priority = ",".join(priority)
        return self._call(
            {
                "mode": "queue",
                "start": start,
                "limit": limit,
                "search": search,
                "category": category,
                "priority": priority,
                "status": status,
                "nzo_ids": nzo_ids,
            }
        )

    def history(
        self,
        *,
        nzo_ids: str | list[str] | None = None,
        start: int | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if isinstance(nzo_ids, list):
            nzo_ids = ",".join(nzo_ids)
        return self._call(
            {
                "mode": "history",
                "start": start,
                "limit": limit,
                "nzo_ids": nzo_ids,
            }
        )

    def pause_job(self, *, nzo_id: str) -> dict[str, Any]:
        return self._call({"mode": "queue", "name": "pause", "value": nzo_id})

    def resume_job(self, *, nzo_id: str) -> dict[str, Any]:
        return self._call({"mode": "queue", "name": "resume", "value": nzo_id})

    def delete_job(
        self, *, nzo_id: str | list[str], delete_files: bool = False
    ) -> dict[str, Any]:
        value = nzo_id if isinstance(nzo_id, str) else ",".join(nzo_id)
        return self._call(
            {
                "mode": "queue",
                "name": "delete",
                "value": value,
                "del_files": 1 if delete_files else 0,
            }
        )
