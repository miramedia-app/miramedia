"""Tests for feed envelope parsing from Torznab XML."""

from datetime import UTC, datetime

from miramedia.feeds.envelope import FeedTorznabParser
from miramedia.feeds.redact import redact_download_url

_TORZNAB_FEED_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>Test Movie 2020 1080p</title>
      <guid isPermaLink="false">abc-guid-123</guid>
      <pubDate>Mon, 15 Jan 2024 12:00:00 +0000</pubDate>
      <size>2147483648</size>
      <category>2000</category>
      <enclosure url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&amp;dn=test"
                 type="application/x-bittorrent"
                 length="2147483648"/>
      <torznab:attr name="seeders" value="42"/>
      <torznab:attr name="imdbid" value="tt1234567"/>
      <torznab:attr name="infohash" value="0123456789abcdef0123456789abcdef01234567"/>
    </item>
  </channel>
</rss>
"""


def test_feed_envelope_preserves_guid_pubdate_infohash():
    parser = FeedTorznabParser()
    envelopes = parser.process_feed_search_result(_TORZNAB_FEED_XML)
    assert len(envelopes) == 1
    envelope = envelopes[0]
    assert envelope.provider_guid == "abc-guid-123"
    assert envelope.pub_date is not None
    assert envelope.pub_date.tzinfo is not None
    assert envelope.info_hash == "0123456789abcdef0123456789abcdef01234567"
    assert envelope.imdb_id == "tt1234567"
    assert envelopes[0].result.title == "Test Movie 2020 1080p"
    assert envelopes[0].result.seeders == 42


def test_redact_download_url_strips_apikey():
    url = "http://tracker.example/torrent?apikey=secret123&passkey=abc"
    redacted = redact_download_url(url)
    assert "secret123" not in redacted
    assert "abc" not in redacted or "<redacted>" in redacted
    assert "apikey" in redacted


def test_future_pubdate_clamped():
    parser = FeedTorznabParser()
    future = datetime.now(UTC).replace(year=2099)
    xml = _TORZNAB_FEED_XML.replace(
        "Mon, 15 Jan 2024 12:00:00 +0000",
        future.strftime("%a, %d %b %Y %H:%M:%S +0000"),
    )
    envelope = parser.process_feed_search_result(xml)[0]
    assert envelope.pub_date is not None
    assert envelope.pub_date <= datetime.now(UTC)
