import logging
import ntpath
import re
import typing
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath

from pathvalidate import sanitize_filename

from miramedia.config import MiraMediaConfig
from miramedia.exceptions import UnsafeTorrentTitleError
from miramedia.torrents.schemas import Torrent

log = logging.getLogger(__name__)


def resolve_within(root: Path, relative_path: str) -> Path | None:
    """Resolve ``relative_path`` against ``root``, refusing escapes.

    Returns the resolved path only if it stays inside ``root`` after
    symlink/".." resolution; returns ``None`` for absolute inputs, ``..``
    traversal, or symlinks pointing outside the root.
    """
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root.resolve()):
        return None
    return candidate


_PATH_SEPARATORS = frozenset("/\\")
_RESERVED_LEAF_NAMES = frozenset({".", ".."})
_WINDOWS_DRIVE_ANCHOR_RE = re.compile(r"^[A-Za-z]:")
_WINDOWS_DEVICE_PREFIXES = ("\\\\.\\", "\\\\?\\", "//./", "//?/")
_WINDOWS_INVALID_LEAF_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_BASE = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)
_APPLICATION_CONTROL_LEAF_NAMES = frozenset({".resume_data"})


def _has_disallowed_unicode_controls(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            return True
        if unicodedata.category(ch) == "Cc":
            return True
    return False


def _is_windows_reserved_leaf(leaf: str) -> bool:
    if hasattr(ntpath, "isreserved"):
        return bool(ntpath.isreserved(leaf))
    upper = leaf.upper()
    if upper in _WINDOWS_RESERVED_BASE:
        return True
    if "." in upper:
        return upper.split(".", 1)[0] in _WINDOWS_RESERVED_BASE
    return False


def _has_invalid_leaf_trailing_chars(leaf: str) -> bool:
    if leaf.endswith((".", " ")):
        return True
    stripped = leaf.strip()
    return bool(stripped) and all(ch == "." for ch in stripped)


class _TorrentRootsCfg(typing.Protocol):
    effective_completed_path: Path | str
    incomplete_torrent_path: str
    torrent_directory: Path | str


def _configured_torrent_roots(cfg: _TorrentRootsCfg) -> list[Path]:
    roots = [Path(cfg.effective_completed_path)]
    incomplete = (cfg.incomplete_torrent_path or "").strip()
    if incomplete:
        roots.append(Path(incomplete))
    return roots


def _application_control_dir_paths(cfg: _TorrentRootsCfg) -> list[Path]:
    return [
        Path(cfg.torrent_directory) / name for name in _APPLICATION_CONTROL_LEAF_NAMES
    ]


def _has_windows_path_anchor(title: str) -> bool:
    """True when *title* carries a Windows drive, UNC, or device anchor."""
    if PureWindowsPath(title).is_absolute():
        return True
    if _WINDOWS_DRIVE_ANCHOR_RE.match(title):
        return True
    if title.startswith("\\\\"):
        return True
    return title.startswith(_WINDOWS_DEVICE_PREFIXES)


def _strict_descendant_under_root(
    root: Path,
    leaf: str,
    *,
    forbid_leaf_symlink: bool,
) -> Path:
    """Return *root/leaf* only when it is a strict descendant of *root*."""
    root_resolved = root.resolve()
    candidate = root / leaf

    if forbid_leaf_symlink and candidate.is_symlink():
        reason = "Torrent title path is a symlink"
        raise UnsafeTorrentTitleError(reason)

    try:
        resolved = candidate.resolve()
    except OSError as exc:
        reason = "Torrent title path is not accessible under the torrent root"
        raise UnsafeTorrentTitleError(reason) from exc

    if resolved == root_resolved:
        reason = "Torrent title resolves to the torrent root directory"
        raise UnsafeTorrentTitleError(reason)
    if not resolved.is_relative_to(root_resolved):
        reason = "Torrent title cannot be contained under the torrent root"
        raise UnsafeTorrentTitleError(reason)

    return resolved


def _is_safe_deletion_target(
    path: Path,
    roots: list[Path],
    *,
    forbidden: list[Path] | None = None,
) -> bool:
    """Return True when *path* is a strict descendant of a root, never a root itself."""
    if path.is_symlink():
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False

    root_resolved = [root.resolve() for root in roots]
    protected = set(root_resolved)
    if forbidden:
        protected.update(item.resolve() for item in forbidden)

    if resolved in protected:
        return False

    return any(
        resolved.is_relative_to(root_item) and resolved != root_item
        for root_item in root_resolved
    )


def torrent_title_path_component(title: str) -> str:
    """Return *title* as a safe single filesystem path component.

    Security boundary: every torrent-title-derived directory join must go
    through this helper. The display title stored in the database is never
    modified — invalid titles are rejected rather than lossily sanitized.
    """
    reason: str | None = None
    if not title or not title.strip():
        reason = "Torrent title is empty"
    elif _has_disallowed_unicode_controls(title):
        reason = f"Torrent title contains control characters: {title!r}"
    elif any(ch in _WINDOWS_INVALID_LEAF_CHARS for ch in title):
        reason = f"Torrent title contains invalid filename characters: {title!r}"
    elif ":" in title:
        reason = f"Torrent title contains an alternate-data-stream marker: {title!r}"
    elif _has_invalid_leaf_trailing_chars(title):
        reason = f"Torrent title has invalid trailing characters: {title!r}"
    elif title.casefold() in {n.casefold() for n in _APPLICATION_CONTROL_LEAF_NAMES}:
        reason = f"Torrent title is a reserved application directory name: {title!r}"
    elif _is_windows_reserved_leaf(title):
        reason = f"Torrent title is a reserved Windows device name: {title!r}"
    elif any(sep in title for sep in _PATH_SEPARATORS):
        reason = f"Torrent title contains a path separator: {title!r}"
    elif PurePosixPath(title).is_absolute():
        reason = f"Torrent title is an absolute path: {title!r}"
    elif _has_windows_path_anchor(title):
        reason = f"Torrent title has a Windows path anchor: {title!r}"
    elif title in _RESERVED_LEAF_NAMES:
        reason = f"Torrent title is not a valid directory name: {title!r}"
    else:
        for part in PurePosixPath(title).parts:
            if part in _RESERVED_LEAF_NAMES:
                reason = f"Torrent title contains a reserved path segment: {title!r}"
                break

    if reason is not None:
        raise UnsafeTorrentTitleError(reason)

    return title


def torrent_dir_under_root(root: Path, title: str) -> Path:
    """Resolve a torrent title to a directory path guaranteed under *root*."""
    leaf = torrent_title_path_component(title)
    return _strict_descendant_under_root(root, leaf, forbid_leaf_symlink=True)


def torrent_deletion_dir_under_root(root: Path, title: str) -> Path:
    """Like :func:`torrent_dir_under_root` but rejects title-leaf symlinks."""
    return torrent_dir_under_root(root, title)


def torrent_sidecar_under_root(root: Path, title: str) -> Path:
    """Return a contained ``.torrent`` sidecar path for *title* under *root*."""
    leaf = torrent_title_path_component(title)
    return _strict_descendant_under_root(
        root, f"{leaf}.torrent", forbid_leaf_symlink=True
    )


def _lookup_dir_if_present(root: Path, leaf: str) -> Path | None:
    """Return a contained, non-symlink directory only when it exists under *root*."""
    if leaf.casefold() in {n.casefold() for n in _APPLICATION_CONTROL_LEAF_NAMES}:
        return None
    try:
        safe_leaf = torrent_title_path_component(leaf)
    except UnsafeTorrentTitleError:
        return None
    candidate = root / safe_leaf
    if candidate.is_symlink() or not candidate.exists() or not candidate.is_dir():
        return None
    try:
        return _strict_descendant_under_root(root, safe_leaf, forbid_leaf_symlink=True)
    except UnsafeTorrentTitleError:
        return None


def exact_save_dirs_for_title(title: str) -> list[Path]:
    """Exact title-derived save paths used when the torrent was created.

    These are not proof of payload ownership for destructive cleanup — callers
    may only ``rmdir()`` them when empty after libtorrent removed files.
    """
    cfg = MiraMediaConfig().misc
    dirs: list[Path] = []
    for root in _configured_torrent_roots(cfg):
        try:
            dirs.append(torrent_dir_under_root(root, title))
        except UnsafeTorrentTitleError:
            continue
    return dirs


def _deterministic_lookup_fallback(root: Path, title: str) -> Path:
    """Return a non-symlink lookup path, using a literal join when absent."""
    safe_leaf = torrent_title_path_component(title)
    candidate = root / safe_leaf
    if candidate.is_symlink():
        reason = "Torrent title path is a symlink"
        raise UnsafeTorrentTitleError(reason)
    if candidate.exists():
        return _strict_descendant_under_root(root, safe_leaf, forbid_leaf_symlink=True)
    return candidate


def get_torrent_filepath(torrent: Torrent) -> Path:
    """Resolve the on-disk directory holding a torrent's files.

    libtorrent / qBittorrent / Transmission save files under the .torrent's
    ``info.name``, which often differs from the indexer-reported
    ``torrent.title`` (the latter omits / adds release-group qualifiers).
    The naive ``<completed>/<title>`` join misses those cases entirely and
    every downstream "no video file" import error traces back here.

    Resolution order:
    1. ``<completed>/<title>`` if it already exists.
    2. ``<completed>/<sanitized title>`` (typical filesystem-safe variant).
    3. A fuzzy scan of ``<completed>`` for the directory whose normalized
       name shares the largest word overlap with the torrent title.
    Falls back to ``<completed>/<title>`` (which may not exist) so callers
    still get a deterministic path they can mkdir-on / report on.
    """
    completed = MiraMediaConfig().misc.effective_completed_path
    leaf = torrent_title_path_component(torrent.title)

    primary = _lookup_dir_if_present(completed, leaf)
    if primary is not None:
        return primary

    sanitized = sanitize_filename(torrent.title)
    if sanitized and sanitized != torrent.title:
        sanitized_match = _lookup_dir_if_present(completed, sanitized)
        if sanitized_match is not None:
            return sanitized_match

    title_words = _torrent_dir_words(torrent.title)
    title_disc = _dir_discriminators(torrent.title)
    if title_words and completed.exists():
        best: tuple[float, Path] | None = None
        try:
            children = list(completed.iterdir())
        except OSError:
            children = []
        for child in children:
            if not child.is_dir():
                continue
            child_disc = _dir_discriminators(child.name)
            if title_disc and child_disc and title_disc.isdisjoint(child_disc):
                continue
            child_words = _torrent_dir_words(child.name)
            if not child_words:
                continue
            overlap = title_words & child_words
            if not overlap:
                continue
            accepted = _lookup_dir_if_present(completed, child.name)
            if accepted is None:
                continue
            score = len(overlap) / len(title_words)
            if best is None or score > best[0]:
                best = (score, accepted)
        if best is not None and best[0] >= 0.6:
            log.debug(
                "Resolved torrent %r to on-disk dir %s (overlap %.0f%%)",
                torrent.title,
                best[1],
                best[0] * 100,
            )
            return best[1]

    return _deterministic_lookup_fallback(completed, torrent.title)


_TORRENT_NORMALIZE_RE = re.compile(r"[._\-\s]+")
_TORRENT_NOISE_WORDS = {
    "1080p",
    "2160p",
    "720p",
    "480p",
    "uhd",
    "hdr",
    "bluray",
    "bdrip",
    "web",
    "webdl",
    "webrip",
    "hdtv",
    "dvdrip",
    "brrip",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "aac",
    "ac3",
    "dts",
    "atmos",
    "10bit",
    "remux",
}


def _torrent_dir_words(name: str) -> set[str]:
    """Lowercase token set for fuzzy dir matching, with quality / codec noise
    stripped so the comparison is driven by the real title words."""
    cleaned = _TORRENT_NORMALIZE_RE.sub(" ", name)
    return {
        w.lower()
        for w in cleaned.split()
        if w and w.lower() not in _TORRENT_NOISE_WORDS
    }


_DISCRIMINATOR_RE = re.compile(
    r"(?:s(\d{1,2})e\d{1,3})|(?:\bs(\d{1,2})\b)|(\b(?:19|20)\d{2}\b)",
    re.IGNORECASE,
)


def _dir_discriminators(name: str) -> set[str]:
    """Identity tokens that distinguish one release from a *similar* sibling:
    the season marker (``s06``) and any release year (``2026``).

    Title word overlap alone can't tell ``Greys.Anatomy.S06`` from
    ``Greys.Anatomy.S09`` (only the season differs) or a show
    (``...Circus.S01E04``) from the same-franchise movie
    (``...Circus.The.Last.Act.2026``) — every other word matches. Callers use
    these to refuse a fuzzy match whose discriminators *conflict* with the
    target's, so a torrent never resolves onto a different season's / a
    different work's on-disk directory.
    """
    out: set[str] = set()
    for sxxexx, sxx, year in _DISCRIMINATOR_RE.findall(name):
        season = sxxexx or sxx
        if season:
            out.add(f"s{int(season):02d}")
        if year:
            out.add(year)
    return out
