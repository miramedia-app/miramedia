"""Thin async HTTP client for the Overseerr / Jellyseerr API.

Only the surface MiraMedia needs for bidirectional request sync:
list/create/approve/decline/delete requests, resolve a TMDB id to a
title + IMDb id, and push availability back so Seerr stops showing the
item as outstanding.

Uses ``httpx.AsyncClient`` so calls don't block the event loop — every
method is ``async`` and must be awaited. Callers that need to dispose
of the client should ``await client.aclose()`` (or use ``async with``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Self

import httpx

log = logging.getLogger(__name__)

# Overseerr MediaRequest.status
SEERR_REQ_PENDING = 1
SEERR_REQ_APPROVED = 2
SEERR_REQ_DECLINED = 3

# Overseerr Media.status
SEERR_MEDIA_PARTIALLY_AVAILABLE = 4
SEERR_MEDIA_AVAILABLE = 5


@dataclass
class SeerrRequest:
    request_id: int
    media_id: int
    media_type: str  # "movie" | "tv"
    request_status: int
    media_status: int
    tmdb_id: int | None
    imdb_id: str | None
    seasons: list[int]


class SeerrError(RuntimeError):
    pass


class SeerrClient:
    def __init__(self, url: str, api_key: str, *, timeout: float = 15.0) -> None:
        base = (url or "").rstrip("/")
        if not base:
            msg = "Seerr url is not configured"
            raise SeerrError(msg)
        self._client = httpx.AsyncClient(
            base_url=f"{base}/api/v1",
            headers={"X-Api-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    # ---- low level -----------------------------------------------------

    async def _get(self, path: str, **params: object) -> dict:
        r = await self._client.get(path, params=params or None)
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, json: dict | None = None) -> dict:
        r = await self._client.post(path, json=json)
        r.raise_for_status()
        return r.json() if r.content else {}

    # ---- read ----------------------------------------------------------

    async def get_status(self) -> dict:
        return await self._get("/status")

    async def iter_requests(self, page_size: int = 50) -> list[SeerrRequest]:
        """Page through every request and normalise to SeerrRequest."""
        out: list[SeerrRequest] = []
        skip = 0
        while True:
            data = await self._get(
                "/request", take=page_size, skip=skip, sort="added", filter="all"
            )
            results = data.get("results", []) or []
            for raw in results:
                parsed = self._parse_request(raw)
                if parsed is not None:
                    out.append(parsed)
            page_info = data.get("pageInfo", {}) or {}
            pages = page_info.get("pages", 1) or 1
            page = page_info.get("page", 1) or 1
            if page >= pages or not results:
                break
            skip += page_size
        return out

    @staticmethod
    def _parse_request(raw: dict) -> SeerrRequest | None:
        media = raw.get("media") or {}
        media_id = media.get("id")
        request_id = raw.get("id")
        if media_id is None or request_id is None:
            return None
        seasons = [
            s.get("seasonNumber")
            for s in (raw.get("seasons") or [])
            if s.get("seasonNumber") is not None
        ]
        return SeerrRequest(
            request_id=int(request_id),
            media_id=int(media_id),
            media_type=str(raw.get("type") or media.get("mediaType") or "movie"),
            request_status=int(raw.get("status") or SEERR_REQ_PENDING),
            media_status=int(media.get("status") or 1),
            tmdb_id=media.get("tmdbId"),
            imdb_id=media.get("imdbId"),
            seasons=seasons,
        )

    async def find_tmdb_by_imdb(self, imdb_id: str) -> tuple[int, str] | None:
        """Resolve an IMDb id to (tmdb_id, media_type) via Seerr's TMDB proxy.

        Overseerr/Jellyseerr treats ``imdb:tt...`` as a special search
        pattern that performs TMDB's external-id lookup, so we get an exact
        match without needing our own TMDB API key.
        """
        if not imdb_id:
            return None
        try:
            data = await self._get("/search", query=f"imdb:{imdb_id}")
        except httpx.HTTPError:
            log.warning(
                "Seerr imdb->tmdb lookup failed for %s",
                imdb_id,
                exc_info=True,
            )
            return None
        for item in data.get("results", []) or []:
            media_type = item.get("mediaType")
            tmdb_id = item.get("id")
            if media_type in ("movie", "tv") and tmdb_id is not None:
                return (int(tmdb_id), media_type)
        return None

    async def resolve_title_imdb(
        self, media_type: str, tmdb_id: int
    ) -> tuple[str, str | None]:
        """Look up display title + IMDb id via Seerr's TMDB proxy."""
        path = "/movie" if media_type == "movie" else "/tv"
        try:
            data = await self._get(f"{path}/{tmdb_id}")
        except httpx.HTTPError:
            log.warning(
                "Seerr title lookup failed for %s/%s",
                media_type,
                tmdb_id,
                exc_info=True,
            )
            return (f"{media_type} {tmdb_id}", None)
        title = data.get("title") or data.get("name") or f"{media_type} {tmdb_id}"
        imdb_id = (data.get("externalIds") or {}).get("imdbId")
        return (str(title), imdb_id)

    # ---- write ---------------------------------------------------------

    async def create_request(
        self,
        media_type: str,
        tmdb_id: int,
        *,
        seasons: list[int] | None = None,
    ) -> SeerrRequest | None:
        body: dict = {"mediaType": media_type, "mediaId": tmdb_id}
        if media_type == "tv":
            body["seasons"] = seasons or "all"
        raw = await self._post("/request", json=body)
        return self._parse_request(raw)

    async def approve(self, request_id: int) -> None:
        await self._post(f"/request/{request_id}/approve")

    async def decline(self, request_id: int) -> None:
        await self._post(f"/request/{request_id}/decline")

    async def delete_request(self, request_id: int) -> None:
        r = await self._client.delete(f"/request/{request_id}")
        if r.status_code not in (200, 204, 404):
            r.raise_for_status()

    async def mark_media_available(self, media_id: int) -> None:
        await self._post(f"/media/{media_id}/available", json={"is4k": False})
