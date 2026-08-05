import ipaddress
import logging
import ntpath
import re
import socket
import typing
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urljoin, urlparse

import libtorrent
import requests
from pathvalidate import sanitize_filename

from miramedia.config import MiraMediaConfig
from miramedia.exceptions import UnsafeTorrentTitleError
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.schemas import Torrent

log = logging.getLogger(__name__)

_MAX_TORRENT_PAYLOAD_BYTES = 32 * 1024 * 1024
_MAX_TORRENT_PAYLOAD_REDIRECTS = 5
_TORRENT_URL_REDACTED = "<redacted>"
_MAGNET_URL_REDACTED = "magnet:<redacted>"


def _parse_torrent_bytes(
    content: bytes,
) -> "tuple[str, str, list[tuple[str, int]]] | None":
    """Parse a bencoded .torrent payload using libtorrent.

    Returns ``(info_hash_hex, name, files)`` where *info_hash_hex* is a
    lowercase 40-char hex string, *name* is the torrent name, and *files* is
    a list of ``(relative_path_str, size_bytes)`` tuples with the torrent-name
    prefix stripped from multi-file paths — matching the shape previously
    produced by hand-walking the bencoded info dict.

    Returns ``None`` on any parse failure.
    """
    try:
        ti = libtorrent.torrent_info(libtorrent.bdecode(content))
    except Exception:
        return None

    ih = ti.info_hashes()
    info_hash_hex = str(ih.v1).lower()
    name = ti.name()

    fs = ti.files()
    files: list[tuple[str, int]] = []
    prefix = name + "/"
    for i in range(fs.num_files()):
        fp = fs.file_path(i)
        # libtorrent prepends the torrent name for multi-file torrents;
        # strip it so the path is relative to the torrent root directory.
        fp = fp.removeprefix(prefix)
        files.append((fp, fs.file_size(i)))

    return info_hash_hex, name, files


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


class TorrentFile(typing.NamedTuple):
    """A single file inside a torrent payload.

    ``size`` is bytes when known, ``0`` when the backend / payload didn't
    expose a size (treat as "size unknown" in heuristics).
    """

    path: Path
    size: int


class TorrentInspection(typing.NamedTuple):
    """Pre-add view of a torrent.

    ``info_hash`` is the canonical 40-char lower-case hex hash when known.
    ``files`` is the file list pulled from the ``.torrent`` payload, or
    ``None`` for pure magnet links whose file dictionary lives in swarm
    metadata.
    """

    info_hash: str | None
    files: list[TorrentFile] | None


def _redact_torrent_url(url: str) -> str:
    """Return a log-safe view of a torrent or magnet URL.

    HTTP(S) URLs keep scheme, host, optional non-default port, and path only.
    Magnet URIs keep a validated info-hash fingerprint when parseable.
    Malformed input never echoes the original string.
    """
    if not url:
        return _TORRENT_URL_REDACTED
    if url.startswith("magnet:"):
        try:
            info_hash = str(libtorrent.parse_magnet_uri(url).info_hash).lower()
        except Exception:
            return _MAGNET_URL_REDACTED
        return f"magnet:?xt=urn:btih:{info_hash}"
    try:
        parsed = urlparse(url)
    except ValueError:
        return _TORRENT_URL_REDACTED
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return _TORRENT_URL_REDACTED
    host = parsed.hostname
    if parsed.port is not None and parsed.port not in (80, 443):
        host = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    return f"{parsed.scheme}://{host}{path}"


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    )


def _resolve_and_validate_torrent_host(hostname: str) -> str:
    """Resolve *hostname* and return the first validated IP to pin for the fetch."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as exc:
        msg = f"Could not resolve torrent host {hostname!r}"
        raise ValueError(msg) from exc
    pinned_ip: str | None = None
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError as exc:
            msg = f"Invalid resolved address for torrent host {hostname!r}"
            raise ValueError(msg) from exc
        if _is_blocked_ip(addr):
            msg = f"Blocked resolved address for torrent host {hostname!r}"
            raise ValueError(msg)
        if pinned_ip is None:
            pinned_ip = str(info[4][0])
    if pinned_ip is None:
        msg = f"No addresses resolved for torrent host {hostname!r}"
        raise ValueError(msg)
    return pinned_ip


def _validate_torrent_http_url(url: str) -> tuple[str, str]:
    """Validate an HTTP(S) torrent URL and return ``(hostname, pinned_ip)``."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        msg = "Malformed torrent URL"
        raise ValueError(msg) from exc
    if parsed.scheme not in ("http", "https"):
        msg = f"Unsupported torrent URL scheme: {parsed.scheme!r}"
        raise ValueError(msg)
    if parsed.username or parsed.password:
        msg = "Torrent URL userinfo is not allowed"
        raise ValueError(msg)
    hostname = parsed.hostname
    if not hostname:
        msg = "Torrent URL is missing a hostname"
        raise ValueError(msg)
    pinned_ip = _resolve_and_validate_torrent_host(hostname)
    return hostname, pinned_ip


@contextmanager
def _dns_pin(hostname: str, pinned_ip: str) -> Iterator[None]:
    """Pin DNS resolution for *hostname* to *pinned_ip* for the current request."""
    real_getaddrinfo = socket.getaddrinfo

    def pinned_getaddrinfo(
        host: str,
        port: object,
        *args: object,
        **kwargs: object,
    ) -> list[tuple]:
        if host == hostname:
            return real_getaddrinfo(pinned_ip, port, *args, **kwargs)
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = pinned_getaddrinfo  # ty: ignore[invalid-assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo


def _read_bounded_response_body(response: requests.Response) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError:
            declared = None
        else:
            if declared > _MAX_TORRENT_PAYLOAD_BYTES:
                msg = (
                    f"Torrent payload Content-Length {declared} exceeds "
                    f"{_MAX_TORRENT_PAYLOAD_BYTES} bytes"
                )
                raise ValueError(msg)

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=1 << 16):
        if not chunk:
            continue
        total += len(chunk)
        if total > _MAX_TORRENT_PAYLOAD_BYTES:
            msg = (
                f"Torrent payload streamed body exceeds "
                f"{_MAX_TORRENT_PAYLOAD_BYTES} bytes"
            )
            raise ValueError(msg)
        chunks.append(chunk)
    return b"".join(chunks)


def _guarded_fetch_torrent_bytes(url: str, *, timeout: float) -> bytes:
    """Fetch a ``.torrent`` payload over HTTP(S) with SSRF and size guards."""
    current_url = url
    for _ in range(_MAX_TORRENT_PAYLOAD_REDIRECTS + 1):
        hostname, pinned_ip = _validate_torrent_http_url(current_url)
        with _dns_pin(hostname, pinned_ip):
            response = requests.get(
                current_url,
                stream=True,
                timeout=timeout,
                allow_redirects=False,
            )
        try:
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    msg = "Torrent payload redirect without Location header"
                    raise ValueError(msg)
                current_url = urljoin(current_url, location)
                if current_url.startswith("magnet:"):
                    msg = "Torrent payload redirect resolved to magnet URI"
                    raise ValueError(msg)
                continue
            response.raise_for_status()
            if not 200 <= response.status_code < 300:
                msg = f"Unexpected torrent payload status code: {response.status_code}"
                raise ValueError(msg)
            return _read_bounded_response_body(response)
        finally:
            response.close()
    msg = "Exceeded maximum number of torrent payload redirects"
    raise ValueError(msg)


def _fetch_torrent_payload(url: str, title: str) -> bytes | None:
    """Return raw bytes of the ``.torrent`` file or ``None`` if unreachable.

    Follows tracker redirects that eventually resolve to a magnet — in which
    case there's nothing to fetch and we surface ``None`` so the caller can
    fall back to the magnet path.
    """
    if url.startswith("magnet:"):
        return None

    timeout = MiraMediaConfig().indexers.timeout_seconds
    parsed = urlparse(url)
    if parsed.scheme in ("http", "https"):
        try:
            return _guarded_fetch_torrent_bytes(url, timeout=timeout)
        except Exception:
            log.debug(
                "Failed to fetch .torrent at %s for inspection",
                _redact_torrent_url(url),
                exc_info=True,
            )
            return None

    try:
        final_url = follow_redirects_to_final_torrent_url(
            initial_url=url,
            session=requests.Session(),
            timeout=timeout,
        )
    except Exception:
        log.debug(
            "Could not follow redirects to inspect torrent payload for %s",
            title,
            exc_info=True,
        )
        return None
    if final_url.startswith("magnet:"):
        return None
    try:
        return _guarded_fetch_torrent_bytes(final_url, timeout=timeout)
    except Exception:
        log.debug(
            "Failed to fetch .torrent at %s for inspection",
            _redact_torrent_url(final_url),
            exc_info=True,
        )
        return None


def inspect_torrent(indexer_result: IndexerQueryResult) -> TorrentInspection:
    """Return what we can know about a torrent without joining its swarm.

    * Magnet link → hash extracted from ``xt=urn:btih:`` clause, ``files`` is
      ``None`` (swarm metadata required).
    * ``.torrent`` URL → fetches the payload once, returns both the
      computed info-hash and the file list.
    * Anything we can't reach → both fields ``None``.

    Callers use the hash to consult the deny-list before adding and the file
    list to reject zero-video releases.
    """
    url = indexer_result.download_url
    if url.startswith("magnet:"):
        try:
            info_hash = str(libtorrent.parse_magnet_uri(url).info_hash).lower()
        except Exception:
            log.debug(
                "Could not parse magnet URI for %s",
                indexer_result.title,
                exc_info=True,
            )
            info_hash = None
        return TorrentInspection(info_hash=info_hash, files=None)

    content = _fetch_torrent_payload(url, indexer_result.title)
    if content is None:
        return TorrentInspection(info_hash=None, files=None)

    parsed = _parse_torrent_bytes(content)
    if parsed is None:
        log.debug("Could not bdecode .torrent payload", exc_info=False)
        return TorrentInspection(info_hash=None, files=None)

    info_hash, _name, raw_files = parsed
    files = [TorrentFile(path=Path(fp), size=size) for fp, size in raw_files if fp]
    return TorrentInspection(info_hash=info_hash, files=files or None)


def list_torrent_files(indexer_result: IndexerQueryResult) -> list[Path] | None:
    """Back-compat shim: return just the file paths from :func:`inspect_torrent`."""
    files = inspect_torrent(indexer_result).files
    if files is None:
        return None
    return [f.path for f in files]


# Heuristic threshold for "this is actually a movie/episode, not a decoy".
# Plenty of bad releases ship a tiny dummy .mkv alongside the real .exe
# payload to defeat naive "any video?" checks. 50 MB filters those decoys
# while still admitting low-bitrate or short-form legit releases.
MIN_MEANINGFUL_VIDEO_BYTES = 50 * 1024 * 1024


def has_meaningful_video(
    files: list["TorrentFile"],
    *,
    min_video_bytes: int = MIN_MEANINGFUL_VIDEO_BYTES,
) -> bool:
    """Return True if the file list looks like a real video release.

    Filters out sample/extras/trailer files first, then requires either
    (a) at least one video file whose size is unknown, or (b) at least one
    video file whose size meets ``min_video_bytes``. A torrent with only
    tiny decoy videos alongside an ``.exe`` fails this check.
    """
    from miramedia.torrents.parsing import is_sample_or_extra, is_video_file

    real_videos = [
        f for f in files if is_video_file(f.path) and not is_sample_or_extra(f.path)
    ]
    if not real_videos:
        return False
    sized = [f.size for f in real_videos if f.size > 0]
    if not sized:
        # Backend / payload didn't expose sizes; we can't gauge legitimacy.
        # Falling back to "any non-sample video file" matches the pre-size
        # behaviour rather than failing closed on missing data.
        return True
    return max(sized) >= min_video_bytes


def get_torrent_hash(torrent: IndexerQueryResult) -> str:
    """
    Helper method to get the torrent hash from the torrent object.

    :param torrent: The torrent object.
    :return: The hash of the torrent.
    """
    torrent_filepath = torrent_sidecar_under_root(
        MiraMediaConfig().misc.effective_completed_path, torrent.title
    )
    if torrent_filepath.exists():
        log.warning(f"Torrent file already exists at: {torrent_filepath}")

    if torrent.download_url.startswith("magnet:"):
        log.info("Parsing torrent with magnet URL: %s", torrent.title)
        log.debug("Magnet URL: %s", _redact_torrent_url(torrent.download_url))
        torrent_hash = str(libtorrent.parse_magnet_uri(torrent.download_url).info_hash)
    else:
        # downloading the torrent file
        log.info("Downloading .torrent file of torrent: %s", torrent.title)
        timeout = MiraMediaConfig().indexers.timeout_seconds
        parsed = urlparse(torrent.download_url)
        try:
            if parsed.scheme in ("http", "https"):
                torrent_content = _guarded_fetch_torrent_bytes(
                    torrent.download_url,
                    timeout=timeout,
                )
            else:
                log.debug(
                    "Invalid schema for URL %s",
                    _redact_torrent_url(torrent.download_url),
                )
                final_url = follow_redirects_to_final_torrent_url(
                    initial_url=torrent.download_url,
                    session=requests.Session(),
                    timeout=timeout,
                )
                if final_url.startswith("magnet:"):
                    return str(libtorrent.parse_magnet_uri(final_url).info_hash)
                torrent_content = _guarded_fetch_torrent_bytes(
                    final_url,
                    timeout=timeout,
                )
        except Exception:
            log.exception("Failed to download torrent file")
            raise

        # saving the torrent file
        torrent_filepath.write_bytes(torrent_content)

        # parsing info hash
        log.debug(
            "parsing torrent file: %s",
            _redact_torrent_url(torrent.download_url),
        )
        parsed = _parse_torrent_bytes(torrent_content)
        if parsed is None:
            msg = "Failed to decode torrent file"
            raise RuntimeError(msg)
        torrent_hash = parsed[0]

    return torrent_hash


def parse_magnet_or_torrent_file(
    magnet_link: str | None = None,
    torrent_file_content: bytes | None = None,
) -> tuple[str, str, str]:
    """
    Parse a magnet link or .torrent file to extract the torrent name, hash,
    and a magnet URI usable by any download client.

    Returns: (name, info_hash, magnet_uri)
    """
    if magnet_link:
        params = libtorrent.parse_magnet_uri(magnet_link)
        name = params.name or "Unknown"
        info_hash = str(params.info_hash)
        return name, info_hash, magnet_link

    if torrent_file_content is None:
        msg = "Either magnet_link or torrent_file_content must be provided"
        raise ValueError(msg)

    try:
        ti = libtorrent.torrent_info(libtorrent.bdecode(torrent_file_content))
    except Exception as exc:
        msg = "Failed to decode torrent file"
        raise ValueError(msg) from exc

    ih = ti.info_hashes()
    info_hash = str(ih.v1).lower()
    name = ti.name()

    # Build a magnet URI from the .torrent file so all download clients work
    magnet_uri = libtorrent.make_magnet_uri(ti)

    return name, info_hash, magnet_uri


def follow_redirects_to_final_torrent_url(
    initial_url: str, session: requests.Session, timeout: float = 10
) -> str:
    """
    Follows redirects to get the final torrent URL.
    :param initial_url: The initial URL to follow.
    :param session: A requests session to use for the requests.
    :param timeout: Timeout in seconds for each redirect request.
    :return: The final torrent URL.
    :raises: RuntimeError if it fails.
    """
    current_url = initial_url
    try:
        for _ in range(_MAX_TORRENT_PAYLOAD_REDIRECTS + 1):
            parsed = urlparse(current_url)
            if parsed.scheme in ("http", "https"):
                hostname, pinned_ip = _validate_torrent_http_url(current_url)
                with _dns_pin(hostname, pinned_ip):
                    response = session.get(
                        current_url,
                        allow_redirects=False,
                        timeout=timeout,
                        stream=True,
                    )
            else:
                response = session.get(
                    current_url,
                    allow_redirects=False,
                    timeout=timeout,
                    stream=True,
                )

            try:
                if 300 <= response.status_code < 400:
                    redirect_url = response.headers.get("Location")
                    if not redirect_url:
                        msg = "Redirect response without Location header"
                        raise RuntimeError(msg)

                    current_url = urljoin(current_url, redirect_url)
                    log.debug(
                        "Following redirect to: %s",
                        _redact_torrent_url(current_url),
                    )

                    if current_url.startswith("magnet:"):
                        return current_url
                else:
                    response.raise_for_status()
                    return current_url
            finally:
                response.close()
        else:
            msg = "Exceeded maximum number of redirects"
            raise RuntimeError(msg)

    except requests.exceptions.RequestException as e:
        log.debug(
            "An error occurred during the request for %s",
            _redact_torrent_url(initial_url),
            exc_info=True,
        )
        msg = "An error occurred during the request"
        raise RuntimeError(msg) from e
