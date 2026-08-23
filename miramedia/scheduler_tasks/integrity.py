"""SHA1 integrity audit scheduler task implementation."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from pathlib import Path

from miramedia.config import MiraMediaConfig
from miramedia.database import background_session
from miramedia.scheduler_tasks.locks import import_sweep_lock

log = logging.getLogger(__name__)

# Bound concurrent SHA1 hashing so the integrity audit doesn't saturate disk I/O.
_SHA1_CONCURRENCY = max(1, int(os.getenv("MIRAMEDIA_SHA1_CONCURRENCY", "4")))
_SHA1_SEM: asyncio.Semaphore | None = None


def get_sha1_semaphore() -> asyncio.Semaphore:
    """Lazy-init the semaphore so it's bound to the running event loop."""
    global _SHA1_SEM
    if _SHA1_SEM is None:
        _SHA1_SEM = asyncio.Semaphore(_SHA1_CONCURRENCY)
    return _SHA1_SEM


async def compute_sha1_async(path: Path) -> str | None:
    """Offload sync SHA1 hashing to a worker thread under a concurrency cap."""
    from miramedia.torrents.integrity import compute_sha1

    sem = get_sha1_semaphore()
    async with sem:
        return await asyncio.to_thread(compute_sha1, path)


async def hash_chunk_targets(chunk_targets: list[tuple]) -> list[tuple]:
    """Hash a bounded chunk concurrently; drop rows whose hash is unavailable."""
    if not chunk_targets:
        return []

    async def _hash_one(
        file_id: uuid.UUID,
        prior: str | None,
        prior_error: str | None,
        target: Path,
    ) -> tuple | None:
        sha = await compute_sha1_async(target)
        if sha is None:
            return None
        return (file_id, prior, prior_error, sha, target)

    results = await asyncio.gather(
        *(
            _hash_one(file_id, prior, prior_error, target)
            for file_id, prior, prior_error, target in chunk_targets
        )
    )
    return [result for result in results if result is not None]


async def verify_imported_files() -> None:
    """Lazy SHA1 baseline + integrity audit for imported files.

    First pass over a row populates ``sha1``; subsequent passes recompute and
    log a WARNING (and stamp ``import_error``) on mismatch. Skipped entirely
    when ``misc.integrity_check_enabled`` is off.

    Session lifetime: we snapshot the row PKs + paths under a short session,
    drop the session, hash each file off-pool, then re-open a short session
    per-batch to persist results. Previously the session was held open for
    the entire hash sweep — multi-hour walltime on a large library, pinning
    one connection ``idle in transaction`` the whole time.
    """
    if not MiraMediaConfig().misc.integrity_check_enabled:
        return

    lock = import_sweep_lock("integrity")
    if lock.locked():
        log.debug("Integrity sweep already running; skipping overlapping tick")
        return
    async with lock:
        from sqlalchemy import func, select

        from miramedia.file_status import ImportOutcome
        from miramedia.movies.models import MovieFile
        from miramedia.movies.repository import MovieRepository
        from miramedia.movies.schemas import MovieFile as MovieFileSchema
        from miramedia.shows.models import EpisodeFile
        from miramedia.shows.repository import ShowRepository
        from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
        from miramedia.torrents.integrity import (
            INTEGRITY_AUDIT_CHUNK_SIZE,
            IntegrityPathLayout,
            batch_resolve_episode_paths_async,
            batch_resolve_movie_paths_async,
        )

        baselined = 0
        verified = 0
        mismatched = 0
        skipped_stale = 0

        async def _apply_episode_result(
            show_repo: ShowRepository,
            file_id: uuid.UUID,
            prior: str | None,
            prior_error: str | None,
            sha: str,
            target: Path,
        ) -> None:
            nonlocal baselined, verified, mismatched, skipped_stale
            if prior is None:
                if await show_repo.apply_integrity_baseline_if_current(
                    file_id,
                    expected_sha1=None,
                    expected_import_error=prior_error,
                    new_sha1=sha,
                ):
                    baselined += 1
                else:
                    skipped_stale += 1
                    log.debug(
                        "integrity audit: skipped stale baseline for episode_file %s",
                        file_id,
                    )
            elif prior != sha:
                mismatch_error = (
                    f"sha1 mismatch (expected {prior[:10]}…, got {sha[:10]}…)"
                )
                if await show_repo.stamp_integrity_mismatch_if_current(
                    file_id,
                    expected_sha1=prior,
                    expected_import_error=prior_error,
                    import_error=mismatch_error,
                ):
                    mismatched += 1
                    log.warning(
                        "integrity audit: episode_file sha1 mismatch %s (%s)",
                        target,
                        file_id,
                    )
                else:
                    skipped_stale += 1
                    log.debug(
                        "integrity audit: skipped stale mismatch for episode_file %s",
                        file_id,
                    )
            else:
                verified += 1

        async def _apply_movie_result(
            movie_repo: MovieRepository,
            file_id: uuid.UUID,
            prior: str | None,
            prior_error: str | None,
            sha: str,
            target: Path,
        ) -> None:
            nonlocal baselined, verified, mismatched, skipped_stale
            if prior is None:
                if await movie_repo.apply_integrity_baseline_if_current(
                    file_id,
                    expected_sha1=None,
                    expected_import_error=prior_error,
                    new_sha1=sha,
                ):
                    baselined += 1
                else:
                    skipped_stale += 1
                    log.debug(
                        "integrity audit: skipped stale baseline for movie_file %s",
                        file_id,
                    )
            elif prior != sha:
                mismatch_error = (
                    f"sha1 mismatch (expected {prior[:10]}…, got {sha[:10]}…)"
                )
                if await movie_repo.stamp_integrity_mismatch_if_current(
                    file_id,
                    expected_sha1=prior,
                    expected_import_error=prior_error,
                    import_error=mismatch_error,
                ):
                    mismatched += 1
                    log.warning(
                        "integrity audit: movie_file sha1 mismatch %s (%s)",
                        target,
                        file_id,
                    )
                else:
                    skipped_stale += 1
                    log.debug(
                        "integrity audit: skipped stale mismatch for movie_file %s",
                        file_id,
                    )
            else:
                verified += 1

        layout = IntegrityPathLayout.from_config()

        ep_max_id: uuid.UUID | None = None
        ep_budget = 0
        mv_max_id: uuid.UUID | None = None
        mv_budget = 0
        async with background_session() as db:
            ep_max_id = (
                await db.execute(
                    select(EpisodeFile.id)
                    .where(EpisodeFile.import_status == ImportOutcome.imported)
                    .order_by(EpisodeFile.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            ep_budget = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(EpisodeFile)
                        .where(EpisodeFile.import_status == ImportOutcome.imported)
                    )
                ).scalar_one()
            )
            mv_max_id = (
                await db.execute(
                    select(MovieFile.id)
                    .where(MovieFile.import_status == ImportOutcome.imported)
                    .order_by(MovieFile.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            mv_budget = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(MovieFile)
                        .where(MovieFile.import_status == ImportOutcome.imported)
                    )
                ).scalar_one()
            )

        last_episode_id = uuid.UUID(int=0)
        remaining_ep_budget = ep_budget
        if remaining_ep_budget > 0 and ep_max_id is not None:
            while remaining_ep_budget > 0:
                row_snapshots: list[tuple] = []
                episode_context = {}
                shows = {}
                ep_schema_rows: list[EpisodeFileSchema] = []
                chunk_limit = min(INTEGRITY_AUDIT_CHUNK_SIZE, remaining_ep_budget)
                async with background_session() as db:
                    ep_result = await db.execute(
                        select(EpisodeFile)
                        .where(
                            EpisodeFile.import_status == ImportOutcome.imported,
                            EpisodeFile.id > last_episode_id,
                            EpisodeFile.id <= ep_max_id,
                        )
                        .order_by(EpisodeFile.id)
                        .limit(chunk_limit)
                    )
                    ep_rows = ep_result.scalars().all()
                    if not ep_rows:
                        break
                    last_episode_id = ep_rows[-1].id
                    remaining_ep_budget -= len(ep_rows)
                    show_repo = ShowRepository(db)
                    ep_schema_rows = [
                        EpisodeFileSchema.model_validate(row) for row in ep_rows
                    ]
                    episode_context = await show_repo.batch_episodes_with_context(
                        [
                            row.episode_id
                            for row in ep_schema_rows
                            if row.episode_id is not None
                        ]
                    )
                    shows = await show_repo.get_shows_by_ids(
                        list({ctx.show_id for ctx in episode_context.values()})
                    )
                    row_snapshots = [
                        (row.id, row.sha1, row.import_error, row) for row in ep_rows
                    ]

                paths = await batch_resolve_episode_paths_async(
                    ep_schema_rows,
                    episode_context,
                    shows,
                    layout,
                )

                chunk_targets: list[tuple] = []
                for file_id, prior, prior_error, _row in row_snapshots:
                    target = paths.get(file_id)
                    if target is None or not target.exists():
                        continue
                    chunk_targets.append((file_id, prior, prior_error, target))

                chunk_results = await hash_chunk_targets(chunk_targets)

                async with background_session() as db:
                    show_repo = ShowRepository(db)
                    for file_id, prior, prior_error, sha, target in chunk_results:
                        await _apply_episode_result(
                            show_repo, file_id, prior, prior_error, sha, target
                        )
                    await db.commit()

        last_movie_id = uuid.UUID(int=0)
        remaining_mv_budget = mv_budget
        if remaining_mv_budget > 0 and mv_max_id is not None:
            while remaining_mv_budget > 0:
                row_snapshots = []
                movies = {}
                mv_schema_rows: list[MovieFileSchema] = []
                chunk_limit = min(INTEGRITY_AUDIT_CHUNK_SIZE, remaining_mv_budget)
                async with background_session() as db:
                    mv_result = await db.execute(
                        select(MovieFile)
                        .where(
                            MovieFile.import_status == ImportOutcome.imported,
                            MovieFile.id > last_movie_id,
                            MovieFile.id <= mv_max_id,
                        )
                        .order_by(MovieFile.id)
                        .limit(chunk_limit)
                    )
                    mv_rows = mv_result.scalars().all()
                    if not mv_rows:
                        break
                    last_movie_id = mv_rows[-1].id
                    remaining_mv_budget -= len(mv_rows)
                    movie_repo = MovieRepository(db)
                    mv_schema_rows = [
                        MovieFileSchema.model_validate(row) for row in mv_rows
                    ]
                    movies = await movie_repo.get_movies_by_ids(
                        [
                            row.movie_id
                            for row in mv_schema_rows
                            if row.movie_id is not None
                        ]
                    )
                    row_snapshots = [
                        (row.id, row.sha1, row.import_error, row) for row in mv_rows
                    ]

                paths = await batch_resolve_movie_paths_async(
                    mv_schema_rows,
                    movies,
                    layout,
                )

                chunk_targets = []
                for file_id, prior, prior_error, _row in row_snapshots:
                    target = paths.get(file_id)
                    if target is None or not target.exists():
                        continue
                    chunk_targets.append((file_id, prior, prior_error, target))

                chunk_results = await hash_chunk_targets(chunk_targets)

                async with background_session() as db:
                    movie_repo = MovieRepository(db)
                    for file_id, prior, prior_error, sha, target in chunk_results:
                        await _apply_movie_result(
                            movie_repo, file_id, prior, prior_error, sha, target
                        )
                    await db.commit()

        log.info(
            "integrity audit: %d baselined, %d verified, %d MISMATCH, %d stale skipped",
            baselined,
            verified,
            mismatched,
            skipped_stale,
        )
        if mismatched:
            from miramedia.imports.queue_hooks import schedule_import_queue_rebuild

            schedule_import_queue_rebuild()
