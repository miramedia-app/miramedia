"""Parsing and request-flow tests for the EXT native indexer."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from selectolax.parser import HTMLParser, Node

from miramedia.indexers.sites.ext import UNKNOWN_AGE_DAYS, ExtSite

FIXTURE = Path(__file__).parent / "fixtures" / "indexer_sites" / "ext.html"
NOW = datetime(2026, 9, 4, tzinfo=UTC)


def _row(html: str) -> Node:
    row = HTMLParser(f"<table><tbody>{html}</tbody></table>").css_first("tr")
    assert row is not None
    return row


def _result_row(
    *,
    title_html: str,
    age_html: str,
    torrent_id: int = 101,
) -> str:
    return f"""
    <tr>
      <td><a class="torrent-title-link" href="/x-{torrent_id}/">{title_html}</a></td>
      <td><div class="add-block-wrapper"><span class="add-block">Size</span><span>1.5 GB</span></div></td>
      <td><div class="add-block-wrapper"><span class="add-block">Age</span>{age_html}</div></td>
      <td><div class="add-block-wrapper"><span class="add-block">Seeds</span><span>42</span></div></td>
      <td><a class="search-magnet-btn" data-id="{torrent_id}"></a></td>
    </tr>
    """


def test_parse_ext_result_row() -> None:
    site = ExtSite()
    row = HTMLParser(FIXTURE.read_text()).css_first("tbody tr")

    assert row is not None
    assert site._parse_row(row, now=NOW) == {
        "torrent_id": 101,
        "title": "Show Name S01E01 1080p",
        "seeders": 42,
        "size": int(1.5 * 1024**3),
        "age": 3,
    }


def test_parse_title_inserts_spaces_around_highlight_spans() -> None:
    site = ExtSite()
    parsed = site._parse_row(
        _row(
            _result_row(
                title_html="<b>The Ultimate<span>Ubuntu</span>Handbook</b>",
                age_html='<span title="1 September 2026">3 days ago</span>',
            )
        ),
        now=NOW,
    )
    assert parsed is not None
    assert parsed["title"] == "The Ultimate Ubuntu Handbook"


def test_parse_title_does_not_pad_punctuation_after_highlight() -> None:
    site = ExtSite()
    parsed = site._parse_row(
        _row(
            _result_row(
                title_html="<b><span>Ubuntu</span>: The Complete Guide</b>",
                age_html='<span title="1 September 2026">3 days ago</span>',
            )
        ),
        now=NOW,
    )
    assert parsed is not None
    assert parsed["title"] == "Ubuntu: The Complete Guide"


def test_parse_age_prefers_absolute_date_in_title() -> None:
    site = ExtSite()
    parsed = site._parse_row(
        _row(
            _result_row(
                title_html="Ubuntu Guide",
                age_html='<span title="15 January 2022">4 years ago</span>',
            )
        ),
        now=NOW,
    )
    assert parsed is not None
    assert parsed["age"] == (NOW.date() - datetime(2022, 1, 15, tzinfo=UTC).date()).days


def test_parse_age_from_relative_units_when_date_missing() -> None:
    site = ExtSite()
    parsed = site._parse_row(
        _row(
            _result_row(
                title_html="Ubuntu Guide",
                age_html="<span>8 months ago</span>",
            )
        ),
        now=NOW,
    )
    assert parsed is not None
    assert parsed["age"] == 8 * 30


def test_parse_unknown_age_does_not_score_as_new() -> None:
    site = ExtSite()
    parsed = site._parse_row(
        _row(
            _result_row(
                title_html="Ubuntu Guide",
                age_html="<span>n/a</span>",
            )
        ),
        now=NOW,
    )
    assert parsed is not None
    assert parsed["age"] == UNKNOWN_AGE_DAYS


def test_search_uses_page_tokens_to_fetch_magnets() -> None:
    site = ExtSite()
    html = FIXTURE.read_text()
    magnets = {
        101: "magnet:?xt=urn:btih:" + "a" * 40,
        202: "magnet:?xt=urn:btih:" + "b" * 40,
    }

    with (
        patch.object(
            site,
            "_fetch_search_page",
            return_value=(html, object(), "https://extto.com"),
        ),
        patch.object(
            site,
            "_fetch_magnet",
            side_effect=lambda _session, _origin, torrent_id, _csrf, _token: magnets[
                torrent_id
            ],
        ),
    ):
        results = site._search("show name")

    assert [result.title for result in results] == [
        "Show Name S01E01 1080p",
        "Movie Name 2026 2160p",
    ]
    assert [result.download_url for result in results] == list(magnets.values())
    assert results[0].seeders == 42
    assert results[0].size == int(1.5 * 1024**3)
    assert all(result.indexer == "ext" for result in results)


def test_search_returns_nothing_when_tokens_are_missing() -> None:
    site = ExtSite()
    html = "<table><tbody><tr><td>no tokens</td></tr></tbody></table>"

    with patch.object(
        site,
        "_fetch_search_page",
        return_value=(html, object(), "https://extto.com"),
    ):
        assert site._search("missing") == []


def test_fetch_magnet_signs_request_and_sends_csrf_header() -> None:
    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, object]:
            return {"success": True, "url": "magnet:?xt=urn:btih:" + "c" * 40}

    class Session:
        kwargs: dict[str, object]

        def post(self, _url: str, **kwargs: object) -> Response:
            self.kwargs = kwargs
            return Response()

    session = Session()
    site = ExtSite()
    with patch("miramedia.indexers.sites.ext.time.time", return_value=1_788_537_531):
        magnet = site._fetch_magnet(
            session, "https://extto.com", 101, "csrf-value", "page-value"
        )

    assert magnet == "magnet:?xt=urn:btih:" + "c" * 40
    data = session.kwargs["data"]
    headers = session.kwargs["headers"]
    assert isinstance(data, dict)
    assert isinstance(headers, dict)
    assert data["hmac"] == hashlib.sha256(b"101|1788537531|page-value").hexdigest()
    assert headers["X-CSRF-Token"] == "csrf-value"


def test_ext_is_enabled_by_default() -> None:
    assert ExtSite.default_enabled is True


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int,
        text: str,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            msg = f"HTTP {self.status_code}"
            raise RuntimeError(msg)


def _challenge(url: str) -> _FakeResponse:
    return _FakeResponse(
        status_code=403,
        text="Just a moment...",
        url=url,
        headers={"server": "cloudflare"},
    )


def _ok_search(url: str) -> _FakeResponse:
    return _FakeResponse(status_code=200, text=FIXTURE.read_text(), url=url)


class _FakeSession:
    def __init__(self, handler: Any) -> None:
        self.handler = handler
        self.gets: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        self.gets.append(url)
        return self.handler(url)  # type: ignore[no-any-return]

    def post(self, _url: str, **_kwargs: object) -> _FakeResponse:
        msg = "magnet POST should be mocked in search tests"
        raise AssertionError(msg)


def _two_mirror_site(*, bypass: Any = None) -> ExtSite:
    site = ExtSite()
    site.bypass = bypass
    site.url = "https://ext.to"
    site.mirror_urls = ["https://extto.com"]
    site._mirror_pref = None
    return site


def test_fetch_search_page_skips_cloudflare_mirrors_without_bypass() -> None:
    site = _two_mirror_site()
    sessions: list[_FakeSession] = []

    def factory() -> _FakeSession:
        def handler(url: str) -> _FakeResponse:
            if url.startswith("https://extto.com"):
                return _ok_search("https://extto.com/browse/?q=ubuntu")
            return _challenge(url)

        session = _FakeSession(handler)
        sessions.append(session)
        return session

    with patch.object(site, "_build_plain_session", side_effect=factory):
        html, session, origin = site._fetch_search_page("ubuntu")

    assert origin == "https://extto.com"
    assert "search-table" in html
    assert session is sessions[-1]
    assert any(url.startswith("https://ext.to/") for s in sessions for url in s.gets)
    assert any(url.startswith("https://extto.com/") for s in sessions for url in s.gets)


def test_fetch_search_page_does_not_solve_when_a_plain_mirror_works() -> None:
    bypass = MagicMock()
    bypass.config.enabled = True
    site = _two_mirror_site(bypass=bypass)

    def factory() -> _FakeSession:
        def handler(url: str) -> _FakeResponse:
            if url.startswith("https://extto.com"):
                return _ok_search("https://extto.com/browse/?q=ubuntu")
            return _challenge(url)

        return _FakeSession(handler)

    with (
        patch.object(site, "_build_plain_session", side_effect=factory),
        patch.object(site, "_build_cf_session") as cf_factory,
    ):
        html, _session, origin = site._fetch_search_page("ubuntu")

    assert origin == "https://extto.com"
    assert "search-table" in html
    bypass.solve.assert_not_called()
    cf_factory.assert_not_called()


def test_fetch_search_page_uses_cf_session_only_after_plain_mirrors_fail() -> None:
    bypass = MagicMock()
    bypass.config.enabled = True
    site = _two_mirror_site(bypass=bypass)

    def plain_factory() -> _FakeSession:
        return _FakeSession(lambda url: _challenge(url))

    def cf_factory() -> _FakeSession:
        return _FakeSession(lambda _url: _ok_search("https://ext.to/browse/?q=ubuntu"))

    with (
        patch.object(site, "_build_plain_session", side_effect=plain_factory),
        patch.object(site, "_build_cf_session", side_effect=cf_factory),
    ):
        html, _session, origin = site._fetch_search_page("ubuntu")

    assert origin == "https://ext.to"
    assert "search-table" in html
    bypass.solve.assert_not_called()


def test_fetch_search_page_solves_when_cf_session_is_still_challenged() -> None:
    bypass = MagicMock()
    bypass.config.enabled = True
    bypass.solve.return_value = "solved"
    site = _two_mirror_site(bypass=bypass)

    class CfSession:
        def get(self, url: str, **_kwargs: object) -> _FakeResponse:
            if bypass.solve.called:
                return _ok_search("https://ext.to/browse/?q=ubuntu")
            return _challenge(url)

    with (
        patch.object(
            site,
            "_build_plain_session",
            side_effect=lambda: _FakeSession(lambda url: _challenge(url)),
        ),
        patch.object(site, "_build_cf_session", side_effect=CfSession),
    ):
        html, _session, origin = site._fetch_search_page("ubuntu")

    assert origin == "https://ext.to"
    assert "search-table" in html
    bypass.solve.assert_called()
