"""Plan 181 — bulk torrent endpoints batch their per-item lookups."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from miramedia.file_status import ImportOutcome
from miramedia.shows.schemas import (
    Episode,
    EpisodeFile,
    EpisodeId,
    EpisodeNumber,
    Season,
    SeasonId,
    SeasonNumber,
    Show,
    ShowId,
)
from miramedia.torrents.schemas import (
    ManualMapTargetType,
    Quality,
    TorrentSourceFile,
)
from tests.fakes.repositories import (
    FakeMovieRepository,
    FakeShowRepository,
    FakeTorrentRepository,
    make_movie,
    make_torrent,
)
from tests.fakes.services import (
    build_movie_service,
    build_show_service,
    build_torrent_service,
)

TORRENTS_PREFIX = "/api/v1/torrents"


def _show_with_episodes(*, episode_count: int = 2) -> tuple[Show, list[Episode]]:
    show_id = ShowId(uuid.uuid4())
    season_id = SeasonId(uuid.uuid4())
    episodes = [
        Episode(
            id=EpisodeId(uuid.uuid4()),
            number=EpisodeNumber(index + 1),
            title=f"Episode {index + 1}",
        )
        for index in range(episode_count)
    ]
    season = Season(
        id=season_id,
        show_id=show_id,
        number=SeasonNumber(1),
        episodes=episodes,
    )
    show = Show(
        id=show_id,
        name="Batch Lookup Show",
        overview="",
        year=2024,
        external_id="ext-batch-lookup",
        metadata_provider="native",
        seasons=[season],
    )
    return show, episodes


@contextmanager
def torrent_bulk_client(
    *,
    torrent_repo: FakeTorrentRepository,
    show_service,
    movie_service,
    torrent_service,
    torrent,
) -> Generator[TestClient]:
    from miramedia.auth.users import current_active_user, current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.movies.dependencies import get_movie_repository, get_movie_service
    from miramedia.shows.dependencies import get_show_repository, get_show_service
    from miramedia.torrents.dependencies import (
        get_torrent_by_id,
        get_torrent_repository,
        get_torrent_service,
    )

    async def _stub_session() -> Any:
        yield None

    async def _active_user() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        user.is_active = True
        user.is_verified = True
        return user

    async def _superuser() -> Any:
        return await _active_user()

    async def _torrent_dep() -> Any:
        return torrent

    prior = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_active_user] = _active_user
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_torrent_by_id] = _torrent_dep
    app.dependency_overrides[get_torrent_repository] = lambda: torrent_repo
    app.dependency_overrides[get_torrent_service] = lambda: torrent_service
    app.dependency_overrides[get_show_service] = lambda: show_service
    app.dependency_overrides[get_movie_service] = lambda: movie_service
    app.dependency_overrides[get_show_repository] = lambda: show_service.show_repository
    app.dependency_overrides[get_movie_repository] = lambda: (
        movie_service.movie_repository
    )
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior)


def test_bulk_retry_import_batches_torrent_and_media_lookups() -> None:
    show, episodes = _show_with_episodes(episode_count=1)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)

    torrents = [make_torrent(title=f"Batch.Retry.{index}") for index in range(3)]
    for torrent in torrents:
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.show_of_torrent[torrent.id] = show
        torrent_repo.episode_files[torrent.id] = [
            EpisodeFile(
                episode_id=episodes[0].id,
                torrent_id=torrent.id,
                quality=Quality.fullhd,
                codec="h264",
                hdr=False,
                source="web",
                variant="",
                extra="",
                import_status=ImportOutcome.imported,
            )
        ]

    show_service.import_show_from_torrent = AsyncMock()
    torrent_service.reset_import_status = AsyncMock()

    with torrent_bulk_client(
        torrent_repo=torrent_repo,
        show_service=show_service,
        movie_service=movie_service,
        torrent_service=torrent_service,
        torrent=torrents[0],
    ) as client:
        response = client.post(
            f"{TORRENTS_PREFIX}/bulk-retry-import",
            json={"torrent_ids": [str(t.id) for t in torrents]},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["succeeded"] == 3
    assert body["failed"] == []
    assert torrent_repo.get_torrents_by_ids_calls == 1
    assert torrent_repo.show_context_batch_calls == 1
    assert torrent_repo.movie_context_batch_calls == 1
    assert show_repo.get_show_by_id_calls == 3
    assert show_service.import_show_from_torrent.await_count == 3


def test_bulk_retry_re_fetches_show_after_prior_import_mutates_title() -> None:
    show, episodes = _show_with_episodes(episode_count=1)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)

    torrents = [make_torrent(title=f"Batch.Retry.Stale.{index}") for index in range(2)]
    for torrent in torrents:
        torrent_repo.torrents[torrent.id] = torrent
        torrent_repo.show_of_torrent[torrent.id] = show
        torrent_repo.episode_files[torrent.id] = [
            EpisodeFile(
                episode_id=episodes[0].id,
                torrent_id=torrent.id,
                quality=Quality.fullhd,
                codec="h264",
                hdr=False,
                source="web",
                variant="",
                extra="",
                import_status=ImportOutcome.imported,
            )
        ]

    seen_names: list[str] = []

    async def mutate_on_first(*, torrent, show):
        seen_names.append(show.name)
        if torrent.title == torrents[0].title:
            show_repo.shows[show.id] = show.model_copy(
                update={"name": "After mutation"}
            )

    show_service.import_show_from_torrent = AsyncMock(side_effect=mutate_on_first)
    torrent_service.reset_import_status = AsyncMock()

    with torrent_bulk_client(
        torrent_repo=torrent_repo,
        show_service=show_service,
        movie_service=movie_service,
        torrent_service=torrent_service,
        torrent=torrents[0],
    ) as client:
        response = client.post(
            f"{TORRENTS_PREFIX}/bulk-retry-import",
            json={"torrent_ids": [str(t.id) for t in torrents]},
        )

    assert response.status_code == 200, response.text
    assert seen_names == ["Batch Lookup Show", "After mutation"]


def test_bulk_retry_missing_torrent_isolates_successful_items() -> None:
    show, episodes = _show_with_episodes(episode_count=1)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)

    good = make_torrent(title="Batch.Retry.Good")
    missing_id = uuid.uuid4()
    torrent_repo.torrents[good.id] = good
    torrent_repo.show_of_torrent[good.id] = show
    torrent_repo.episode_files[good.id] = [
        EpisodeFile(
            episode_id=episodes[0].id,
            torrent_id=good.id,
            quality=Quality.fullhd,
            codec="h264",
            hdr=False,
            source="web",
            variant="",
            extra="",
            import_status=ImportOutcome.imported,
        )
    ]
    show_service.import_show_from_torrent = AsyncMock()
    torrent_service.reset_import_status = AsyncMock()

    with torrent_bulk_client(
        torrent_repo=torrent_repo,
        show_service=show_service,
        movie_service=movie_service,
        torrent_service=torrent_service,
        torrent=good,
    ) as client:
        response = client.post(
            f"{TORRENTS_PREFIX}/bulk-retry-import",
            json={"torrent_ids": [str(missing_id), str(good.id)]},
        )

    body = response.json()
    assert body["succeeded"] == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["torrent_id"] == str(missing_id)
    assert body["failed"][0]["error"] == "not found"
    assert show_service.import_show_from_torrent.await_count == 1


def test_bulk_retry_duplicate_ids_preserves_order_and_batches_once() -> None:
    show, episodes = _show_with_episodes(episode_count=1)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)

    torrent = make_torrent(title="Batch.Retry.Duplicate")
    torrent_repo.torrents[torrent.id] = torrent
    torrent_repo.show_of_torrent[torrent.id] = show
    torrent_repo.episode_files[torrent.id] = [
        EpisodeFile(
            episode_id=episodes[0].id,
            torrent_id=torrent.id,
            quality=Quality.fullhd,
            codec="h264",
            hdr=False,
            source="web",
            variant="",
            extra="",
            import_status=ImportOutcome.imported,
        )
    ]
    show_service.import_show_from_torrent = AsyncMock()
    torrent_service.reset_import_status = AsyncMock()

    with torrent_bulk_client(
        torrent_repo=torrent_repo,
        show_service=show_service,
        movie_service=movie_service,
        torrent_service=torrent_service,
        torrent=torrent,
    ) as client:
        response = client.post(
            f"{TORRENTS_PREFIX}/bulk-retry-import",
            json={"torrent_ids": [str(torrent.id), str(torrent.id)]},
        )

    body = response.json()
    assert body["succeeded"] == 1
    assert body["failed"] == []
    assert torrent_repo.get_torrents_by_ids_calls == 1
    assert torrent_repo.last_torrent_ids_batch == [torrent.id]
    assert show_service.import_show_from_torrent.await_count == 1


def test_bulk_retry_payload_over_max_length_returns_422() -> None:
    show, episodes = _show_with_episodes(episode_count=1)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)

    torrent = make_torrent(title="Batch.Retry.Cap")
    torrent_repo.torrents[torrent.id] = torrent
    torrent_repo.show_of_torrent[torrent.id] = show
    torrent_repo.episode_files[torrent.id] = [
        EpisodeFile(
            episode_id=episodes[0].id,
            torrent_id=torrent.id,
            quality=Quality.fullhd,
            codec="h264",
            hdr=False,
            source="web",
            variant="",
            extra="",
            import_status=ImportOutcome.imported,
        )
    ]

    with torrent_bulk_client(
        torrent_repo=torrent_repo,
        show_service=show_service,
        movie_service=movie_service,
        torrent_service=torrent_service,
        torrent=torrent,
    ) as client:
        response = client.post(
            f"{TORRENTS_PREFIX}/bulk-retry-import",
            json={"torrent_ids": [str(uuid.uuid4()) for _ in range(501)]},
        )

    assert response.status_code == 422


def test_bulk_retry_unexpected_value_error_hides_secret_path() -> None:
    show, episodes = _show_with_episodes(episode_count=1)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)

    torrent = make_torrent(title="Batch.Retry.Secret")
    torrent_repo.torrents[torrent.id] = torrent
    torrent_repo.show_of_torrent[torrent.id] = show
    torrent_repo.episode_files[torrent.id] = [
        EpisodeFile(
            episode_id=episodes[0].id,
            torrent_id=torrent.id,
            quality=Quality.fullhd,
            codec="h264",
            hdr=False,
            source="web",
            variant="",
            extra="",
            import_status=ImportOutcome.imported,
        )
    ]
    show_service.import_show_from_torrent = AsyncMock(
        side_effect=ValueError("driver failed: /secret/db/path")
    )
    torrent_service.reset_import_status = AsyncMock()

    with torrent_bulk_client(
        torrent_repo=torrent_repo,
        show_service=show_service,
        movie_service=movie_service,
        torrent_service=torrent_service,
        torrent=torrent,
    ) as client:
        response = client.post(
            f"{TORRENTS_PREFIX}/bulk-retry-import",
            json={"torrent_ids": [str(torrent.id)]},
        )

    body = response.json()
    assert body["succeeded"] == 0
    assert len(body["failed"]) == 1
    assert body["failed"][0]["error"] == "import failed"
    assert "/secret/" not in body["failed"][0]["error"]


def test_manual_map_batches_episode_and_show_lookups(tmp_path) -> None:
    show, episodes = _show_with_episodes(episode_count=2)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, movie_repo, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    torrent = make_torrent(title="Batch.Map.Show.S01")
    torrent_repo.torrents[torrent.id] = torrent

    for index, _episode in enumerate(episodes):
        media_file = tmp_path / f"ep{index + 1}.mkv"
        media_file.write_bytes(b"video")

    show_service.import_episode_from_file = AsyncMock(
        return_value=(ImportOutcome.imported, None)
    )

    items = [
        {
            "relative_path": f"ep{index + 1}.mkv",
            "target_type": ManualMapTargetType.episode.value,
            "episode_id": str(episode.id),
            "quality_override": Quality.fullhd.value,
        }
        for index, episode in enumerate(episodes)
    ]

    with patch(
        "miramedia.torrents.paths.get_torrent_filepath",
        return_value=tmp_path,
    ):
        with torrent_bulk_client(
            torrent_repo=torrent_repo,
            show_service=show_service,
            movie_service=movie_service,
            torrent_service=torrent_service,
            torrent=torrent,
        ) as client:
            response = client.post(
                f"{TORRENTS_PREFIX}/{torrent.id}/map",
                json={"items": items},
            )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mapped"] == 2
    assert body["failed"] == 0
    assert show_repo.get_episodes_with_seasons_calls == 1
    assert show_repo.get_shows_by_ids_calls == 1
    assert getattr(movie_repo, "get_movies_by_ids_calls", 0) == 0
    assert show_service.import_episode_from_file.await_count == 2


def test_manual_map_batches_duplicate_movie_ids_once(tmp_path) -> None:
    movie_repo = FakeMovieRepository()
    movie = make_movie(name="Batch Map Movie")
    movie_repo.add_movie(movie)
    torrent_repo = FakeTorrentRepository(movie_repo=movie_repo)
    show_service, show_repo, _ = build_show_service(torrent_repo=torrent_repo)
    movie_service, movie_repo, _ = build_movie_service(
        movie_repo=movie_repo, torrent_repo=torrent_repo
    )
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    torrent = make_torrent(title="Batch.Map.Movie")
    torrent_repo.torrents[torrent.id] = torrent

    for index in range(2):
        media_file = tmp_path / f"disc{index + 1}.mkv"
        media_file.write_bytes(b"video")

    movie_service.import_movie_from_file = AsyncMock(
        return_value=(ImportOutcome.imported, None)
    )

    items = [
        {
            "relative_path": f"disc{index + 1}.mkv",
            "target_type": ManualMapTargetType.movie.value,
            "movie_id": str(movie.id),
            "quality_override": Quality.fullhd.value,
        }
        for index in range(2)
    ]

    with patch(
        "miramedia.torrents.paths.get_torrent_filepath",
        return_value=tmp_path,
    ):
        with torrent_bulk_client(
            torrent_repo=torrent_repo,
            show_service=show_service,
            movie_service=movie_service,
            torrent_service=torrent_service,
            torrent=torrent,
        ) as client:
            response = client.post(
                f"{TORRENTS_PREFIX}/{torrent.id}/map",
                json={"items": items},
            )

    body = response.json()
    assert body["mapped"] == 2
    assert body["failed"] == 0
    assert movie_repo.get_movies_by_ids_calls == 1
    assert movie_repo.last_movie_ids_batch == [movie.id]
    assert getattr(movie_repo, "get_movie_by_id_calls", 0) == 0
    assert getattr(show_repo, "get_episodes_with_seasons_calls", 0) == 0
    assert movie_service.import_movie_from_file.await_count == 2


def test_manual_map_missing_episode_isolates_other_items(tmp_path) -> None:
    show, episodes = _show_with_episodes(episode_count=1)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    torrent = make_torrent(title="Batch.Map.Missing")
    torrent_repo.torrents[torrent.id] = torrent

    good_file = tmp_path / "good.mkv"
    bad_file = tmp_path / "bad.mkv"
    good_file.write_bytes(b"video")
    bad_file.write_bytes(b"video")
    missing_episode_id = uuid.uuid4()

    show_service.import_episode_from_file = AsyncMock(
        return_value=(ImportOutcome.imported, None)
    )

    items = [
        {
            "relative_path": "bad.mkv",
            "target_type": ManualMapTargetType.episode.value,
            "episode_id": str(missing_episode_id),
            "quality_override": Quality.fullhd.value,
        },
        {
            "relative_path": "good.mkv",
            "target_type": ManualMapTargetType.episode.value,
            "episode_id": str(episodes[0].id),
            "quality_override": Quality.fullhd.value,
        },
    ]

    with patch(
        "miramedia.torrents.paths.get_torrent_filepath",
        return_value=tmp_path,
    ):
        with torrent_bulk_client(
            torrent_repo=torrent_repo,
            show_service=show_service,
            movie_service=movie_service,
            torrent_service=torrent_service,
            torrent=torrent,
        ) as client:
            response = client.post(
                f"{TORRENTS_PREFIX}/{torrent.id}/map",
                json={"items": items},
            )

    body = response.json()
    assert body["mapped"] == 1
    assert body["failed"] == 1
    assert body["errors"][0].startswith("bad.mkv:")
    assert body["errors"][0].endswith("not found")
    assert show_service.import_episode_from_file.await_count == 1


def test_manual_map_preserves_item_order_with_skip(tmp_path) -> None:
    show, episodes = _show_with_episodes(episode_count=2)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    torrent = make_torrent(title="Batch.Map.Order")
    torrent_repo.torrents[torrent.id] = torrent

    for index in range(2):
        media_file = tmp_path / f"ep{index + 1}.mkv"
        media_file.write_bytes(b"video")

    show_service.import_episode_from_file = AsyncMock(
        return_value=(ImportOutcome.imported, None)
    )

    items = [
        {
            "relative_path": "ep1.mkv",
            "target_type": ManualMapTargetType.episode.value,
            "episode_id": str(episodes[0].id),
            "quality_override": Quality.fullhd.value,
        },
        {
            "relative_path": "skip.mkv",
            "target_type": ManualMapTargetType.skip.value,
        },
        {
            "relative_path": "ep2.mkv",
            "target_type": ManualMapTargetType.episode.value,
            "episode_id": str(episodes[1].id),
            "quality_override": Quality.fullhd.value,
        },
    ]

    with patch(
        "miramedia.torrents.paths.get_torrent_filepath",
        return_value=tmp_path,
    ):
        with torrent_bulk_client(
            torrent_repo=torrent_repo,
            show_service=show_service,
            movie_service=movie_service,
            torrent_service=torrent_service,
            torrent=torrent,
        ) as client:
            response = client.post(
                f"{TORRENTS_PREFIX}/{torrent.id}/map",
                json={"items": items},
            )

    body = response.json()
    assert body["mapped"] == 2
    assert body["skipped"] == 1
    assert body["failed"] == 0
    assert show_service.import_episode_from_file.await_count == 2


def test_dry_run_import_batches_episode_lookups() -> None:
    show, episodes = _show_with_episodes(episode_count=2)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    torrent = make_torrent(title="Batch.DryRun.Show.S01")
    torrent_repo.torrents[torrent.id] = torrent
    torrent_repo.show_of_torrent[torrent.id] = show

    sources = [
        TorrentSourceFile(
            relative_path=f"ep{index + 1}.mkv",
            size=1024,
            is_video=True,
            is_subtitle=False,
            suggested_episode_id=episode.id,
        )
        for index, episode in enumerate(episodes)
    ]
    torrent_service.list_source_files = AsyncMock(return_value=sources)
    torrent_service.get_show_of_torrent = AsyncMock(return_value=show)
    torrent_service.get_movie_of_torrent = AsyncMock(return_value=None)

    with torrent_bulk_client(
        torrent_repo=torrent_repo,
        show_service=show_service,
        movie_service=movie_service,
        torrent_service=torrent_service,
        torrent=torrent,
    ) as client:
        response = client.post(
            f"{TORRENTS_PREFIX}/{torrent.id}/import",
            params={"dry_run": "true"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["plan"]) == 2
    assert all(item["target_path"] for item in body["plan"])
    assert show_repo.get_episodes_with_seasons_calls == 1
    assert show_repo.last_episode_ids_batch == [episodes[0].id, episodes[1].id]


def test_dry_run_import_dedupes_duplicate_episode_ids_in_stable_order() -> None:
    show, episodes = _show_with_episodes(episode_count=1)
    show_repo = FakeShowRepository()
    show_repo.add_show(show)
    torrent_repo = FakeTorrentRepository(show_repo=show_repo)
    show_service, _, _ = build_show_service(
        show_repo=show_repo, torrent_repo=torrent_repo
    )
    movie_service, _, _ = build_movie_service(torrent_repo=torrent_repo)
    torrent_service, _ = build_torrent_service(torrent_repo=torrent_repo)
    torrent = make_torrent(title="Batch.DryRun.Duplicate")
    torrent_repo.torrents[torrent.id] = torrent
    torrent_repo.show_of_torrent[torrent.id] = show

    episode = episodes[0]
    sources = [
        TorrentSourceFile(
            relative_path="dup-a.mkv",
            size=1024,
            is_video=True,
            is_subtitle=False,
            suggested_episode_id=episode.id,
        ),
        TorrentSourceFile(
            relative_path="dup-b.mkv",
            size=1024,
            is_video=True,
            is_subtitle=False,
            suggested_episode_id=episode.id,
        ),
    ]
    torrent_service.list_source_files = AsyncMock(return_value=sources)
    torrent_service.get_show_of_torrent = AsyncMock(return_value=show)
    torrent_service.get_movie_of_torrent = AsyncMock(return_value=None)

    with torrent_bulk_client(
        torrent_repo=torrent_repo,
        show_service=show_service,
        movie_service=movie_service,
        torrent_service=torrent_service,
        torrent=torrent,
    ) as client:
        response = client.post(
            f"{TORRENTS_PREFIX}/{torrent.id}/import",
            params={"dry_run": "true"},
        )

    assert response.status_code == 200, response.text
    assert show_repo.get_episodes_with_seasons_calls == 1
    assert show_repo.last_episode_ids_batch == [episode.id]
    assert len(response.json()["plan"]) == 2
