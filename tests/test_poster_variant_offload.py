import asyncio
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from miramedia.core import router as core_router


def _make_poster(path: Path, *, width: int = 800, height: int = 1200) -> None:
    image = Image.new("RGB", (width, height), color=(120, 80, 40))
    image.save(path, format="JPEG", quality=90)


def _patch_image_dir(monkeypatch: pytest.MonkeyPatch, image_dir: Path) -> None:
    monkeypatch.setattr(core_router.config.misc, "image_directory", image_dir)


async def _gather_variants(file_path: Path, width: int) -> list[Path | None]:
    return list(
        await asyncio.gather(
            core_router._poster_variant_async(file_path, width),
            core_router._poster_variant_async(file_path, width),
        )
    )


def test_generate_poster_variant_async(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    source = image_dir / "poster.jpg"
    _make_poster(source, width=800, height=1200)
    _patch_image_dir(monkeypatch, image_dir)

    variant = asyncio.run(core_router._poster_variant_async(source, 400))

    assert variant is not None
    assert variant.exists()
    with Image.open(variant) as image:
        assert image.width <= 400
        assert image.height <= int(400 * 1.5)


def test_single_flight_generates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    source = image_dir / "poster.jpg"
    _make_poster(source)
    _patch_image_dir(monkeypatch, image_dir)

    calls = {"count": 0}
    real_generate = core_router._generate_poster_variant

    def counting_generate(file_path: Path, width: int) -> Path | None:
        calls["count"] += 1
        time.sleep(0.05)
        return real_generate(file_path, width)

    monkeypatch.setattr(core_router, "_generate_poster_variant", counting_generate)

    results = asyncio.run(_gather_variants(source, 400))

    assert calls["count"] == 1
    assert results[0] is not None
    assert results[0] == results[1]


def test_fresh_variant_skips_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    variant_dir = image_dir / ".variants"
    variant_dir.mkdir(parents=True)
    source = image_dir / "poster.jpg"
    _make_poster(source)
    variant = variant_dir / "poster-400.jpg"
    _make_poster(variant, width=400, height=600)
    os.utime(source, (1.0, 1.0))
    os.utime(variant, (2.0, 2.0))
    _patch_image_dir(monkeypatch, image_dir)

    calls = {"count": 0}

    def skip_generate(_file_path: Path, _width: int) -> Path | None:
        calls["count"] += 1
        return None

    monkeypatch.setattr(core_router, "_generate_poster_variant", skip_generate)

    result = asyncio.run(core_router._poster_variant_async(source, 400))

    assert calls["count"] == 0
    assert result == variant


def test_corrupt_image_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    source = image_dir / "poster.jpg"
    source.write_bytes(b"not-an-image")
    _patch_image_dir(monkeypatch, image_dir)

    result = asyncio.run(core_router._poster_variant_async(source, 400))

    assert result is None
