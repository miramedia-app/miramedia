from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from miramedia.indexers.sites.base import BaseSite

# Registry of all preloaded sites.
# Lazy imports to avoid import-time side effects.


def get_preloaded_sites() -> dict[str, type[BaseSite]]:
    from miramedia.indexers.sites.bittorrented import BitTorrentedSite
    from miramedia.indexers.sites.ext import ExtSite
    from miramedia.indexers.sites.eztv import EztvSite
    from miramedia.indexers.sites.limetorrents import LimeTorrentsSite
    from miramedia.indexers.sites.nyaa import NyaaSite
    from miramedia.indexers.sites.thepiratebay import ThePirateBaySite
    from miramedia.indexers.sites.torrentdownloads import TorrentDownloadsSite
    from miramedia.indexers.sites.x1337 import X1337Site
    from miramedia.indexers.sites.yts import YtsSite

    return {
        "yts": YtsSite,
        "eztv": EztvSite,
        "ext": ExtSite,
        "thepiratebay": ThePirateBaySite,
        "1337x": X1337Site,
        "bittorrented": BitTorrentedSite,
        "limetorrents": LimeTorrentsSite,
        "nyaa": NyaaSite,
        "torrentdownloads": TorrentDownloadsSite,
    }
