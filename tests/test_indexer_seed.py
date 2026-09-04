"""Tests for preloaded indexer site seeding."""

from __future__ import annotations

from unittest.mock import patch
from uuid import NAMESPACE_DNS, uuid5

import pytest

from miramedia.indexers.models import IndexerSite
from miramedia.indexers.schemas import IndexerSiteId
from miramedia.indexers.seed import _preloaded_id, seed_preloaded_sites
from miramedia.indexers.sites.bittorrented import BitTorrentedSite
from miramedia.indexers.sites.x1337 import X1337Site
from miramedia.indexers.sites.yts import YtsSite


class _FakeSession:
    def __init__(self) -> None:
        self._sites: dict[IndexerSiteId, IndexerSite] = {}
        self.added: list[IndexerSite] = []
        self.committed = False

    async def get(
        self, _model: type[IndexerSite], site_id: IndexerSiteId
    ) -> IndexerSite | None:
        return self._sites.get(site_id)

    def add(self, site: IndexerSite) -> None:
        self.added.append(site)
        self._sites[IndexerSiteId(site.id)] = site

    async def delete(self, site: IndexerSite) -> None:
        self._sites.pop(IndexerSiteId(site.id), None)

    async def commit(self) -> None:
        self.committed = True


def _seeded_urls(site_cls: type) -> list[str]:
    default_url = getattr(site_cls, "url", "")
    extra_urls = list(getattr(site_cls, "available_urls", []) or [])
    seeded: list[str] = []
    for url in [default_url, *extra_urls]:
        if url and url not in seeded:
            seeded.append(url)
    return seeded


@pytest.mark.anyio
async def test_new_install_enables_1337x_and_bittorrented() -> None:
    db = _FakeSession()
    with patch("miramedia.indexers.seed.invalidate_native_indexer"):
        await seed_preloaded_sites(db)  # type: ignore[arg-type]

    by_name = {site.name: site for site in db.added}
    assert by_name["1337x"].enabled is True
    assert by_name["bittorrented"].enabled is True
    assert by_name["1337x"].url == X1337Site.url
    assert by_name["bittorrented"].url == BitTorrentedSite.url
    assert by_name["1337x"].supports_tv is True
    assert by_name["bittorrented"].supports_movies is True
    assert by_name["1337x"].available_urls == _seeded_urls(X1337Site)
    assert db.committed is True


@pytest.mark.anyio
async def test_repeat_seeding_is_idempotent() -> None:
    db = _FakeSession()
    with patch("miramedia.indexers.seed.invalidate_native_indexer"):
        await seed_preloaded_sites(db)  # type: ignore[arg-type]
        first_count = len(db.added)
        await seed_preloaded_sites(db)  # type: ignore[arg-type]

    assert len(db.added) == first_count


@pytest.mark.anyio
async def test_existing_site_preserves_enabled_and_url() -> None:
    db = _FakeSession()
    site_id = _preloaded_id("1337x")
    existing = IndexerSite(
        id=site_id,
        name="1337x",
        site_type="native",
        url="https://custom-mirror.example",
        available_urls=["https://custom-mirror.example"],
        api_key="",
        supports_tv=True,
        supports_movies=True,
        categories_tv="",
        categories_movies="",
        cloudflare_protected=True,
        enabled=False,
        is_preloaded=True,
    )
    db._sites[site_id] = existing

    with patch("miramedia.indexers.seed.invalidate_native_indexer"):
        await seed_preloaded_sites(db)  # type: ignore[arg-type]

    assert existing.enabled is False
    assert existing.url == "https://custom-mirror.example"
    merged = list(existing.available_urls or [])
    for url in _seeded_urls(X1337Site):
        assert url in merged


@pytest.mark.anyio
async def test_existing_site_merges_new_mirror_urls_only() -> None:
    db = _FakeSession()
    site_id = _preloaded_id("1337x")
    existing = IndexerSite(
        id=site_id,
        name="1337x",
        site_type="native",
        url="https://1337x.to",
        available_urls=["https://1337x.to"],
        api_key="",
        supports_tv=True,
        supports_movies=True,
        categories_tv="",
        categories_movies="",
        cloudflare_protected=True,
        enabled=True,
        is_preloaded=True,
    )
    db._sites[site_id] = existing

    with patch("miramedia.indexers.seed.invalidate_native_indexer"):
        await seed_preloaded_sites(db)  # type: ignore[arg-type]

    assert existing.available_urls == _seeded_urls(X1337Site)


@pytest.mark.anyio
async def test_existing_site_keeps_disabled_and_reordered_mirrors() -> None:
    db = _FakeSession()
    site_id = _preloaded_id("1337x")
    # User disabled 1337x.to and reordered 1337x.st to the front.
    existing = IndexerSite(
        id=site_id,
        name="1337x",
        site_type="native",
        url="https://1337x.st",
        available_urls=["https://1337x.st"],
        mirrors=[
            {"url": "https://1337x.st", "enabled": True, "source": "seeded"},
            {"url": "https://1337x.to", "enabled": False, "source": "seeded"},
            {"url": "https://my-mirror.example", "enabled": True, "source": "user"},
        ],
        api_key="",
        supports_tv=True,
        supports_movies=True,
        categories_tv="",
        categories_movies="",
        cloudflare_protected=True,
        enabled=True,
        is_preloaded=True,
    )
    db._sites[site_id] = existing

    with patch("miramedia.indexers.seed.invalidate_native_indexer"):
        await seed_preloaded_sites(db)  # type: ignore[arg-type]

    by_url = {m["url"]: m for m in existing.mirrors}
    # disable + reorder + user mirror all survive
    assert by_url["https://1337x.to"]["enabled"] is False
    assert by_url["https://my-mirror.example"]["source"] == "user"
    assert existing.mirrors[0]["url"] == "https://1337x.st"
    # new code mirrors were appended (enabled)
    assert "https://x1337x.eu" in by_url
    # disabled seeded mirror is excluded from the live list
    assert "https://1337x.to" not in existing.available_urls
    assert "https://my-mirror.example" in existing.available_urls


@pytest.mark.anyio
async def test_retired_site_row_is_deleted() -> None:
    db = _FakeSession()
    tg_id = _preloaded_id("torrentgalaxy")
    db._sites[tg_id] = IndexerSite(
        id=tg_id,
        name="torrentgalaxy",
        site_type="native",
        url="https://torrentgalaxy.one",
        available_urls=["https://torrentgalaxy.one"],
        api_key="",
        supports_tv=True,
        supports_movies=True,
        categories_tv="",
        categories_movies="",
        cloudflare_protected=False,
        enabled=True,
        is_preloaded=True,
    )

    with patch("miramedia.indexers.seed.invalidate_native_indexer"):
        await seed_preloaded_sites(db)  # type: ignore[arg-type]

    assert tg_id not in db._sites
    assert "torrentgalaxy" not in {s.name for s in db.added}


def test_preloaded_ids_are_deterministic() -> None:
    assert _preloaded_id("1337x") == IndexerSiteId(
        uuid5(NAMESPACE_DNS, "preloaded.1337x")
    )
    assert _preloaded_id("bittorrented") == IndexerSiteId(
        uuid5(NAMESPACE_DNS, "preloaded.bittorrented")
    )


@pytest.mark.anyio
async def test_new_install_seeds_yts_mirrors_without_overwriting_active_url() -> None:
    db = _FakeSession()
    with patch("miramedia.indexers.seed.invalidate_native_indexer"):
        await seed_preloaded_sites(db)  # type: ignore[arg-type]

    yts = next(site for site in db.added if site.name == "yts")
    assert yts.url == YtsSite.url
    assert yts.available_urls == _seeded_urls(YtsSite)
    assert "https://yts.gg" in yts.available_urls
