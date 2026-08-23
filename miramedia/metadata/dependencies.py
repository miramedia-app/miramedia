import logging
from typing import Annotated, Literal

from fastapi import Depends
from fastapi.exceptions import HTTPException

from miramedia.config import MiraMediaConfig
from miramedia.metadata.backends.generic import AbstractMetadataProvider

log = logging.getLogger(__name__)

# Single provider precedence used EVERYWHERE — search, trending, details
# refresh, and fan-out. Details no longer use a different order than search:
# one list, one mental model. TMDB and TVDB cover both media types; the native
# provider sits last and internally prefers Cinemeta, then TVMaze (shows only).
# Net per-media order:
#   Movies: TMDB → TVDB → Cinemeta
#   Shows:  TMDB → TVDB → Cinemeta → TVMaze (TVMaze = native fallback only)
_PROVIDER_ORDER = ["tmdb", "tvdb", "native"]


def get_discovery_providers() -> list[AbstractMetadataProvider]:
    """Ordered list of enabled providers for search/trending fan-through.

    Disabled / unconfigured providers (no API key) are skipped. Callers try
    each in turn and fall through on no-results or an unreachable provider.
    """
    providers: list[AbstractMetadataProvider] = []
    for name in _PROVIDER_ORDER:
        try:
            providers.append(get_metadata_provider(name))
        except HTTPException:
            continue  # not enabled / missing key
    return providers


def get_all_enabled_providers() -> list[AbstractMetadataProvider]:
    """Return every enabled metadata provider, in the same order as search.
    Used by features that fan a query across providers (details refresh,
    library-scan candidate search, ...) instead of resolving a single one."""
    providers: list[AbstractMetadataProvider] = []
    for name in _PROVIDER_ORDER:
        try:
            providers.append(get_metadata_provider(name))
        except Exception:  # noqa: S112 — best-effort fan-out, skip unavailable provider
            continue
    return providers


def resolve_metadata_provider(
    original_provider: str,
) -> AbstractMetadataProvider | None:
    """Resolve the best available metadata provider for a media item.

    Tries the original provider first, then falls back through enabled providers
    in the shared search order: tmdb → tvdb → native.
    """
    # Try original first, then remaining in shared precedence order
    candidates = [original_provider] + [
        p for p in _PROVIDER_ORDER if p != original_provider
    ]

    for name in candidates:
        try:
            return get_metadata_provider(name)
        except Exception:  # noqa: S112 — best-effort fallback, try next provider
            continue

    log.warning("No metadata provider available (tried: %s)", candidates)
    return None


def get_metadata_provider(
    metadata_provider: Literal["native", "tmdb", "tvdb"] = "native",
) -> AbstractMetadataProvider:
    config = MiraMediaConfig().metadata

    if metadata_provider == "native":
        if not config.native.enabled:
            raise HTTPException(
                status_code=400,
                detail="Native metadata provider is not enabled.",
            )
        from miramedia.metadata.backends.native import NativeMetadataProvider

        return NativeMetadataProvider()

    if metadata_provider == "tmdb":
        if not config.tmdb.enabled or not config.tmdb.api_key:
            raise HTTPException(
                status_code=400,
                detail="TMDB is not enabled or API key is missing.",
            )
        from miramedia.metadata.backends.tmdb import TmdbMetadataProvider

        return TmdbMetadataProvider()

    if metadata_provider == "tvdb":
        if not config.tvdb.enabled or not config.tvdb.api_key:
            raise HTTPException(
                status_code=400,
                detail="TVDB is not enabled or API key is missing.",
            )
        from miramedia.metadata.backends.tvdb import TvdbMetadataProvider

        return TvdbMetadataProvider()

    raise HTTPException(
        status_code=400,
        detail=f"Invalid metadata provider: {metadata_provider}. Supported: 'native', 'tmdb', 'tvdb'.",
    )


metadata_provider_dep = Annotated[
    AbstractMetadataProvider, Depends(get_metadata_provider)
]
