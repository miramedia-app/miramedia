"""Integration tests for personalized Up Next queue."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from miramedia.file_status import ImportOutcome
from miramedia.movies.repository import MovieRepository
from miramedia.playback.models import PlaybackProgress
from miramedia.playback.repository import PlaybackRepository
from miramedia.playback.schemas import MediaKind, WatchStateUpdate
from miramedia.playback.service import PlaybackService
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.shows.repository import ShowRepository
from miramedia.torrents.schemas import Quality

pytestmark = pytest.mark.integration


async def _seed_user(db) -> uuid.UUID:
    from miramedia.auth.db import User

    user_id = uuid.uuid4()
    db.add(
        User(
            id=user_id,
            email=f"{user_id.hex}@test.local",
            hashed_password="x",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
    )
    await db.commit()
    return user_id


async def _service(db) -> PlaybackService:
    return PlaybackService(
        PlaybackRepository(db),
        MovieRepository(db),
        ShowRepository(db),
    )


async def _insert_show_with_episodes(
    db,
    *,
    name: str,
    episodes: list[tuple[int, int, str, bool]],
    files: dict[tuple[int, int], list[tuple[uuid.UUID, datetime | None, str]]]
    | None = None,
) -> tuple[Show, dict[tuple[int, int], list[EpisodeFile]]]:
    """Create a show. episodes = (season, ep_num, title, skipped).

    files maps (s,e) -> [(file_id, imported_at, variant)].
    """
    show_id = uuid.uuid4()
    show = Show(
        id=show_id,
        external_id=f"ext-{show_id.hex[:8]}",
        metadata_provider="native",
        name=name,
        overview="",
        year=2026,
    )
    db.add(show)
    seasons: dict[int, Season] = {}
    episode_rows: dict[tuple[int, int], Episode] = {}
    file_rows: dict[tuple[int, int], list[EpisodeFile]] = {}

    for season_num, ep_num, title, skipped in episodes:
        if season_num not in seasons:
            season_id = uuid.uuid4()
            seasons[season_num] = Season(
                id=season_id, show_id=show_id, number=season_num
            )
            db.add(seasons[season_num])
        episode_id = uuid.uuid4()
        episode = Episode(
            id=episode_id,
            season_id=seasons[season_num].id,
            number=ep_num,
            title=title,
            skipped=skipped,
        )
        db.add(episode)
        episode_rows[(season_num, ep_num)] = episode
        file_rows[(season_num, ep_num)] = []

    await db.flush()

    for key, file_specs in (files or {}).items():
        episode = episode_rows[key]
        for file_id, imported_at, variant in file_specs:
            ef = EpisodeFile(
                id=file_id,
                episode_id=episode.id,
                quality=Quality.hd,
                variant=variant,
                import_status=ImportOutcome.imported,
                imported_at=imported_at or datetime.now(UTC),
            )
            db.add(ef)
            file_rows[key].append(ef)

    await db.commit()
    return show, file_rows


async def _mark_watched(
    db, *, user_id: uuid.UUID, episode_id: uuid.UUID, watched: bool
) -> None:
    service = await _service(db)
    await service.set_watched(
        user_id=user_id,
        data=WatchStateUpdate(
            media_kind="episode",
            media_id=episode_id,
            watched=watched,
        ),
    )


async def _add_progress(
    db,
    *,
    user_id: uuid.UUID,
    episode_file_id: uuid.UUID,
    position_ms: int = 60_000,
    duration_ms: int = 100_000,
    completed: bool = False,
    updated_at: datetime | None = None,
) -> None:
    db.add(
        PlaybackProgress(
            user_id=user_id,
            episode_file_id=episode_file_id,
            position_ms=position_ms,
            duration_ms=duration_ms,
            completed=completed,
            updated_at=updated_at or datetime.now(UTC),
        )
    )
    await db.commit()


def test_up_next_returns_one_next_playable_unwatched_episode_per_started_show(
    db, run_async
) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        f1, f2 = uuid.uuid4(), uuid.uuid4()
        show, files = await _insert_show_with_episodes(
            db,
            name="Alpha Show",
            episodes=[(1, 1, "Pilot", False), (1, 2, "Second", False)],
            files={(1, 1): [(f1, None, "")], (1, 2): [(f2, None, "")]},
        )
        ep1 = next(iter(files[(1, 1)])).episode_id
        await _mark_watched(db, user_id=user_id, episode_id=ep1, watched=True)

        items = await PlaybackRepository(db).list_up_next(user_id=user_id, limit=20)

        assert len(items) == 1
        item = items[0]
        assert item.show_id == show.id
        assert item.season_number == 1
        assert item.episode_number == 2
        assert item.file_id == f2
        assert item.watched is False

    run_async(_run())


def test_up_next_skips_undownloaded_gap(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        f1, f3 = uuid.uuid4(), uuid.uuid4()
        _show, files = await _insert_show_with_episodes(
            db,
            name="Gap Show",
            episodes=[
                (1, 1, "One", False),
                (1, 2, "Two", False),
                (1, 3, "Three", False),
            ],
            files={(1, 1): [(f1, None, "")], (1, 3): [(f3, None, "")]},
        )
        ep1 = next(iter(files[(1, 1)])).episode_id
        await _mark_watched(db, user_id=user_id, episode_id=ep1, watched=True)

        items = await PlaybackRepository(db).list_up_next(user_id=user_id, limit=20)

        assert len(items) == 1
        assert items[0].episode_number == 3
        assert items[0].file_id == f3

    run_async(_run())


def test_up_next_excludes_specials_and_skipped_episodes(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        f_special, f_skip, f_regular = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        show, _files = await _insert_show_with_episodes(
            db,
            name="Filter Show",
            episodes=[
                (0, 1, "Special", False),
                (1, 1, "Skipped", True),
                (1, 2, "Next", False),
            ],
            files={
                (0, 1): [(f_special, None, "")],
                (1, 1): [(f_skip, None, "")],
                (1, 2): [(f_regular, None, "")],
            },
        )
        # Start the show via progress on the regular track (not specials).
        await _add_progress(
            db,
            user_id=user_id,
            episode_file_id=f_regular,
            position_ms=1_000,
            completed=False,
        )

        items = await PlaybackRepository(db).list_up_next(
            user_id=user_id, limit=20, include_specials=False
        )

        assert len(items) == 1
        assert items[0].episode_number == 2
        assert items[0].file_id == f_regular

        with_specials = await PlaybackRepository(db).list_up_next(
            user_id=user_id, limit=20, include_specials=True
        )
        assert len(with_specials) == 1
        assert with_specials[0].show_id == show.id

    run_async(_run())


def test_up_next_manual_unwatched_requeues_completed_episode(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        f1 = uuid.uuid4()
        _show, files = await _insert_show_with_episodes(
            db,
            name="Rewatch Show",
            episodes=[(1, 1, "Pilot", False)],
            files={(1, 1): [(f1, None, "")]},
        )
        ep1 = next(iter(files[(1, 1)])).episode_id
        service = await _service(db)
        await service.upsert_progress(
            user_id=user_id,
            data=__import__(
                "miramedia.playback.schemas", fromlist=["PlaybackProgressUpsert"]
            ).PlaybackProgressUpsert(
                file_id=f1,
                media_kind=MediaKind.episode,
                position_ms=95_000,
                duration_ms=100_000,
            ),
        )
        await _mark_watched(db, user_id=user_id, episode_id=ep1, watched=False)

        items = await PlaybackRepository(db).list_up_next(user_id=user_id, limit=20)

        assert len(items) == 1
        assert items[0].episode_number == 1
        assert items[0].file_id == f1

    run_async(_run())


def test_up_next_chooses_incomplete_progress_file_before_newest_import(
    db, run_async
) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        older_file, newer_file = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        _show, _files = await _insert_show_with_episodes(
            db,
            name="Dual File Show",
            episodes=[(1, 1, "Pilot", False)],
            files={
                (1, 1): [
                    (older_file, now - timedelta(days=2), "older"),
                    (newer_file, now, "newer"),
                ],
            },
        )
        await _add_progress(
            db,
            user_id=user_id,
            episode_file_id=older_file,
            position_ms=30_000,
            completed=False,
            updated_at=now - timedelta(hours=1),
        )

        items = await PlaybackRepository(db).list_up_next(user_id=user_id, limit=20)

        assert len(items) == 1
        assert items[0].file_id == older_file
        assert items[0].position_ms == 30_000

    run_async(_run())


def test_up_next_omits_never_started_show(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        f1 = uuid.uuid4()
        await _insert_show_with_episodes(
            db,
            name="Never Started",
            episodes=[(1, 1, "Pilot", False)],
            files={(1, 1): [(f1, None, "")]},
        )

        items = await PlaybackRepository(db).list_up_next(user_id=user_id, limit=20)

        assert items == []

    run_async(_run())


def test_up_next_is_owner_isolated_and_activity_sorted(db, run_async) -> None:
    async def _run() -> None:
        user_a = await _seed_user(db)
        user_b = await _seed_user(db)
        f_a, f_b = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        show_old, _ = await _insert_show_with_episodes(
            db,
            name="Bravo Show",
            episodes=[(1, 1, "Pilot", False), (1, 2, "Next", False)],
            files={(1, 1): [(f_a, None, "")], (1, 2): [(uuid.uuid4(), None, "")]},
        )
        show_new, _ = await _insert_show_with_episodes(
            db,
            name="Alpha Show",
            episodes=[(1, 1, "Pilot", False), (1, 2, "Next", False)],
            files={(1, 1): [(f_b, None, "")], (1, 2): [(uuid.uuid4(), None, "")]},
        )
        await _add_progress(
            db,
            user_id=user_a,
            episode_file_id=f_a,
            completed=False,
            updated_at=now - timedelta(days=2),
        )
        await _add_progress(
            db,
            user_id=user_a,
            episode_file_id=f_b,
            completed=False,
            updated_at=now - timedelta(hours=1),
        )
        await _add_progress(
            db,
            user_id=user_b,
            episode_file_id=f_b,
            completed=False,
            updated_at=now,
        )

        items_a = await PlaybackRepository(db).list_up_next(user_id=user_a, limit=20)
        items_b = await PlaybackRepository(db).list_up_next(user_id=user_b, limit=20)

        assert [item.show_name for item in items_a] == ["Alpha Show", "Bravo Show"]
        assert len(items_b) == 1
        assert items_b[0].show_id == show_new.id
        assert items_b[0].show_id != show_old.id

    run_async(_run())


def test_up_next_ignores_inactive_show_and_matches_active_only(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        f_active = uuid.uuid4()
        show_active, _files = await _insert_show_with_episodes(
            db,
            name="Active Show",
            episodes=[(1, 1, "Pilot", False), (1, 2, "Next", False)],
            files={(1, 1): [(f_active, None, "")], (1, 2): [(uuid.uuid4(), None, "")]},
        )
        # Library noise: fully imported show the user never touched.
        await _insert_show_with_episodes(
            db,
            name="Inactive Show",
            episodes=[(1, 1, "Pilot", False), (1, 2, "Next", False)],
            files={
                (1, 1): [(uuid.uuid4(), None, "")],
                (1, 2): [(uuid.uuid4(), None, "")],
            },
        )
        await _add_progress(
            db,
            user_id=user_id,
            episode_file_id=f_active,
            completed=True,
            position_ms=95_000,
        )

        repo = PlaybackRepository(db)
        items = await repo.list_up_next(user_id=user_id, limit=20)

        assert [item.show_name for item in items] == ["Active Show"]
        assert items[0].show_id == show_active.id
        assert items[0].episode_number == 2

    run_async(_run())


def test_up_next_query_plan_is_set_based(db, run_async) -> None:
    async def _run() -> None:
        user_id = await _seed_user(db)
        for idx in range(5):
            f1 = uuid.uuid4()
            await _insert_show_with_episodes(
                db,
                name=f"Plan Show {idx}",
                episodes=[(1, 1, "Pilot", False), (1, 2, "Next", False)],
                files={(1, 1): [(f1, None, "")], (1, 2): [(uuid.uuid4(), None, "")]},
            )
            await _add_progress(
                db,
                user_id=user_id,
                episode_file_id=f1,
                completed=True,
                position_ms=95_000,
            )

        repo = PlaybackRepository(db)
        plan = await repo.explain_up_next(user_id=user_id, limit=20)
        plan_text = "\n".join(row[0] for row in plan)

        # One round-trip set-based query (CTE pipeline), not per-show SQL from Python.
        assert any(token in plan_text for token in ("CTE Scan", "Hash Join", "Unique"))
        assert "Execution Time" in plan_text

    run_async(_run())
