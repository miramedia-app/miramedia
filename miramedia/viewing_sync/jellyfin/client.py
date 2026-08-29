"""Jellyfin UserData HTTP adapter (design 386 Slice A — read-only)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self, cast

import httpx

from miramedia.notifications.destination_policy import validate_webhook_url
from miramedia.viewing_sync.digest import payload_digest_for_event
from miramedia.viewing_sync.matcher import normalize_provider_ids
from miramedia.viewing_sync.redact import redact_secret_text
from miramedia.viewing_sync.schemas import ExternalViewingEvent, MediaKind

log = logging.getLogger(__name__)

_TICKS_PER_MS = 10_000
_PAGE_SIZE = 100
_MAX_ATTEMPTS = 3
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_RETRY_BACKOFF_S = (0.5, 1.0)

_ITEM_FIELDS = (
    "ProviderIds,UserData,ParentIndexNumber,IndexNumber,IndexNumberEnd,"
    "RunTimeTicks,SeriesId,Type,Name,ProductionYear,SeriesName"
)


class JellyfinError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class JellyfinUser:
    id: str
    name: str


class JellyfinClient:
    _api_key: str
    _client: httpx.Client

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        timeout_seconds: int = 30,
        allow_private_network: bool = True,
        allow_insecure_transport: bool = True,
    ) -> None:
        base = (url or "").rstrip("/")
        if not base:
            msg = "Jellyfin url is not configured"
            raise JellyfinError(msg)
        if not api_key:
            msg = "Jellyfin api_key is not configured"
            raise JellyfinError(msg)
        decision = validate_webhook_url(
            base,
            allow_private_network=allow_private_network,
            allow_insecure_transport=allow_insecure_transport,
        )
        if not decision.allowed:
            reason = decision.reason.value if decision.reason else "rejected"
            msg = f"Jellyfin url rejected: {reason}"
            raise JellyfinError(msg)
        self._api_key = api_key
        self._client = httpx.Client(
            base_url=base,
            headers={
                "Accept": "application/json",
                "Authorization": _auth_header(api_key),
            },
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def list_users(self) -> list[JellyfinUser]:
        data = self._request("GET", "/Users")
        users: list[JellyfinUser] = []
        for raw in data if isinstance(data, list) else []:
            user_id = raw.get("Id")
            if not user_id:
                continue
            users.append(JellyfinUser(id=str(user_id), name=str(raw.get("Name") or "")))
        return users

    def iter_user_items(
        self,
        user_id: str,
        *,
        min_last_played_date: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, str | int | bool] = {
            "userId": user_id,
            "recursive": "true",
            "includeItemTypes": "Movie,Episode",
            "fields": _ITEM_FIELDS,
            "enableUserData": "true",
            "limit": _PAGE_SIZE,
        }
        if min_last_played_date is not None:
            params["minLastPlayedDate"] = min_last_played_date.astimezone(
                UTC
            ).isoformat()

        items: list[dict[str, Any]] = []
        start_index = 0
        while True:
            params["startIndex"] = start_index
            page = self._request("GET", "/Items", params=params)
            page_dict = cast(dict[str, Any], page)
            batch = page_dict.get("Items", []) if isinstance(page_dict, dict) else []
            if not batch:
                break
            items.extend(batch)
            total = int(page_dict.get("TotalRecordCount") or 0)
            start_index += len(batch)
            if start_index >= total:
                break
        return items

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int | bool] | None = None,
    ) -> object:
        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = self._client.request(method, path, params=params)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt + 1 < _MAX_ATTEMPTS:
                    time.sleep(
                        _RETRY_BACKOFF_S[min(attempt, len(_RETRY_BACKOFF_S) - 1)]
                    )
                    continue
                msg = redact_secret_text(str(exc), api_key=self._api_key)
                raise JellyfinError(msg) from exc
            if (
                response.status_code in _RETRYABLE_STATUS
                and attempt + 1 < _MAX_ATTEMPTS
            ):
                time.sleep(_RETRY_BACKOFF_S[min(attempt, len(_RETRY_BACKOFF_S) - 1)])
                continue
            if response.status_code >= 400:
                msg = redact_secret_text(
                    f"HTTP {response.status_code}",
                    api_key=self._api_key,
                )
                raise JellyfinError(msg)
            if not response.content:
                return None
            return response.json()
        if last_error is not None:
            msg = redact_secret_text(str(last_error), api_key=self._api_key)
            raise JellyfinError(msg) from last_error
        msg = "Jellyfin request failed"
        raise JellyfinError(msg)


def _auth_header(api_key: str) -> str:
    return (
        'MediaBrowser Client="MiraMedia", Device="MiraMedia", '
        'DeviceId="miramedia-viewing-sync", Version="1.0.0", '
        f'Token="{api_key}"'
    )


def _parse_remote_at(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _has_play_signal(user_data: dict[str, Any]) -> bool:
    if user_data.get("LastPlayedDate"):
        return True
    if int(user_data.get("PlayCount") or 0) > 0:
        return True
    ticks = int(user_data.get("PlaybackPositionTicks") or 0)
    return ticks > 0


def jellyfin_item_to_event(
    raw: dict[str, Any],
    *,
    connector_user_id: str,
) -> ExternalViewingEvent | None:
    user_data = raw.get("UserData") or {}
    if not _has_play_signal(user_data):
        return None

    item_type = str(raw.get("Type") or "")
    if item_type == "Movie":
        media_kind = MediaKind.movie
    elif item_type == "Episode":
        media_kind = MediaKind.episode
    else:
        return None

    ticks = int(user_data.get("PlaybackPositionTicks") or 0)
    runtime_ticks = int(raw.get("RunTimeTicks") or 0)
    position_ms = ticks // _TICKS_PER_MS
    duration_ms = runtime_ticks // _TICKS_PER_MS

    event = ExternalViewingEvent(
        connector="jellyfin",
        connector_user_id=connector_user_id,
        connector_item_id=str(raw.get("Id") or ""),
        media_kind=media_kind,
        provider_ids=normalize_provider_ids(raw.get("ProviderIds") or {}),
        season_number=_optional_int(raw.get("ParentIndexNumber")),
        episode_number=_optional_int(raw.get("IndexNumber")),
        episode_number_end=_optional_int(raw.get("IndexNumberEnd")),
        position_ms=position_ms,
        duration_ms=duration_ms,
        remote_played=bool(user_data.get("Played")),
        remote_at=_parse_remote_at(user_data.get("LastPlayedDate")),
        payload_digest="",
        title=str(raw.get("Name") or ""),
        year=_optional_int(raw.get("ProductionYear")),
        series_name=str(raw.get("SeriesName") or "") or None,
        play_count=int(user_data.get("PlayCount") or 0),
    )
    return _with_digest(event)


def _with_digest(event: ExternalViewingEvent) -> ExternalViewingEvent:
    return ExternalViewingEvent(
        connector=event.connector,
        connector_user_id=event.connector_user_id,
        connector_item_id=event.connector_item_id,
        media_kind=event.media_kind,
        provider_ids=event.provider_ids,
        season_number=event.season_number,
        episode_number=event.episode_number,
        episode_number_end=event.episode_number_end,
        position_ms=event.position_ms,
        duration_ms=event.duration_ms,
        remote_played=event.remote_played,
        remote_at=event.remote_at,
        payload_digest=payload_digest_for_event(event),
        title=event.title,
        year=event.year,
        series_name=event.series_name,
        play_count=event.play_count,
    )


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
