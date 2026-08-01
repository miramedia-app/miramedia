"""On-demand HLS packaging via ffmpeg for browsers that cannot direct-play.

Encodes into a per-run temp directory under ``hls_cache_root()`` and atomically
publishes to the final key directory on success. Multi-worker deployments may
encode the same source concurrently into different temp dirs (wasted CPU;
last-publish-wins is harmless when another worker already published).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)

_DIRECT_PLAY_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov"}
_HLS_SEGMENT_SECONDS = 4
_HLS_POLL_INTERVAL_S = 0.25
_HLS_START_TIMEOUT_S = 120.0
_TMP_DIR_PREFIX = ".tmp-"
_TMP_REAP_AGE_S = 3600.0

_COMPLETE_MARKER = ".complete"

_inflight: dict[str, _InflightEntry] = {}
_inflight_lock = asyncio.Lock()
_warm_tasks: set[asyncio.Task[Path]] = set()


@dataclass
class _InflightEntry:
    task: asyncio.Task[None]
    tmp_dir: Path


def _complete_marker(out_dir: Path) -> Path:
    return out_dir / _COMPLETE_MARKER


def hls_cache_root() -> Path:
    root = Path(os.getenv("MIRAMEDIA_HLS_CACHE", "/app/images/hls"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def can_direct_play(path: Path) -> bool:
    return path.suffix.lower() in _DIRECT_PLAY_EXTENSIONS


def hls_transcode_available() -> bool:
    """Whether server-side HLS packaging is possible in this environment."""
    return shutil.which("ffmpeg") is not None


class HlsTranscodeError(RuntimeError):
    """Raised when HLS packaging cannot run (missing ffmpeg or encode failure)."""


def cache_key_for(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


def playlist_path(source: Path) -> Path:
    return hls_cache_root() / cache_key_for(source) / "index.m3u8"


def segment_dir(source: Path) -> Path:
    return hls_cache_root() / cache_key_for(source)


def _dir_hls_started(out_dir: Path) -> bool:
    playlist = out_dir / "index.m3u8"
    if not playlist.is_file():
        return False
    return any(out_dir.glob("seg_*.ts"))


def hls_playlist_started(source: Path) -> bool:
    """True when a cached playlist and at least one segment exist (encode may still run)."""
    return _dir_hls_started(segment_dir(source))


def hls_playlist_ready(source: Path) -> bool:
    """True when a fully-encoded cached playlist exists (marker written on ffmpeg exit 0)."""
    out_dir = segment_dir(source)
    if not (out_dir / "index.m3u8").is_file():
        return False
    if not _complete_marker(out_dir).is_file():
        return False
    return any(out_dir.glob("seg_*.ts"))


def current_hls_dir(source: Path) -> Path | None:
    """Published cache dir, or the in-flight temp dir while encoding."""
    if hls_playlist_ready(source):
        return segment_dir(source)
    entry = _inflight.get(cache_key_for(source))
    if entry is not None and entry.tmp_dir.is_dir():
        return entry.tmp_dir
    out_dir = segment_dir(source)
    if _dir_hls_started(out_dir):
        return out_dir
    return None


def schedule_hls_warm(source: Path) -> None:
    """Encode HLS in the background; never blocks the caller."""
    if not hls_transcode_available() or hls_playlist_ready(source):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(ensure_hls_playlist(source))
    _warm_tasks.add(task)

    def _on_done(t: asyncio.Task[Path]) -> None:
        _warm_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is None:
            return
        if isinstance(exc, HlsTranscodeError):
            log.error("Background HLS warm failed for %s: %s", source.name, exc)
        else:
            log.error("Background HLS warm crashed for %s", source.name, exc_info=exc)

    task.add_done_callback(_on_done)


def _ffmpeg_cmd(ffmpeg: str, source: Path, out_dir: Path, playlist: Path) -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-f",
        "hls",
        "-hls_time",
        str(_HLS_SEGMENT_SECONDS),
        "-hls_playlist_type",
        "event",
        "-hls_flags",
        "independent_segments",
        "-hls_segment_filename",
        str(out_dir / "seg_%03d.ts"),
        str(playlist),
    ]


def _reap_stale_tmp_dirs() -> None:
    root = hls_cache_root()
    now = time.time()
    for path in root.iterdir():
        if not path.name.startswith(_TMP_DIR_PREFIX) or not path.is_dir():
            continue
        newest_mtime = _newest_file_mtime(path)
        if newest_mtime is None:
            try:
                newest_mtime = path.stat().st_mtime
            except OSError:
                continue
        if now - newest_mtime > _TMP_REAP_AGE_S:
            shutil.rmtree(path, ignore_errors=True)


def _newest_file_mtime(directory: Path) -> float | None:
    newest: float | None = None
    for file_path in directory.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def _publish_tmp_dir(tmp_dir: Path, out_dir: Path) -> None:
    """Atomically move a completed temp encode into the final cache slot."""
    if _complete_marker(out_dir).is_file():
        # TOCTOU: another worker may publish between the check and replace — acceptable.
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    tmp_dir.replace(out_dir)


async def _wait_for_playlist_start(
    proc: subprocess.Popen[bytes],
    *,
    out_dir: Path,
) -> None:
    """Return once the first segment exists; ffmpeg keeps running in the background."""
    deadline = time.monotonic() + _HLS_START_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if _dir_hls_started(out_dir):
            return
        await asyncio.sleep(_HLS_POLL_INTERVAL_S)

    if proc.poll() is None:
        proc.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=5.0)
        except TimeoutError:
            proc.kill()
            await asyncio.to_thread(proc.wait)

    if _dir_hls_started(out_dir):
        return
    msg = "HLS transcode failed or timed out waiting for first segment"
    raise HlsTranscodeError(msg)


async def _wait_until_hls_playable(
    source: Path,
    encode_task: asyncio.Task[None],
    tmp_dir: Path,
) -> None:
    """Return once the first segment exists; observe encode failures."""
    out_dir = segment_dir(source)
    while True:
        if _dir_hls_started(tmp_dir) or _dir_hls_started(out_dir):
            return
        if encode_task.done():
            exc = encode_task.exception()
            if exc is not None and not isinstance(
                exc, (asyncio.CancelledError, HlsTranscodeError)
            ):
                log.error(
                    "HLS encode task failed for %s",
                    source.name,
                    exc_info=exc,
                )
            await encode_task
            if _dir_hls_started(tmp_dir) or _dir_hls_started(out_dir):
                return
            msg = "HLS playlist missing after transcode"
            raise HlsTranscodeError(msg)
        await asyncio.sleep(_HLS_POLL_INTERVAL_S)


def _resolve_playlist_path(source: Path, tmp_dir: Path) -> Path:
    if hls_playlist_ready(source):
        return segment_dir(source) / "index.m3u8"
    if _dir_hls_started(tmp_dir):
        playlist = tmp_dir / "index.m3u8"
        if playlist.is_file():
            return playlist
    published = segment_dir(source) / "index.m3u8"
    if published.is_file():
        return published
    return segment_dir(source) / "index.m3u8"


async def _encode_hls(source: Path, _key: str, tmp_dir: Path) -> None:
    out_dir = segment_dir(source)
    if hls_playlist_ready(source):
        return

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        msg = "ffmpeg not available for transcoding"
        raise HlsTranscodeError(msg)

    _reap_stale_tmp_dirs()

    await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)
    playlist = tmp_dir / "index.m3u8"
    cmd = _ffmpeg_cmd(ffmpeg, source, tmp_dir, playlist)

    def _popen() -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 — trusted args, no shell
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    proc = await asyncio.to_thread(_popen)
    drained = False

    async def _drain_stderr() -> None:
        """Always read stderr and reap the process to avoid pipe-buffer deadlock/zombies."""
        nonlocal drained
        if drained:
            return
        drained = True
        if proc.stderr is not None:
            err = await asyncio.to_thread(proc.stderr.read)
        else:
            err = b""
        code = await asyncio.to_thread(proc.wait)
        if code == 0:
            _complete_marker(tmp_dir).touch()
            try:
                _publish_tmp_dir(tmp_dir, out_dir)
            except OSError:
                log.error(
                    "HLS publish failed for %s",
                    source.name,
                    exc_info=True,
                )
        else:
            msg = f"HLS background encode failed for {source.name} (exit code {code})"
            if err:
                msg = f"{msg}: {err.decode(errors='replace')[-2000:]}"
            log.error(msg)
            shutil.rmtree(tmp_dir, ignore_errors=True)

    try:
        await _wait_for_playlist_start(proc, out_dir=tmp_dir)
        log.info("HLS ready for playback: %s", source.name)
        # Keep this task (and the _inflight entry) alive until ffmpeg exits and the
        # completion marker is written — waiters return earlier via _wait_until_hls_playable.
        await _drain_stderr()
    except Exception:
        if proc.poll() is None:
            proc.kill()
        await _drain_stderr()
        if await asyncio.to_thread(tmp_dir.is_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


async def ensure_hls_playlist(source: Path) -> Path:
    """Build (or reuse) an HLS playlist. Returns once the first segment is playable."""
    if hls_playlist_ready(source):
        return segment_dir(source) / "index.m3u8"

    key = cache_key_for(source)
    async with _inflight_lock:
        entry = _inflight.get(key)
        if entry is None:
            tmp_dir = hls_cache_root() / f"{_TMP_DIR_PREFIX}{key}-{uuid4().hex[:8]}"
            task = asyncio.create_task(_encode_hls(source, key, tmp_dir))
            entry = _InflightEntry(task=task, tmp_dir=tmp_dir)
            _inflight[key] = entry

            def _cleanup(t: asyncio.Task[None], *, _key: str = key) -> None:
                # Done-callbacks run on the loop; dict ops are atomic there, so
                # no need to take the async lock (and we can't await here).
                current = _inflight.get(_key)
                if current is not None and current.task is t:
                    _inflight.pop(_key, None)

            task.add_done_callback(_cleanup)

    # A waiter may be cancelled on client disconnect; shield so cancellation stays
    # local to that waiter and never kills the shared encode. Wait only until the
    # first segment is playable — not until ffmpeg finishes.
    await asyncio.shield(_wait_until_hls_playable(source, entry.task, entry.tmp_dir))

    return _resolve_playlist_path(source, entry.tmp_dir)


_SWEEP_RECENCY_S = 3600.0
_STALE_INCOMPLETE_AGE_S = 3600.0
_LASTREAD_NAME = ".lastread"
_LASTREAD_TOUCH_INTERVAL_S = 60.0
# Never reap dirs read within this window during tier-two budget pressure.
_TIER_TWO_RECENCY_FLOOR_S = 300.0


def _lastread_path(dir_path: Path) -> Path:
    return dir_path / _LASTREAD_NAME


def _touch_last_read(dir_path: Path) -> None:
    """Update ``.lastread`` mtime so sweeps see recent playback on noatime mounts."""
    path = _lastread_path(dir_path)
    now = time.time()
    if path.is_file():
        try:
            if now - path.stat().st_mtime < _LASTREAD_TOUCH_INTERVAL_S:
                return
        except OSError:
            pass
    path.touch()


def _lastread_mtime(dir_path: Path) -> float:
    path = _lastread_path(dir_path)
    try:
        if path.is_file():
            return path.stat().st_mtime
    except OSError:
        pass
    return 0.0


def _cache_key_for_dir(dir_path: Path) -> str | None:
    name = dir_path.name
    if name.startswith(_TMP_DIR_PREFIX):
        rest = name[len(_TMP_DIR_PREFIX) :]
        if len(rest) < 32:
            return None
        return rest[:32]
    if len(name) == 32 and all(ch in "0123456789abcdef" for ch in name.lower()):
        return name
    return None


def _dir_cache_stats(dir_path: Path) -> tuple[int, float, float]:
    total = 0
    newest_mtime = 0.0
    newest_atime = 0.0
    for file_path in dir_path.rglob("*"):
        if not file_path.is_file():
            continue
        if file_path.name == _LASTREAD_NAME:
            continue
        try:
            stat = file_path.stat()
        except OSError:
            continue
        total += stat.st_size
        newest_mtime = max(newest_mtime, stat.st_mtime)
        newest_atime = max(newest_atime, stat.st_atime)
    if total == 0 and newest_mtime == 0.0:
        try:
            stat = dir_path.stat()
        except OSError:
            return 0, 0.0, 0.0
        newest_mtime = stat.st_mtime
        newest_atime = stat.st_atime
    return total, newest_mtime, newest_atime


def _is_inflight_cache_dir(
    dir_path: Path,
    inflight_tmp_dirs: set[Path],
    inflight_keys: set[str],
) -> bool:
    resolved = dir_path.resolve()
    if resolved in inflight_tmp_dirs:
        return True
    key = _cache_key_for_dir(dir_path)
    return key is not None and key in inflight_keys


def sweep_hls_cache(max_bytes: int, max_age_s: float) -> dict[str, int]:
    """Delete expired and over-budget HLS cache directories.

    Recency uses ``.lastread`` mtime (written on segment GET) plus newest atime
    (mtime fallback) so dirs actively streamed are not evicted on noatime mounts.
    """
    now = time.time()
    root = hls_cache_root()
    inflight_tmp_dirs = {e.tmp_dir.resolve() for e in list(_inflight.values())}
    inflight_keys = set(_inflight)
    candidates: list[tuple[Path, int, float, bool, bool]] = []
    tier_two: list[tuple[Path, int, float]] = []
    total_bytes = 0
    skipped_recent_dirs = 0

    for dir_path in root.iterdir():
        if not dir_path.is_dir():
            continue
        if _cache_key_for_dir(dir_path) is None:
            continue

        size, newest_mtime, newest_atime = _dir_cache_stats(dir_path)
        total_bytes += size

        if _is_inflight_cache_dir(dir_path, inflight_tmp_dirs, inflight_keys):
            continue

        recency_ts = newest_atime if newest_atime > 0 else newest_mtime
        lastread_mtime = _lastread_mtime(dir_path)
        if lastread_mtime > recency_ts:
            recency_ts = lastread_mtime

        is_recent = recency_ts > 0 and now - recency_ts < _SWEEP_RECENCY_S
        if is_recent:
            skipped_recent_dirs += 1
            tier_two.append((dir_path, size, recency_ts))
            continue

        complete = _complete_marker(dir_path).is_file()
        stale_incomplete = (
            not complete
            and newest_mtime > 0
            and (now - newest_mtime >= _STALE_INCOMPLETE_AGE_S)
        )
        if not complete and not stale_incomplete:
            continue

        expired = newest_mtime > 0 and (now - newest_mtime > max_age_s)
        candidates.append((dir_path, size, newest_mtime, expired, stale_incomplete))

    to_delete: list[Path] = [
        path
        for path, _, _, expired, stale_incomplete in candidates
        if expired or stale_incomplete
    ]
    freed_bytes = sum(
        size
        for path, size, _, expired, stale_incomplete in candidates
        if expired or stale_incomplete
    )
    remaining_bytes = total_bytes - freed_bytes

    if remaining_bytes > max_bytes:
        budget_candidates = sorted(
            [
                (path, size, mtime)
                for path, size, mtime, expired, stale_incomplete in candidates
                if not expired and not stale_incomplete
            ],
            key=lambda item: item[2],
        )
        for path, size, _mtime in budget_candidates:
            if remaining_bytes <= max_bytes:
                break
            to_delete.append(path)
            freed_bytes += size
            remaining_bytes -= size

    if remaining_bytes > max_bytes:
        tier_two_candidates = sorted(
            [
                (path, size, recency_ts)
                for path, size, recency_ts in tier_two
                if now - recency_ts >= _TIER_TWO_RECENCY_FLOOR_S
            ],
            key=lambda item: item[2],
        )
        if tier_two_candidates:
            log.warning(
                "HLS cache over budget (%d bytes); evicting %d recent dir(s) "
                "older than %.0f s",
                remaining_bytes,
                len(tier_two_candidates),
                _TIER_TWO_RECENCY_FLOOR_S,
            )
            for path, size, _recency_ts in tier_two_candidates:
                if remaining_bytes <= max_bytes:
                    break
                to_delete.append(path)
                freed_bytes += size
                remaining_bytes -= size

    if remaining_bytes > max_bytes:
        log.warning(
            "HLS cache still over budget after sweep (%d bytes remaining, limit %d)",
            remaining_bytes,
            max_bytes,
        )

    deleted_dirs = 0
    for path in to_delete:
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            deleted_dirs += 1

    return {
        "deleted_dirs": deleted_dirs,
        "freed_bytes": freed_bytes,
        "remaining_bytes": remaining_bytes,
        "skipped_recent_dirs": skipped_recent_dirs,
    }
