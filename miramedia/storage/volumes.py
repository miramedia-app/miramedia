"""Bounded disk-usage probes for configured storage roots."""

from __future__ import annotations

import shutil
from pathlib import Path

from miramedia.config import BasicConfig
from miramedia.storage.schemas import StorageVolume


def probe_volume(label: str, path: Path | str) -> StorageVolume:
    """Stat one path's volume. Walks up to an existing parent; never walks media."""
    raw = str(path).strip()
    if not raw:
        return StorageVolume(label=label, path=raw, error="unset")
    walk = Path(raw)
    while not walk.exists() and walk.parent != walk:
        walk = walk.parent
    try:
        usage = shutil.disk_usage(walk)
    except OSError as exc:
        return StorageVolume(
            label=label,
            path=raw,
            error=exc.strerror or type(exc).__name__,
        )
    return StorageVolume(
        label=label,
        path=raw,
        total_bytes=int(usage.total),
        used_bytes=int(usage.used),
        free_bytes=int(usage.free),
    )


def probe_storage_volumes(misc: BasicConfig) -> list[StorageVolume]:
    """O(configured-roots) volume list: libraries, downloads, images."""
    targets: list[tuple[str, Path | str]] = [
        ("Shows (Default)", misc.show_directory),
        ("Movies (Default)", misc.movie_directory),
        ("Downloads", misc.effective_completed_path),
        ("Images", misc.image_directory),
    ]
    incomplete = (misc.incomplete_torrent_path or "").strip()
    if incomplete:
        targets.append(("Incomplete downloads", incomplete))
    targets.extend(
        (f"Shows ({lib.name})", lib.path)
        for lib in misc.show_libraries
        if lib.name != "Default"
    )
    targets.extend(
        (f"Movies ({lib.name})", lib.path)
        for lib in misc.movie_libraries
        if lib.name != "Default"
    )
    return [probe_volume(label, path) for label, path in targets]
