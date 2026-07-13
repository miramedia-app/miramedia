"""Filesystem containment checks for configured media library roots."""

from __future__ import annotations

from pathlib import Path

from miramedia.config import MiraMediaConfig
from miramedia.torrents.schemas import MediaType


class PathNotFoundError(Exception):
    """Path does not exist or cannot be resolved."""


class PathNotDirectoryError(Exception):
    """Path exists but is not a directory."""


class PathOutsideRootsError(Exception):
    """Resolved path is not contained by any allowed root."""


class PathCanonicalResolutionError(Exception):
    """Path could not be resolved to a canonical filesystem identity."""


def paths_same_canonical(left: Path, right: Path) -> bool:
    """True when both paths resolve to the same filesystem location."""
    try:
        return left.resolve() == right.resolve()
    except OSError as exc:
        raise PathCanonicalResolutionError(str(left)) from exc


def library_roots_for_media_type(media_type: MediaType) -> list[Path]:
    """Configured show or movie library roots, including named libraries."""
    cfg = MiraMediaConfig().misc
    if media_type == MediaType.show:
        roots = [Path(cfg.show_directory)]
        roots.extend(Path(lib.path) for lib in cfg.show_libraries)
        return roots
    roots = [Path(cfg.movie_directory)]
    roots.extend(Path(lib.path) for lib in cfg.movie_libraries)
    return roots


def resolve_path_within_roots(
    path: Path,
    allowed_roots: list[Path],
    *,
    require_directory: bool = False,
    require_exists: bool = True,
) -> Path:
    """Canonicalize ``path`` and accept it only when contained by a root."""
    if not allowed_roots:
        raise PathOutsideRootsError(str(path))
    if require_exists and not path.exists():
        raise PathNotFoundError(str(path))
    if require_directory and not path.is_dir():
        raise PathNotDirectoryError(str(path))
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise PathNotFoundError(str(path)) from exc
    for root in allowed_roots:
        try:
            root_resolved = root.resolve()
        except OSError:
            continue
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return resolved
    raise PathOutsideRootsError(str(path))
