"""Parse Torznab/Newznab RSS items into feed envelopes."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element

import libtorrent
from defusedxml import ElementTree as DefusedET

from miramedia.feeds.schemas import FeedEnvelope
from miramedia.indexers.backends.torznab_mixin import TorznabMixin
from miramedia.indexers.schemas import IndexerQueryResult

log = logging.getLogger(__name__)

_XMLNS = {
    "torznab": "http://torznab.com/schemas/2015/feed",
    "atom": "http://www.w3.org/2005/Atom",
}


def _parse_pub_date(item: Element) -> datetime | None:
    pub = item.find("pubDate")
    if pub is not None and pub.text:
        try:
            dt = parsedate_to_datetime(pub.text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            return None
        else:
            return dt
    return None


def _clamp_future_pub_date(pub_date: datetime | None) -> datetime | None:
    if pub_date is None:
        return None
    now = datetime.now(UTC)
    if pub_date > now:
        return now
    return pub_date


def _attr_map(item: Element, is_usenet: bool) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for attribute in item.findall("torznab:attr", _XMLNS):
        name = attribute.attrib.get("name", "").lower()
        value = attribute.attrib.get("value", "")
        attrs[name] = value
        if is_usenet and name == "usenetdate" and "pub_date" not in attrs:
            try:
                posted = parsedate_to_datetime(value)
                if posted.tzinfo is None:
                    posted = posted.replace(tzinfo=UTC)
                attrs["pub_date"] = posted.isoformat()
            except (TypeError, ValueError):
                pass
    return attrs


def _info_hash_from_magnet(url: str) -> str | None:
    if not url.startswith("magnet:"):
        return None
    try:
        return str(libtorrent.parse_magnet_uri(url).info_hash).lower()
    except Exception:
        return None


class FeedTorznabParser(TorznabMixin):
    """Extends TorznabMixin to preserve GUID/IDs for feed observation."""

    def process_feed_search_result(self, xml: str | bytes) -> list[FeedEnvelope]:
        envelopes: list[FeedEnvelope] = []
        xml_tree = DefusedET.fromstring(xml)
        for item in xml_tree.findall("channel/item"):
            try:
                envelope = self._parse_feed_item(item)
                if envelope is not None:
                    envelopes.append(envelope)
            except Exception:
                log.exception("Feed Torznab item parse failed")
        return envelopes

    def _parse_feed_item(self, item: Element) -> FeedEnvelope | None:
        flags: list[str] = []
        seeders = 0
        age = 0
        indexer_name = "unknown"

        jackett = item.find("jackettindexer")
        if jackett is not None and jackett.text:
            indexer_name = jackett.text
        prowlarr = item.find("prowlarrindexer")
        if prowlarr is not None and prowlarr.text:
            indexer_name = prowlarr.text

        enclosure = item.find("enclosure")
        if enclosure is None or enclosure.attrib.get("url") is None:
            log.warning("Torznab feed item missing enclosure, skipping")
            return None

        is_usenet = enclosure.attrib.get("type") != "application/x-bittorrent"
        attrs = _attr_map(item, is_usenet)

        if is_usenet:
            if "usenetdate" in attrs:
                try:
                    posted_date = parsedate_to_datetime(attrs["usenetdate"])
                    if posted_date.tzinfo is None:
                        posted_date = posted_date.replace(tzinfo=UTC)
                    age = int((datetime.now(UTC) - posted_date).total_seconds())
                except (TypeError, ValueError):
                    pass
        else:
            if attrs.get("seeders"):
                try:
                    seeders = int(attrs["seeders"])
                except ValueError:
                    seeders = 0
            download_volume_factor = attrs.get("downloadvolumefactor")
            if download_volume_factor is not None:
                dvf = float(download_volume_factor)
                if dvf == 0:
                    flags.append("freeleech")
                elif dvf == 0.5:
                    flags.append("halfleech")
                elif dvf == 0.75:
                    flags.append("freeleech75")
                elif dvf == 0.25:
                    flags.append("freeleech25")
            upload_volume_factor = attrs.get("uploadvolumefactor")
            if upload_volume_factor is not None:
                try:
                    if int(upload_volume_factor) == 2:
                        flags.append("doubleupload")
                except ValueError:
                    pass

        title_el = item.find("title")
        title = title_el.text if title_el is not None else "unknown"
        size_str = item.find("size")
        if size_str is None or size_str.text is None:
            log.warning("Torznab feed item %s has no size, skipping", title)
            return None
        try:
            size = int(size_str.text or "0")
        except ValueError:
            log.warning("Torznab feed item %s has invalid size, skipping", title)
            return None

        download_url = str(enclosure.attrib["url"])
        result = IndexerQueryResult(
            title=title or "unknown",
            download_url=download_url,
            seeders=seeders,
            flags=flags,
            size=size,
            usenet=is_usenet,
            age=age,
            indexer=indexer_name,
        )

        guid_el = item.find("guid")
        provider_guid = guid_el.text if guid_el is not None and guid_el.text else None

        pub_date = _parse_pub_date(item)
        if pub_date is None and attrs.get("pub_date"):
            try:
                pub_date = datetime.fromisoformat(attrs["pub_date"])
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=UTC)
            except ValueError:
                pub_date = None
        pub_date = _clamp_future_pub_date(pub_date)

        info_hash = attrs.get("infohash") or attrs.get("info_hash")
        if info_hash:
            info_hash = info_hash.lower()
        else:
            info_hash = _info_hash_from_magnet(download_url)

        categories = [cat.text for cat in item.findall("category") if cat.text]
        if attrs.get("category"):
            categories.append(attrs["category"])

        imdb_id = attrs.get("imdb") or attrs.get("imdbid")
        tmdb_id = attrs.get("tmdb") or attrs.get("tmdbid")
        tvdb_id = attrs.get("tvdb") or attrs.get("tvdbid")

        return FeedEnvelope(
            result=result,
            provider_guid=provider_guid,
            pub_date=pub_date,
            info_hash=info_hash,
            imdb_id=imdb_id,
            tmdb_id=tmdb_id,
            tvdb_id=tvdb_id,
            categories=categories,
        )
