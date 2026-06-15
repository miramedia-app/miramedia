"""Reusable Cloudflare challenge bypass.

Combines a non-headless Chrome (via nodriver) that earns cf_clearance cookies
with a curl_cffi-backed Session that replays those cookies using a matching
browser TLS fingerprint.

Public API:
    from miramedia.cloudflare import (
        CloudflareBypass,        # solver
        CloudflareSession,       # drop-in requests-like session with auto-bypass
        get_cloudflare_bypass,   # process-wide singleton
        is_cloudflare_challenge, # response sniffer
    )
"""

from miramedia.cloudflare.bypass import CloudflareBypass, is_cloudflare_challenge
from miramedia.cloudflare.session import CloudflareSession
from miramedia.cloudflare.singleton import get_cloudflare_bypass

__all__ = [
    "CloudflareBypass",
    "CloudflareSession",
    "get_cloudflare_bypass",
    "is_cloudflare_challenge",
]
