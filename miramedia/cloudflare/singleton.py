"""Process-wide CloudflareBypass instance.

Single solver per process so cookies are shared across all callers
(indexer sites, subtitle providers, future modules).
"""

from __future__ import annotations

import threading

from miramedia.cloudflare.bypass import CloudflareBypass

_lock = threading.Lock()
_instance: CloudflareBypass | None = None


def get_cloudflare_bypass() -> CloudflareBypass:
    """Return the process-wide CloudflareBypass singleton."""
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                # Local import to avoid circular import with miramedia.config
                from miramedia.config import MiraMediaConfig

                _instance = CloudflareBypass(MiraMediaConfig().cloudflare)
    return _instance


def reset_cloudflare_bypass() -> None:
    """Drop the cached singleton. Test/debug helper."""
    global _instance
    with _lock:
        _instance = None
