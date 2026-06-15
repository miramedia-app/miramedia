from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from miramedia.indexers.sites.base import BaseSite

# Registry of all preloaded sites.
# Lazy imports to avoid import-time side effects.


def get_preloaded_sites() -> dict[str, type[BaseSite]]:
    from miramedia.indexers.sites.eztv import EztvSite
    from miramedia.indexers.sites.limetorrents import LimeTorrentsSite
    from miramedia.indexers.sites.nyaa import NyaaSite
    from miramedia.indexers.sites.thepiratebay import ThePirateBaySite
    from miramedia.indexers.sites.torrentgalaxy import TorrentGalaxySite
    from miramedia.indexers.sites.x1337 import X1337Site
    from miramedia.indexers.sites.yts import YtsSite

    return {
        "yts": YtsSite,
        "eztv": EztvSite,
        "thepiratebay": ThePirateBaySite,
        "1337x": X1337Site,
        "torrentgalaxy": TorrentGalaxySite,
        "limetorrents": LimeTorrentsSite,
        "nyaa": NyaaSite,
    }
