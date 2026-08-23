import logging
import typing
from pathlib import Path
from urllib.parse import urlparse

import libtorrent
import requests

from miramedia.config import MiraMediaConfig
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.torrents.fetch import (
    _fetch_torrent_payload,
    _guarded_fetch_torrent_bytes,
    _redact_torrent_url,
    follow_redirects_to_final_torrent_url,
)
from miramedia.torrents.paths import torrent_sidecar_under_root

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
        log.warning("Torrent file already exists at: %s", torrent_filepath)

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
        except Exception as exc:
            log.error(  # noqa: TRY400 — exception text embeds credential URL
                "Failed to download torrent file from %s (%s)",
                _redact_torrent_url(torrent.download_url),
                type(exc).__name__,
            )
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
