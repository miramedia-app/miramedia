import os
from pathlib import Path

from miramedia.scheduler import evict_poster_variants


def _touch(path: Path, size: int, *, atime: float, mtime: float) -> None:
    path.write_bytes(b"x" * size)
    os.utime(path, (atime, mtime))


def test_orphaned_variant_removed(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    variant_dir = image_dir / ".variants"
    variant_dir.mkdir(parents=True)
    orphan = variant_dir / "missing-poster-400.jpg"
    orphan.write_bytes(b"orphan")

    deleted = evict_poster_variants(image_dir, variant_dir, max_total_bytes=1024)

    assert deleted == [orphan]
    assert not orphan.exists()


def test_under_cap_variants_untouched(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    variant_dir = image_dir / ".variants"
    variant_dir.mkdir(parents=True)
    source = image_dir / "poster.jpg"
    source.write_bytes(b"src")
    variant = variant_dir / "poster-400.jpg"
    variant.write_bytes(b"variant")

    deleted = evict_poster_variants(image_dir, variant_dir, max_total_bytes=1024)

    assert deleted == []
    assert variant.exists()


def test_over_cap_deletes_oldest_first(tmp_path: Path) -> None:
    image_dir = tmp_path / "images"
    variant_dir = image_dir / ".variants"
    variant_dir.mkdir(parents=True)

    for name in ("poster-a.jpg", "poster-b.jpg", "poster-c.jpg"):
        (image_dir / name).write_bytes(b"src")

    old = variant_dir / "poster-a-400.jpg"
    mid = variant_dir / "poster-b-400.jpg"
    new = variant_dir / "poster-c-400.jpg"
    _touch(old, 400, atime=1.0, mtime=1.0)
    _touch(mid, 400, atime=2.0, mtime=2.0)
    _touch(new, 400, atime=3.0, mtime=3.0)

    deleted = evict_poster_variants(image_dir, variant_dir, max_total_bytes=900)

    assert deleted == [old]
    assert not old.exists()
    assert mid.exists()
    assert new.exists()
