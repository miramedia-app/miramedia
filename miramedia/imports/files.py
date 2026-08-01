"""Filesystem mechanics for importing media into the library.

These helpers are deliberately torrent-agnostic: they operate on plain
directories and files. The torrents module resolves a finished download to a
directory and hands that path here; the scanner hands a scanned directory here
directly. Either way the physical file work — archive extraction, video/subtitle
separation, hardlink/copy publishing, free-space checks — lives in the imports
domain, not the torrents domain.
"""

import glob
import logging
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path, UnsupportedOperation

from miramedia.imports.archive_extraction import (
    ArchiveExtractionError,
    classify_archive,
    extract_archive_to_directory,
)
from miramedia.imports.archive_publication import list_importable_files
from miramedia.torrents.parsing import (
    SubtitleInfo,
    is_sample_or_extra,
    is_subtitle_file,
    is_video_file,
)

log = logging.getLogger(__name__)


class DiskSpaceError(RuntimeError):
    """Raised when the import target lacks free space for the source file."""


class ImportConflictError(RuntimeError):
    """Raised when the target path exists with content from a different source."""


def files_matching_stem(directory: Path, stem: str) -> list[Path]:
    """Return files in ``directory`` whose name starts with ``stem + "."``.

    Equivalent to ``directory.glob(f"{stem}.*")`` but safe for stems
    containing glob metacharacters like ``[`` and ``]`` (which would
    otherwise be interpreted as character classes).
    """
    if not directory.exists() or not directory.is_dir():
        return []
    prefix = stem + "."
    try:
        return [
            p for p in directory.iterdir() if p.is_file() and p.name.startswith(prefix)
        ]
    except OSError:
        return []


def find_renamed_duplicate[K](
    source_file: Path,
    existing_paths: Mapping[K, Path],
) -> K | None:
    """Return the key whose on-disk file is the same content as ``source_file``.

    Detects media already present in the library under a *different* name — e.g.
    an older ``...1080p.mkv`` when re-importing the same content as
    ``...1080p [h265].mkv`` after the naming scheme gained a codec tag. Without
    this the importer treats the new name as a fresh variant and writes a
    second physical copy.

    A match is inode identity (definitive — the same physical file, which also
    covers re-scanning a file already in the library) or an exact byte-size
    match against a file in the *same media slot*. The caller restricts the keys
    to a single episode/movie, so a size collision between two genuinely
    different encodings of the same slot is astronomically unlikely.
    """
    try:
        src = source_file.stat()
    except OSError:
        return None
    for key, path in existing_paths.items():
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_ino == src.st_ino and st.st_dev == src.st_dev:
            return key
        if path != source_file and st.st_size == src.st_size:
            return key
    return None


def rename_media_slot(directory: Path, old_stem: str, new_stem: str) -> None:
    """Rename every file in ``directory`` whose name starts with ``old_stem.``.

    Moves the video and all its siblings (subtitles, ``.nfo``…) from the old
    canonical stem to ``new_stem`` in one shot, preserving each file's tail
    (language/flag/extension). A no-op when ``old_stem == new_stem``.
    """
    if old_stem == new_stem:
        return
    for f in files_matching_stem(directory, old_stem):
        tail = f.name[len(old_stem) :]
        dst = directory / (new_stem + tail)
        if f == dst:
            continue
        try:
            f.replace(dst)
        except OSError:
            log.warning("Failed to rename %s -> %s during slot rename", f, dst)


def list_files_recursively(path: Path = Path()) -> list[Path]:
    files = list(path.glob("**/*"))
    log.debug("Found %d entries via glob", len(files))
    valid_files = []
    for x in files:
        if x.is_dir():
            log.debug("'%s' is a directory", x)
        elif x.is_symlink():
            log.debug("'%s' is a symlink", x)
        else:
            valid_files.append(x)
    log.debug("Returning %d files after filtering", len(valid_files))
    return valid_files


def extract_archives(files: list) -> None:
    for file in files:
        classification = classify_archive(file)
        if classification is None:
            continue
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "File: %s, Size: %d bytes, classification: %s",
                file,
                file.stat().st_size,
                classification,
            )
        if classification.disposition == "unsupported":
            log.warning(
                "Skipping unsupported archive %s (format=%s)",
                file,
                classification.format,
            )
            continue
        log.info(
            "File %s is archive format %s, extracting safely into directory %s",
            file,
            classification.format,
            file.parent,
        )
        try:
            extract_archive_to_directory(file, file.parent)
        except ArchiveExtractionError:
            log.exception("Failed to safely extract archive %s", file)


def _raise_unless_same_content(
    source_file: Path,
    target_file: Path,
    *,
    phase: str,
) -> None:
    """Return on idempotent match; raise :class:`ImportConflictError` otherwise."""
    try:
        src_stat = source_file.stat()
        dst_stat = target_file.stat()
    except OSError:
        src_stat = dst_stat = None
    if (
        src_stat is not None
        and dst_stat is not None
        and (
            (dst_stat.st_ino == src_stat.st_ino and dst_stat.st_dev == src_stat.st_dev)
            or (target_file.is_file() and dst_stat.st_size == src_stat.st_size)
        )
    ):
        log.debug(
            "Target %s re-published by a concurrent import; treating as done",
            target_file,
        )
        return
    msg = f"Target {target_file} reappeared during {phase}"
    raise ImportConflictError(msg)


def _publish(
    tmp: Path,
    target: Path,
    *,
    source_file: Path,
    overwrite: bool,
) -> None:
    if overwrite:
        tmp.replace(target)
        return
    try:
        os.link(tmp, target)
    except FileExistsError:
        _raise_unless_same_content(source_file, target, phase="publish")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def import_file(
    target_file: Path,
    source_file: Path,
    *,
    overwrite: bool = True,
) -> None:
    """Hardlink ``source_file`` to ``target_file``, falling back to copy.

    If ``target_file`` already points at the same inode as ``source_file``
    the call is a no-op (idempotent re-imports). Cross-filesystem hardlinks
    fall back to ``shutil.copy``; failures from a missing or full target
    directory are surfaced as :class:`DiskSpaceError` so callers can record
    a structured import error.
    """

    if target_file.exists():
        try:
            src_stat = source_file.stat()
            dst_stat = target_file.stat()
        except OSError:
            src_stat = dst_stat = None
        if (
            src_stat is not None
            and dst_stat is not None
            and dst_stat.st_ino == src_stat.st_ino
            and dst_stat.st_dev == src_stat.st_dev
        ):
            log.debug("Target %s already linked to source, skipping", target_file)
            return
        # Copy-based idempotency (the NAS norm: source + library on different
        # volumes, so re-imports always have a distinct inode). A *complete*
        # prior copy has the same size as the source — treat it as already
        # imported instead of re-copying multi-GB on every retry. A truncated
        # file from an interrupted copy has a different size, so it falls
        # through and gets re-published atomically below.
        if (
            src_stat is not None
            and dst_stat is not None
            and target_file.is_file()
            and dst_stat.st_size == src_stat.st_size
        ):
            log.debug("Target %s already a full copy of source, skipping", target_file)
            return
        if not overwrite:
            msg = f"Target {target_file} already exists and overwrite=False"
            raise ImportConflictError(msg)

    # Atomic publish: link or copy to a temp file on the SAME directory/filesystem,
    # then os.replace() (atomic within a filesystem) into place. The canonical path
    # is only ever written by replace from a fully-published temp — never unlinked
    # up front. A kill/power-loss mid-copy leaves only the .mmpart temp, never a
    # truncated file at the canonical path the importer would mistake for finished.
    tmp_target = target_file.with_name(
        f"{target_file.name}.{uuid.uuid4().hex[:12]}.mmpart"
    )
    # Sweep stale temps from prior interrupted imports of this target. Unique
    # names mean nobody else's live temp matches ours; anything matching the
    # glob older than 24h is an orphan (best-effort cleanup).
    now = time.time()
    for stale in target_file.parent.glob(f"{glob.escape(target_file.name)}.*.mmpart"):
        try:
            st = stale.stat()
        except OSError:
            continue
        if now - st.st_mtime > 86400:
            try:
                stale.unlink()
            except OSError:
                pass

    try:
        tmp_target.hardlink_to(source_file)
    except FileExistsError:
        try:
            _raise_unless_same_content(source_file, target_file, phase="link")
        finally:
            try:
                tmp_target.unlink()
            except OSError:
                pass
        return
    except (OSError, UnsupportedOperation, NotImplementedError) as exc:
        log.warning(
            "Hardlink %s -> %s failed (%s); falling back to copy",
            source_file,
            target_file,
            exc,
        )
    else:
        # Hardlinks share the source inode's mtime; stamp now so the stale sweep
        # measures temp age, not download age. Also updates the source inode.
        try:
            os.utime(tmp_target, None)
        except OSError:
            pass
        _publish(
            tmp_target,
            target_file,
            source_file=source_file,
            overwrite=overwrite,
        )
        return

    try:
        ensure_free_space(target_file.parent, source_file.stat().st_size)
        shutil.copy(src=source_file, dst=tmp_target)
        try:
            os.utime(tmp_target, None)
        except OSError:
            pass
        _publish(
            tmp_target,
            target_file,
            source_file=source_file,
            overwrite=overwrite,
        )
    except OSError as exc:
        try:
            tmp_target.unlink()
        except OSError:
            pass
        msg = f"Failed to copy {source_file} to {target_file}: {exc}"
        raise DiskSpaceError(msg) from exc


def ensure_free_space(target_dir: Path, required_bytes: int) -> None:
    """Raise :class:`DiskSpaceError` if ``target_dir`` lacks ``required_bytes`` free."""
    probe = target_dir
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError as exc:
        log.warning("disk_usage probe failed for %s: %s", probe, exc)
        return
    if free < required_bytes:
        msg = f"Need {required_bytes} bytes at {target_dir}, only {free} free"
        raise DiskSpaceError(msg)


def delete_files_matching_stems(directory: Path, stems: Iterable[str]) -> None:
    for stem in stems:
        for f in files_matching_stem(directory, stem):
            try:
                f.unlink(missing_ok=True)
                log.info(f"Deleted file: {f}")
            except OSError:
                log.warning(f"Failed to delete file: {f}", exc_info=True)


def link_subtitles(
    subtitle_files: Sequence[Path],
    *,
    match: Callable[[Path], SubtitleInfo | None],
    target_for: Callable[[SubtitleInfo, int], Path],
) -> None:
    """Link parsed subtitle files next to a target video, disambiguating collisions."""
    used: set[Path] = set()
    for sub in subtitle_files:
        sub_info = match(sub)
        if sub_info is None:
            continue
        target_sub = target_for(sub_info, 1)
        # Disambiguate same-lang+flag collisions (in-batch only, to keep
        # re-imports idempotent) — otherwise the second sub silently clobbers
        # the first.
        if target_sub in used:
            n = 2
            while True:
                candidate = target_for(sub_info, n)
                if candidate not in used:
                    target_sub = candidate
                    break
                n += 1
        used.add(target_sub)
        try:
            import_file(target_file=target_sub, source_file=sub)
        except DiskSpaceError:
            log.exception("Disk space error importing subtitle %s", sub)


def link_video_into_slot(
    directory: Path,
    source_file: Path,
    stem: str,
    target_video: Path,
    *,
    source_in_place: bool,
) -> None:
    if source_in_place:
        rename_media_slot(directory, source_file.stem, stem)
        return
    ensure_free_space(target_video.parent, source_file.stat().st_size)
    import_file(target_file=target_video, source_file=source_file)


def get_files_for_import(
    directory: Path,
) -> tuple[list[Path], list[Path], list[Path]]:
    """Collect importable files from a directory, extracting archives first.

    Returns a tuple of: video files, subtitle files, and all files found in the
    directory. Callers resolving a torrent to its on-disk directory should do so
    in the torrents domain (``get_torrent_filepath``) and hand the resulting
    path here.
    """
    log.info(f"Importing files from directory {directory}")
    search_directory = directory

    all_files: list[Path] = list_importable_files(search_directory)
    log.debug("Found %d files in the directory", len(all_files))
    extract_archives(all_files)
    all_files = list_importable_files(search_directory)

    video_files: list[Path] = []
    subtitle_files: list[Path] = []
    for file in all_files:
        if is_sample_or_extra(file):
            log.debug("Skipping sample/extra: %s", file)
            continue
        if is_video_file(file):
            video_files.append(file)
            log.debug("File is a video, it will be imported: %s", file)
        elif is_subtitle_file(file):
            subtitle_files.append(file)
            log.debug("File is a subtitle, it will be imported: %s", file)
        else:
            log.debug(
                "File is neither a video nor a subtitle, will not be imported: %s",
                file,
            )

    log.info(
        f"Found {len(all_files)} files ({len(video_files)} video files, {len(subtitle_files)} subtitle files) for further processing."
    )
    return video_files, subtitle_files, all_files


_SEASON_DIR_RE = re.compile(r"^(season|s)\s*\d+$|^specials$", re.IGNORECASE)


def walk_importable_media_directories(
    roots: list[Path],
    ignored_paths: set[str] | None = None,
) -> list[Path]:
    """Walk all roots recursively, returning candidate media directories.

    A "media directory" is the closest ancestor of a video file that is NOT
    itself named like a season (``Season 01``, ``S1``, ...). For movies this
    is the parent dir of the file; for shows it is the show root above the
    season dirs.

    Pruning rules (skip subtree entirely):
    * directories starting with ``.`` (hidden)
    * directories containing a ``.mmignore`` marker file
    * any path in ``ignored_paths`` (absolute string match)
    * any of the configured library roots themselves are not pruned, but their
      absolute path is excluded from the result set
    """
    ignored_paths = ignored_paths or set()
    ignored_abs = {Path(p).absolute() for p in ignored_paths}
    root_abs_set = {r.absolute() for r in roots}

    results: dict[Path, None] = {}

    def _resolve_media_dir(file_parent: Path, root: Path) -> Path:
        """Walk up while parent is a season-like dir, stopping before root."""
        media_dir = file_parent
        while (
            media_dir != root
            and media_dir.parent != media_dir
            and _SEASON_DIR_RE.match(media_dir.name) is not None
        ):
            media_dir = media_dir.parent
        return media_dir

    # Guard against symlink loops: a symlinked dir pointing at an ancestor
    # (common on NAS collection setups) would otherwise recurse forever →
    # RecursionError aborts the whole scan. Skip symlinked dirs and never
    # re-enter an already-visited real path.
    visited: set[Path] = set()

    def _walk(directory: Path, root: Path) -> None:
        try:
            real = directory.resolve()
        except OSError:
            return
        if real in visited:
            return
        visited.add(real)
        try:
            children = list(directory.iterdir())
        except (OSError, PermissionError):
            return
        if any(c.name == ".mmignore" for c in children):
            return
        for child in children:
            if child.name.startswith("."):
                continue
            abs_child = child.absolute()
            if abs_child in ignored_abs:
                continue
            if child.is_file():
                if is_video_file(child):
                    media_dir = _resolve_media_dir(child.parent, root)
                    abs_md = media_dir.absolute()
                    if abs_md in root_abs_set or abs_md in ignored_abs:
                        continue
                    results.setdefault(media_dir, None)
            elif child.is_dir() and not child.is_symlink():
                _walk(child, root)

    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        if root.absolute() in ignored_abs:
            continue
        _walk(root, root)

    return list(results.keys())
