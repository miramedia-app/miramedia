"""Redact secrets from viewing-sync log and persisted error text."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_TOKEN_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(\S+)"),
    re.compile(r"(?i)(token\s*[:=]\s*[\"']?)([^\"'\s,]+)"),
)


def redact_secret_text(text: str, *, api_key: str = "") -> str:
    redacted = text
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub(r"\1***", redacted)
    if api_key:
        redacted = redacted.replace(api_key, "***")
    return redacted


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return "<redacted-url>"
    return f"{parsed.scheme}://<host>"
