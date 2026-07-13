import asyncio
import time
from pathlib import Path

import pytest

from miramedia.core import router as core_router


def test_concurrent_same_key_generates_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}
    generated: dict[tuple[str, int], Path] = {}

    def fake_fresh(file_path: Path, width: int) -> Path | None:
        return generated.get((str(file_path), width))

    def fake_generate(file_path: Path, width: int) -> Path | None:
        calls["count"] += 1
        time.sleep(0.05)
        variant = Path(f"{file_path}.w{width}")
        generated[(str(file_path), width)] = variant
        return variant

    monkeypatch.setattr(core_router, "_fresh_poster_variant", fake_fresh)
    monkeypatch.setattr(core_router, "_generate_poster_variant", fake_generate)

    file_path = Path("poster-a.jpg")
    width = 400

    async def run() -> list[Path | None]:
        return list(
            await asyncio.gather(
                core_router._poster_variant_async(file_path, width),
                core_router._poster_variant_async(file_path, width),
                core_router._poster_variant_async(file_path, width),
            )
        )

    results = asyncio.run(run())

    assert calls["count"] == 1
    assert len(set(results)) == 1
    assert results[0] == Path(f"{file_path}.w{width}")


def test_distinct_keys_generate_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}
    generated: dict[tuple[str, int], Path] = {}

    def fake_fresh(file_path: Path, width: int) -> Path | None:
        return generated.get((str(file_path), width))

    def fake_generate(file_path: Path, width: int) -> Path | None:
        calls["count"] += 1
        time.sleep(0.05)
        variant = Path(f"{file_path}.w{width}")
        generated[(str(file_path), width)] = variant
        return variant

    monkeypatch.setattr(core_router, "_fresh_poster_variant", fake_fresh)
    monkeypatch.setattr(core_router, "_generate_poster_variant", fake_generate)

    path_a = Path("poster-a.jpg")
    path_b = Path("poster-b.jpg")

    async def run() -> list[Path | None]:
        return list(
            await asyncio.gather(
                core_router._poster_variant_async(path_a, 400),
                core_router._poster_variant_async(path_b, 400),
            )
        )

    results = asyncio.run(run())

    assert calls["count"] == 2
    assert results[0] == Path(f"{path_a}.w400")
    assert results[1] == Path(f"{path_b}.w400")
