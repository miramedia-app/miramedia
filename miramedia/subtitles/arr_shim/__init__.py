"""Sonarr/Radarr API compatibility shim for Bazarr integration."""

from miramedia.subtitles.arr_shim.radarr import (
    legacy_router as radarr_shim_legacy_router,
)
from miramedia.subtitles.arr_shim.radarr import (
    router as radarr_shim_router,
)
from miramedia.subtitles.arr_shim.sonarr import (
    legacy_router as sonarr_shim_legacy_router,
)
from miramedia.subtitles.arr_shim.sonarr import (
    router as sonarr_shim_router,
)

__all__ = [
    "SHIM_PATH_PREFIXES",
    "radarr_shim_legacy_router",
    "radarr_shim_router",
    "sonarr_shim_legacy_router",
    "sonarr_shim_router",
]

# Path prefixes the shim owns. Anything under these is API surface, never SPA
# fallback territory (see the 404 handler in miramedia/main.py).
SHIM_PATH_PREFIXES = ("/sonarr/", "/radarr/")
