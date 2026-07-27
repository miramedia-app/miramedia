"""Thread-safety tests for the SRT→WebVTT TTL cache."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from miramedia.streams.router import _VTT_CACHE, _convert_srt_to_vtt

_SRT_BODY = """1
00:00:01,000 --> 00:00:04,000
Hello

"""


@pytest.fixture(autouse=True)
def _clear_vtt_cache() -> None:
    _VTT_CACHE.clear()


def test_concurrent_srt_to_vtt_cache(tmp_path: Path) -> None:
    files: list[Path] = []
    for i in range(4):
        srt = tmp_path / f"sub_{i}.srt"
        srt.write_text(_SRT_BODY, encoding="utf-8")
        files.append(srt)

    for batch in range(5):
        for srt in files:
            mtime = 1_000_000_000 + batch
            os.utime(srt, (mtime, mtime))

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [
                pool.submit(_convert_srt_to_vtt, files[i % len(files)])
                for i in range(200)
            ]
            for fut in as_completed(futures):
                assert fut.result().startswith("WEBVTT")
