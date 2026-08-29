"""Redact indexer enclosure URLs before persist/log (design 385 I9)."""

from __future__ import annotations

from urllib.parse import urlencode, urlparse, urlunparse

_SENSITIVE_QUERY_KEYS = frozenset(
    {"apikey", "api_key", "passkey", "auth", "token", "key"}
)


def redact_download_url(url: str) -> str:
    """Strip sensitive query parameters; keep scheme/host/path for dedup hints."""
    if not url:
        return ""
    if url.startswith("magnet:"):
        from miramedia.torrents.fetch import _redact_torrent_url

        return _redact_torrent_url(url)
    try:
        parsed = urlparse(url)
    except ValueError:
        return "<redacted>"
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "<redacted>"
    query_pairs = []
    if parsed.query:
        from urllib.parse import parse_qsl

        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key.lower() in _SENSITIVE_QUERY_KEYS:
                query_pairs.append((key, "<redacted>"))
            else:
                query_pairs.append((key, value))
    redacted_query = urlencode(query_pairs)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/",
            parsed.params,
            redacted_query,
            "",
        )
    )
