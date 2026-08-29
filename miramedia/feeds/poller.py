"""HTTP polling for Torznab/Newznab recent-search feeds."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from defusedxml import ElementTree as DefusedET
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import MiraMediaConfig
from miramedia.feeds.envelope import FeedTorznabParser
from miramedia.feeds.schemas import FeedEnvelope
from miramedia.indexers.models import IndexerSite

log = logging.getLogger(__name__)

_FEED_RESULT_LIMIT = 500
_PARSER = FeedTorznabParser()


@dataclass(frozen=True)
class FeedPollResult:
    envelopes: list[FeedEnvelope]
    http_error: str | None = None
    parse_error: str | None = None


def _is_retryable_status(status: int) -> bool:
    return status == 429 or status >= 500


class FeedPoller:
    def poll_jackett(self, indexer_key: str) -> FeedPollResult:
        cfg = MiraMediaConfig().indexers
        jackett = cfg.jackett
        url = f"{jackett.url.rstrip('/')}/api/v2.0/indexers/{indexer_key}/results/torznab/api"
        params = {
            "apikey": jackett.api_key,
            "t": "search",
            "extended": "1",
            "cat": "2000,5000",
            "limit": _FEED_RESULT_LIMIT,
        }
        return self._get_torznab(url, params, timeout=cfg.timeout_seconds)

    def poll_prowlarr(self, indexer_id: int) -> FeedPollResult:
        cfg = MiraMediaConfig().indexers
        prowlarr = cfg.prowlarr
        url = f"{prowlarr.url.rstrip('/')}/api/v1/indexer/{indexer_id}/newznab"
        headers = {"X-Api-Key": prowlarr.api_key}
        params = {
            "t": "search",
            "extended": "1",
            "cat": "2000,5000",
            "limit": _FEED_RESULT_LIMIT,
        }
        return self._get_newznab(
            url, params, headers=headers, timeout=cfg.timeout_seconds
        )

    def poll_torznab_site(self, site: IndexerSite) -> FeedPollResult:
        cfg = MiraMediaConfig().indexers
        cats = f"{site.categories_movies},{site.categories_tv}"
        params: dict[str, str | int] = {
            "t": "search",
            "extended": "1",
            "cat": cats,
            "limit": _FEED_RESULT_LIMIT,
        }
        if site.api_key:
            params["apikey"] = site.api_key
        return self._get_torznab(site.url, params, timeout=cfg.timeout_seconds)

    def _get_torznab(
        self, url: str, params: dict[str, str | int], timeout: int
    ) -> FeedPollResult:
        return self._get_newznab(url, params, headers=None, timeout=timeout)

    def _get_newznab(
        self,
        url: str,
        params: dict[str, str | int],
        headers: dict[str, str] | None,
        timeout: int,
    ) -> FeedPollResult:
        try:
            with requests.Session() as session:
                response = session.get(
                    url, params=params, headers=headers, timeout=timeout
                )
        except requests.RequestException as exc:
            return FeedPollResult(
                envelopes=[],
                http_error=f"{type(exc).__name__}",
            )
        if response.status_code != 200:
            if _is_retryable_status(response.status_code):
                return FeedPollResult(
                    envelopes=[],
                    http_error=f"HTTP {response.status_code}",
                )
            return FeedPollResult(
                envelopes=[],
                http_error=f"HTTP {response.status_code}",
            )
        try:
            envelopes = _PARSER.process_feed_search_result(response.content)
        except DefusedET.ParseError as exc:
            return FeedPollResult(
                envelopes=[],
                parse_error=str(exc),
            )
        return FeedPollResult(envelopes=envelopes)


def jackett_feed_indexer_keys() -> list[str]:
    indexers = MiraMediaConfig().indexers.jackett.indexers
    return [key for key in indexers if key and key.lower() != "all"]


def prowlarr_feed_indexer_ids() -> list[tuple[int, str]]:
    cfg = MiraMediaConfig().indexers.prowlarr
    url = f"{cfg.url.rstrip('/')}/api/v1/indexer"
    headers = {"X-Api-Key": cfg.api_key}
    timeout = MiraMediaConfig().indexers.timeout_seconds
    try:
        with requests.Session() as session:
            response = session.get(url, headers=headers, timeout=timeout)
        if response.status_code != 200:
            log.warning("Prowlarr indexer list failed: HTTP %s", response.status_code)
            return []
        indexers = response.json()
        return [(int(i["id"]), i.get("name", str(i["id"]))) for i in indexers]
    except Exception:
        log.exception("Prowlarr indexer list failed")
        return []


async def list_native_torznab_sites(db: AsyncSession) -> list[IndexerSite]:
    from sqlalchemy import select

    stmt = select(IndexerSite).where(
        IndexerSite.enabled.is_(True),
        IndexerSite.site_type == "torznab",
    )
    return list((await db.execute(stmt)).scalars().all())
