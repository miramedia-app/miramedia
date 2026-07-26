"""On-demand HLS packaging via ffmpeg for browsers that cannot direct-play."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

_DIRECT_PLAY_EXTENSIONS = {".mp4", ".m4v", ".webm", ".mov"}
_HLS_SEGMENT_SECONDS = 4
_HLS_POLL_INTERVAL_S = 0.25
_HLS_START_TIMEOUT_S = 120.0

_inflight: dict[str, asyncio.Task[None]] = {}
_inflight_lock = asyncio.Lock()
_warm_tasks: set[asyncio.Task[Path]] = set()
_drain_tasks: set[asyncio.Task[None]] = set()


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


def hls_playlist_ready(source: Path) -> bool:
    """True when a cached playlist and at least one segment exist."""
    out_dir = segment_dir(source)
    playlist = out_dir / "index.m3u8"
    if not playlist.is_file():
        return False
    return any(out_dir.glob("seg_*.ts"))


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
        "vod",
        "-hls_flags",
        "independent_segments",
        "-hls_segment_filename",
        str(out_dir / "seg_%03d.ts"),
        str(playlist),
    ]


async def _wait_for_playlist_start(
    proc: subprocess.Popen[bytes],
    *,
    playlist: Path,
    out_dir: Path,
) -> None:
    """Return once the first segment exists; ffmpeg keeps running in the background."""
    deadline = time.monotonic() + _HLS_START_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if playlist.is_file() and any(out_dir.glob("seg_*.ts")):  # noqa: ASYNC240 — cheap stat, intentional
            return
        await asyncio.sleep(_HLS_POLL_INTERVAL_S)

    if proc.poll() is None:
        proc.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=5.0)
        except TimeoutError:
            proc.kill()
            await asyncio.to_thread(proc.wait)

    if playlist.is_file() and any(out_dir.glob("seg_*.ts")):  # noqa: ASYNC240 — cheap stat, intentional
        return
    msg = "HLS transcode failed or timed out waiting for first segment"
    raise HlsTranscodeError(msg)


async def _encode_hls(source: Path) -> None:
    out_dir = segment_dir(source)
    playlist = out_dir / "index.m3u8"
    if hls_playlist_ready(source):
        return

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        msg = "ffmpeg not available for transcoding"
        raise HlsTranscodeError(msg)

    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = _ffmpeg_cmd(ffmpeg, source, out_dir, playlist)

    def _popen() -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 — trusted args, no shell
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

    async def _drain_stderr() -> None:
        """Always read stderr and reap the process to avoid pipe-buffer deadlock/zombies."""
        if proc.stderr is not None:
            err = await asyncio.to_thread(proc.stderr.read)
        else:
            err = b""
        code = await asyncio.to_thread(proc.wait)
        if code != 0 and err:
            log.error(
                "HLS background encode failed for %s: %s",
                source.name,
                err.decode(errors="replace")[-2000:],
            )

    proc = await asyncio.to_thread(_popen)
    try:
        await _wait_for_playlist_start(proc, playlist=playlist, out_dir=out_dir)
        log.info("HLS ready for playback: %s", source.name)
        # ffmpeg may still be encoding remaining segments; drain stderr and reap it
        # in the background so a full pipe buffer never blocks the process.
        drain = asyncio.create_task(_drain_stderr())
        _drain_tasks.add(drain)
        drain.add_done_callback(_drain_tasks.discard)
    except Exception:
        if proc.poll() is None:
            proc.kill()
        await _drain_stderr()
        raise


async def ensure_hls_playlist(source: Path) -> Path:
    """Build (or reuse) an HLS playlist. Returns once the first segment is playable."""
    if hls_playlist_ready(source):
        return segment_dir(source) / "index.m3u8"

    key = cache_key_for(source)
    async with _inflight_lock:
        task = _inflight.get(key)
        if task is None:
            task = asyncio.create_task(_encode_hls(source))
            _inflight[key] = task

    try:
        # A waiter (playlist request) may be cancelled on client disconnect;
        # shield so cancellation stays local to that waiter and never kills
        # the shared encode other viewers / the warm task are awaiting.
        await asyncio.shield(task)
    finally:
        async with _inflight_lock:
            if _inflight.get(key) is task:
                _inflight.pop(key, None)

    playlist = segment_dir(source) / "index.m3u8"
    if not hls_playlist_ready(source):
        msg = "HLS playlist missing after transcode"
        raise HlsTranscodeError(msg)
    return playlist
