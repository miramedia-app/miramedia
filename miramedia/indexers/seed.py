"""Seed preloaded indexer sites into the database on startup."""

import logging
from uuid import NAMESPACE_DNS, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.indexers.backends.native import invalidate_native_indexer
from miramedia.indexers.mirror_state import (
    derive_available_urls,
    load_entries,
    mirrors_from_urls,
    reconcile_seeded,
)
from miramedia.indexers.models import IndexerSite
from miramedia.indexers.schemas import IndexerSiteId
from miramedia.indexers.sites import get_preloaded_sites

log = logging.getLogger(__name__)


def _preloaded_id(name: str) -> IndexerSiteId:
    return IndexerSiteId(uuid5(NAMESPACE_DNS, f"preloaded.{name}"))


# Preloaded sites that were removed from the code. Their rows are deleted on
# startup so a retired scraper doesn't linger in the UI as an inert entry.
_RETIRED_PRELOADED = ("torrentgalaxy",)


async def seed_preloaded_sites(db: AsyncSession) -> None:
    """
    Ensure every preloaded site definition exists in the database.

    - New sites are inserted with defaults from the code definition.
    - Existing sites are NOT overwritten (user changes are preserved).
    - If the code definition's URL isn't in the site's available_urls yet,
      it gets appended (so new mirror URLs propagate on upgrade).
    """
    preloaded = get_preloaded_sites()

    for name, site_cls in preloaded.items():
        site_id = _preloaded_id(name)
        existing = await db.get(IndexerSite, site_id)

        default_url = getattr(site_cls, "url", "")
        # Optional class attr: additional mirror URLs to expose alongside `url`
        extra_urls = list(getattr(site_cls, "available_urls", []) or [])
        # De-dup, preserve order, ensure default_url is first
        seeded_urls: list[str] = []
        for u in [default_url, *extra_urls]:
            if u and u not in seeded_urls:
                seeded_urls.append(u)

        if existing is None:
            mirrors = mirrors_from_urls(seeded_urls, default_url, source="seeded")
            site = IndexerSite(
                id=site_id,
                name=name,
                site_type="native",
                url=default_url,
                available_urls=derive_available_urls(mirrors),
                mirrors=[m.model_dump() for m in mirrors],
                api_key="",
                supports_tv=getattr(site_cls, "supports_tv", True),
                supports_movies=getattr(site_cls, "supports_movies", True),
                categories_tv="",
                categories_movies="",
                cloudflare_protected=getattr(site_cls, "cloudflare_protected", False),
                enabled=getattr(site_cls, "default_enabled", True),
                is_preloaded=True,
            )
            db.add(site)
            log.info("Seeded preloaded indexer site: %s", name)
        else:
            # Reconcile the structured mirror list: append genuinely-new code
            # mirrors, (re)classify source, but keep the user's order and
            # enabled/disabled choices. A seeded mirror the user disabled is NOT
            # re-enabled, and a mirror the user reordered keeps its position.
            before = load_entries(
                existing.mirrors, existing.available_urls, existing.url
            )
            reconciled = reconcile_seeded(before, seeded_urls)
            existing.mirrors = [m.model_dump() for m in reconciled]
            existing.available_urls = derive_available_urls(reconciled)
            added = [m.url for m in reconciled if m.url not in {e.url for e in before}]
            if added:
                log.info("Added new mirror URL(s) for %s: %s", name, added)

    for name in _RETIRED_PRELOADED:
        retired = await db.get(IndexerSite, _preloaded_id(name))
        if retired is not None:
            await db.delete(retired)
            log.info("Removed retired preloaded indexer site: %s", name)

    await db.commit()
    invalidate_native_indexer()
    log.debug("Preloaded indexer site seeding complete")
