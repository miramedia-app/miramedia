"""Resolution-only quality upgrade helpers for movie auto-download (design 309 Slice A)."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from miramedia.indexers.config import QualityOption
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.schemas import Quality

log = logging.getLogger(__name__)

_QUALITY_ORDINALS: dict[Quality, int] = {
    Quality.uhd: 4,
    Quality.fullhd: 3,
    Quality.hd: 2,
    Quality.sd: 1,
    Quality.unknown: 0,
}


def quality_ordinal(quality: Quality) -> int:
    """Return the stable resolution ladder ordinal (higher = better)."""
    return _QUALITY_ORDINALS[quality]


def effective_quality_upgrades(
    *,
    global_enabled: bool,
    movie_override: bool | None,
) -> bool:
    """Tri-state quality_upgrades: NULL inherits global; explicit False wins."""
    if movie_override is False:
        return False
    if movie_override is True:
        return True
    return global_enabled


def quality_option_name_to_quality(option_name: str) -> Quality | None:
    """Map an indexer quality option display name to a Quality bucket."""
    lowered = option_name.lower()
    if "4k" in lowered or "uhd" in lowered:
        return Quality.uhd
    if "1080" in lowered or "full hd" in lowered or "fullhd" in lowered:
        return Quality.fullhd
    if "720" in lowered or lowered.strip() == "hd":
        return Quality.hd
    if "sd" in lowered or "480" in lowered or "576" in lowered:
        return Quality.sd
    return None


def resolve_upgrade_cutoff_quality(
    *,
    movie_cutoff_name: str | None,
    global_cutoff_name: str | None,
    quality_options: Iterable[QualityOption],
) -> Quality:
    """Resolve the upgrade-until cutoff bucket from explicit names or defaults."""
    for name in (movie_cutoff_name, global_cutoff_name):
        if name:
            resolved = quality_option_name_to_quality(name)
            if resolved is not None:
                return resolved
    for opt in quality_options:
        if not opt.enabled:
            continue
        resolved = quality_option_name_to_quality(opt.name)
        if resolved is not None:
            return resolved
    return Quality.fullhd


def best_on_disk_library_quality(file_qualities: Iterable[Quality]) -> Quality | None:
    """Return the best known resolution among on-disk files, or None."""
    best: Quality | None = None
    best_ord = -1
    for quality in file_qualities:
        if quality == Quality.unknown:
            continue
        ordinal = quality_ordinal(quality)
        if ordinal > best_ord:
            best_ord = ordinal
            best = quality
    return best


def is_strict_resolution_upgrade(
    *,
    candidate: Quality,
    best_library: Quality,
    cutoff: Quality,
) -> bool:
    """True when candidate is strictly better than library and within cutoff."""
    if candidate == Quality.unknown or best_library == Quality.unknown:
        return False
    candidate_ord = quality_ordinal(candidate)
    best_ord = quality_ordinal(best_library)
    cutoff_ord = quality_ordinal(cutoff)
    return candidate_ord > best_ord and candidate_ord <= cutoff_ord


def filter_upgrade_candidates(
    results: list[IndexerQueryResult],
    *,
    best_library: Quality,
    cutoff: Quality,
) -> list[IndexerQueryResult]:
    """Keep only strict resolution upgrades at or below the cutoff."""
    return [
        result
        for result in results
        if is_strict_resolution_upgrade(
            candidate=result.quality,
            best_library=best_library,
            cutoff=cutoff,
        )
    ]


def library_satisfied_for_cutoff(*, best_library: Quality, cutoff: Quality) -> bool:
    """True when the best on-disk file already meets or exceeds the cutoff."""
    if best_library == Quality.unknown:
        return False
    return quality_ordinal(best_library) >= quality_ordinal(cutoff)
