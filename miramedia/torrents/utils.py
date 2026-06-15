import logging
import re
import typing
from pathlib import Path
from urllib.parse import urljoin

import libtorrent
import requests
from pathvalidate import sanitize_filename
from requests.exceptions import InvalidSchema

from miramedia.config import MiraMediaConfig
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.schemas import Torrent

log = logging.getLogger(__name__)


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
    primary = completed / torrent.title
    if primary.exists():
        return primary

    sanitized = sanitize_filename(torrent.title)
    if sanitized and sanitized != torrent.title:
        sanitized_path = completed / sanitized
        if sanitized_path.exists():
            return sanitized_path

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
            # Refuse a sibling whose season/year identity conflicts with the
            # title's. Without this a same-franchise movie or a different
            # season pack — which share every title word — wins the overlap
            # score and the torrent imports from the WRONG directory.
            child_disc = _dir_discriminators(child.name)
            if title_disc and child_disc and title_disc.isdisjoint(child_disc):
                continue
            child_words = _torrent_dir_words(child.name)
            if not child_words:
                continue
            overlap = title_words & child_words
            if not overlap:
                continue
            score = len(overlap) / len(title_words)
            if best is None or score > best[0]:
                best = (score, child)
        # require at least ~60% of the title words to match so we don't fall
        # into a wildly unrelated sibling dir.
        if best is not None and best[0] >= 0.6:
            log.debug(
                "Resolved torrent %r to on-disk dir %s (overlap %.0f%%)",
                torrent.title,
                best[1],
                best[0] * 100,
            )
            return best[1]

    return primary


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


def _fetch_torrent_payload(url: str, title: str) -> bytes | None:
    """Return raw bytes of the ``.torrent`` file or ``None`` if unreachable.

    Follows tracker redirects that eventually resolve to a magnet — in which
    case there's nothing to fetch and we surface ``None`` so the caller can
    fall back to the magnet path.
    """
    try:
        response = requests.get(str(url), timeout=30)
        response.raise_for_status()
    except InvalidSchema:
        try:
            final_url = follow_redirects_to_final_torrent_url(
                initial_url=url,
                session=requests.Session(),
                timeout=MiraMediaConfig().indexers.timeout_seconds,
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
            response = requests.get(final_url, timeout=30)
            response.raise_for_status()
        except Exception:
            log.debug(
                "Failed to fetch .torrent at %s for inspection",
                final_url,
                exc_info=True,
            )
            return None
        else:
            return response.content
    except Exception:
        log.debug(
            "Failed to fetch .torrent at %s for inspection",
            url,
            exc_info=True,
        )
        return None
    else:
        return response.content


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
    torrent_filepath = (
        MiraMediaConfig().misc.effective_completed_path
        / f"{sanitize_filename(torrent.title)}.torrent"
    )
    if torrent_filepath.exists():
        log.warning(f"Torrent file already exists at: {torrent_filepath}")

    if torrent.download_url.startswith("magnet:"):
        log.info(f"Parsing torrent with magnet URL: {torrent.title}")
        log.debug(f"Magnet URL: {torrent.download_url}")
        torrent_hash = str(libtorrent.parse_magnet_uri(torrent.download_url).info_hash)
    else:
        # downloading the torrent file
        log.info(f"Downloading .torrent file of torrent: {torrent.title}")
        try:
            response = requests.get(str(torrent.download_url), timeout=30)
            response.raise_for_status()
            torrent_content = response.content
        except InvalidSchema:
            log.debug(f"Invalid schema for URL {torrent.download_url}", exc_info=True)
            final_url = follow_redirects_to_final_torrent_url(
                initial_url=torrent.download_url,
                session=requests.Session(),
                timeout=MiraMediaConfig().indexers.timeout_seconds,
            )
            return str(libtorrent.parse_magnet_uri(final_url).info_hash)
        except Exception:
            log.exception("Failed to download torrent file")
            raise

        # saving the torrent file
        torrent_filepath.write_bytes(torrent_content)

        # parsing info hash
        log.debug(f"parsing torrent file: {torrent.download_url}")
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
        for _ in range(10):  # Limit redirects to prevent infinite loops
            response = session.get(current_url, allow_redirects=False, timeout=timeout)

            if 300 <= response.status_code < 400:
                redirect_url = response.headers.get("Location")
                if not redirect_url:
                    msg = "Redirect response without Location header"
                    raise RuntimeError(msg)

                # Resolve relative redirects against the last URL
                current_url = urljoin(current_url, redirect_url)
                log.debug(f"Following redirect to: {current_url}")

                if current_url.startswith("magnet:"):
                    return current_url
            else:
                response.raise_for_status()  # Raise an exception for bad status codes
                return current_url
        else:
            msg = "Exceeded maximum number of redirects"
            raise RuntimeError(msg)

    except requests.exceptions.RequestException as e:
        log.debug(
            f"An error occurred during the request for {initial_url}",
            exc_info=True,
        )
        msg = "An error occurred during the request"
        raise RuntimeError(msg) from e
