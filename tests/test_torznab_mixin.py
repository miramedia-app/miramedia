"""Tests for Torznab XML parsing hardening and naive-date handling."""

import pytest
from defusedxml.common import EntitiesForbidden

from miramedia.indexers.backends.torznab_mixin import TorznabMixin

_MINIMAL_USENET_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>Test NZB</title>
      <size>1048576</size>
      <enclosure url="http://example.com/file.nzb"
                 type="application/x-nzb"
                 length="1048576"/>
      <torznab:attr name="usenetdate" value="Mon, 01 Jan 2024 00:00:00 -0000"/>
    </item>
  </channel>
</rss>
"""

_MALICIOUS_ENTITY_XML = """\
<?xml version="1.0"?>
<!DOCTYPE rss [
  <!ENTITY xxe "expanded">
]>
<rss version="2.0">
  <channel></channel>
</rss>
"""


class _TorznabParser(TorznabMixin):
    pass


def test_naive_usenetdate_is_parsed_without_error():
    parser = _TorznabParser()
    results = parser.process_search_result(_MINIMAL_USENET_XML)

    assert len(results) == 1
    assert results[0].title == "Test NZB"
    assert results[0].usenet is True
    assert results[0].age > 0


def test_malicious_entity_xml_raises():
    parser = _TorznabParser()
    with pytest.raises(EntitiesForbidden):
        parser.process_search_result(_MALICIOUS_ENTITY_XML)
