"""Movie quality upgrade comparator and auto-download integration (design 309 Slice A)."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from miramedia.file_status import ImportOutcome
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.movies.quality_upgrades import (
    best_on_disk_library_quality,
    effective_quality_upgrades,
    filter_upgrade_candidates,
    is_strict_resolution_upgrade,
    library_satisfied_for_cutoff,
    quality_option_name_to_quality,
    quality_ordinal,
    resolve_upgrade_cutoff_quality,
)
from miramedia.movies.schemas import MovieFile
from miramedia.torrents.schemas import Quality
from tests.fakes import build_movie_service, run_async
from tests.fakes.repositories import FakeMovieRepository, make_movie


def _indexer_result(title: str) -> IndexerQueryResult:
    return IndexerQueryResult(
        title=title,
        download_url=f"magnet:?xt=urn:btih:{hash(title) & 0xFFFFFFFF:08x}",
        seeders=10,
        flags=[],
        size=2_000_000_000,
        usenet=False,
        age=1,
        indexer="x",
    )


class TestQualityUpgradeComparator:
    @pytest.mark.parametrize(
        ("quality", "expected"),
        [
            (Quality.uhd, 4),
            (Quality.fullhd, 3),
            (Quality.hd, 2),
            (Quality.sd, 1),
            (Quality.unknown, 0),
        ],
    )
    def test_quality_ordinal(self, quality: Quality, expected: int) -> None:
        assert quality_ordinal(quality) == expected

    @pytest.mark.parametrize(
        ("global_enabled", "movie_override", "expected"),
        [
            (False, None, False),
            (False, True, True),
            (False, False, False),
            (True, None, True),
            (True, True, True),
            (True, False, False),
        ],
    )
    def test_effective_quality_upgrades(
        self,
        global_enabled: bool,
        movie_override: bool | None,
        expected: bool,
    ) -> None:
        assert (
            effective_quality_upgrades(
                global_enabled=global_enabled,
                movie_override=movie_override,
            )
            is expected
        )

    def test_unknown_library_blocks_upgrade(self) -> None:
        assert not is_strict_resolution_upgrade(
            candidate=Quality.fullhd,
            best_library=Quality.unknown,
            cutoff=Quality.fullhd,
        )

    def test_unknown_candidate_blocks_upgrade(self) -> None:
        assert not is_strict_resolution_upgrade(
            candidate=Quality.unknown,
            best_library=Quality.hd,
            cutoff=Quality.fullhd,
        )

    def test_equal_ordinal_is_not_upgrade(self) -> None:
        assert not is_strict_resolution_upgrade(
            candidate=Quality.fullhd,
            best_library=Quality.fullhd,
            cutoff=Quality.uhd,
        )

    def test_downgrade_is_rejected(self) -> None:
        assert not is_strict_resolution_upgrade(
            candidate=Quality.hd,
            best_library=Quality.fullhd,
            cutoff=Quality.uhd,
        )

    def test_strictly_better_within_cutoff(self) -> None:
        assert is_strict_resolution_upgrade(
            candidate=Quality.fullhd,
            best_library=Quality.hd,
            cutoff=Quality.fullhd,
        )

    def test_above_cutoff_is_rejected(self) -> None:
        assert not is_strict_resolution_upgrade(
            candidate=Quality.uhd,
            best_library=Quality.hd,
            cutoff=Quality.fullhd,
        )

    def test_filter_upgrade_candidates_keeps_only_strict_upgrades(self) -> None:
        results = [
            _indexer_result("Movie.720p.HDTV"),
            _indexer_result("Movie.1080p.BluRay"),
            _indexer_result("Movie.2160p.UHD"),
        ]
        filtered = filter_upgrade_candidates(
            results,
            best_library=Quality.hd,
            cutoff=Quality.fullhd,
        )
        assert [result.title for result in filtered] == ["Movie.1080p.BluRay"]

    def test_best_on_disk_library_quality_skips_unknown(self) -> None:
        assert best_on_disk_library_quality([Quality.unknown, Quality.hd]) == Quality.hd
        assert best_on_disk_library_quality([Quality.unknown]) is None

    def test_library_satisfied_for_cutoff(self) -> None:
        assert library_satisfied_for_cutoff(
            best_library=Quality.fullhd,
            cutoff=Quality.fullhd,
        )
        assert not library_satisfied_for_cutoff(
            best_library=Quality.hd,
            cutoff=Quality.fullhd,
        )

    def test_resolve_cutoff_prefers_movie_override(self) -> None:
        from miramedia.indexers.config import QualityOption

        cutoff = resolve_upgrade_cutoff_quality(
            movie_cutoff_name="720p (HD)",
            global_cutoff_name="1080p (Full HD)",
            quality_options=[
                QualityOption(name="4K (UHD)", keywords=["2160p"], enabled=True),
            ],
        )
        assert cutoff == Quality.hd

    def test_quality_option_name_mapping(self) -> None:
        assert quality_option_name_to_quality("4K (UHD)") == Quality.uhd
        assert quality_option_name_to_quality("1080p (Full HD)") == Quality.fullhd
        assert quality_option_name_to_quality("720p (HD)") == Quality.hd
        assert quality_option_name_to_quality("SD") == Quality.sd


class TestMovieQualityUpgradeAutoDownload:
    def _bg_movie(self, svc):
        @asynccontextmanager
        async def fake_bg():
            yield svc

        return patch("miramedia.background_services.bg_movie_service", fake_bg)

    def _config_patch(self, *, quality_upgrades: bool = True):
        return patch(
            "miramedia.media_service.MiraMediaConfig",
            return_value=type(
                "Cfg",
                (),
                {
                    "misc": type(
                        "Misc",
                        (),
                        {
                            "quality_upgrades": quality_upgrades,
                            "upgrade_until_quality": None,
                            "auto_download_interval_hours": 1,
                        },
                    )(),
                    "indexers": type(
                        "Idx",
                        (),
                        {
                            "quality_options": [
                                type(
                                    "Opt",
                                    (),
                                    {
                                        "name": "4K (UHD)",
                                        "enabled": True,
                                    },
                                )(),
                                type(
                                    "Opt",
                                    (),
                                    {
                                        "name": "1080p (Full HD)",
                                        "enabled": True,
                                    },
                                )(),
                            ],
                        },
                    )(),
                },
            )(),
        )

    def test_default_off_skips_when_downloaded(self) -> None:
        movie = make_movie()
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        searched: list[bool] = []

        async def track(*, movie):
            _ = movie
            searched.append(True)
            return []

        with (
            self._bg_movie(svc),
            self._config_patch(quality_upgrades=False),
            patch.object(svc, "is_movie_downloaded", AsyncMock(return_value=True)),
            patch.object(svc, "get_all_available_torrents_for_movie", track),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert searched == []

    def test_upgrade_downloads_strictly_better_candidate(self) -> None:
        movie = make_movie(quality_upgrades=True)
        existing_file = MovieFile(
            id=uuid.uuid4(),
            movie_id=movie.id,
            quality=Quality.hd,
            import_status=ImportOutcome.imported,
        )
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        run_async(movie_repo.add_movie_file(existing_file))
        picked = _indexer_result("Movie.1080p.BluRay.x264")
        svc, _, _ = build_movie_service(movie_repo=movie_repo)

        with (
            self._bg_movie(svc),
            self._config_patch(quality_upgrades=True),
            patch.object(svc, "is_movie_downloaded", AsyncMock(return_value=True)),
            patch.object(
                svc,
                "get_on_disk_movie_file_qualities",
                AsyncMock(return_value=[Quality.hd]),
            ),
            patch.object(
                svc,
                "get_all_available_torrents_for_movie",
                AsyncMock(return_value=[picked, _indexer_result("Movie.720p.HDTV")]),
            ),
            patch.object(
                svc.torrent_service,
                "filter_deny_listed",
                AsyncMock(side_effect=lambda results: results),
            ),
            patch.object(
                svc, "_try_download_first_valid", AsyncMock(return_value=picked)
            ) as mock_download,
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        mock_download.assert_awaited_once()
        passed_results = mock_download.await_args.kwargs["results"]
        assert len(passed_results) == 1
        assert passed_results[0].quality == Quality.fullhd

    def test_satisfied_at_cutoff_skips_indexer_fan_out(self) -> None:
        movie = make_movie(
            quality_upgrades=True,
            upgrade_until_quality="1080p (Full HD)",
        )
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        searched: list[bool] = []

        async def track(*, movie):
            _ = movie
            searched.append(True)
            return []

        with (
            self._bg_movie(svc),
            self._config_patch(quality_upgrades=True),
            patch.object(svc, "is_movie_downloaded", AsyncMock(return_value=True)),
            patch.object(
                svc,
                "get_on_disk_movie_file_qualities",
                AsyncMock(return_value=[Quality.fullhd]),
            ),
            patch.object(svc, "get_all_available_torrents_for_movie", track),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert searched == []

    def test_unknown_on_disk_best_skips_upgrade(self) -> None:
        movie = make_movie(quality_upgrades=True)
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        searched: list[bool] = []

        async def track(*, movie):
            _ = movie
            searched.append(True)
            return []

        with (
            self._bg_movie(svc),
            self._config_patch(quality_upgrades=True),
            patch.object(svc, "is_movie_downloaded", AsyncMock(return_value=True)),
            patch.object(
                svc,
                "get_on_disk_movie_file_qualities",
                AsyncMock(return_value=[Quality.unknown]),
            ),
            patch.object(svc, "get_all_available_torrents_for_movie", track),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert searched == []

    def test_movie_override_false_blocks_global_on(self) -> None:
        movie = make_movie(quality_upgrades=False)
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        searched: list[bool] = []

        async def track(*, movie):
            _ = movie
            searched.append(True)
            return []

        with (
            self._bg_movie(svc),
            self._config_patch(quality_upgrades=True),
            patch.object(svc, "is_movie_downloaded", AsyncMock(return_value=True)),
            patch.object(svc, "get_all_available_torrents_for_movie", track),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert searched == []

    def test_active_torrent_blocks_upgrade(self) -> None:
        movie = make_movie(quality_upgrades=True)
        pending = MovieFile(
            id=uuid.uuid4(),
            movie_id=movie.id,
            quality=Quality.fullhd,
            torrent_id=uuid.uuid4(),
            import_status=ImportOutcome.pending,
        )
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        run_async(movie_repo.add_movie_file(pending))
        svc, _, _ = build_movie_service(movie_repo=movie_repo)
        searched: list[bool] = []

        async def track(*, movie):
            _ = movie
            searched.append(True)
            return []

        with (
            self._bg_movie(svc),
            self._config_patch(quality_upgrades=True),
            patch.object(svc, "is_movie_downloaded", AsyncMock(return_value=True)),
            patch.object(svc, "get_all_available_torrents_for_movie", track),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert searched == []

    def test_upgrade_never_deletes_existing_movie_file_rows(self) -> None:
        movie = make_movie(quality_upgrades=True)
        existing_file = MovieFile(
            id=uuid.uuid4(),
            movie_id=movie.id,
            quality=Quality.hd,
            import_status=ImportOutcome.imported,
            imported_at=datetime.now(UTC),
        )
        movie_repo = FakeMovieRepository()
        movie_repo.add_movie(movie)
        run_async(movie_repo.add_movie_file(existing_file))
        original_files = dict(movie_repo.movie_files)
        picked = _indexer_result("Movie.1080p.BluRay.x264")
        svc, _, _ = build_movie_service(movie_repo=movie_repo)

        with (
            self._bg_movie(svc),
            self._config_patch(quality_upgrades=True),
            patch.object(svc, "is_movie_downloaded", AsyncMock(return_value=True)),
            patch.object(
                svc,
                "get_on_disk_movie_file_qualities",
                AsyncMock(return_value=[Quality.hd]),
            ),
            patch.object(
                svc,
                "get_all_available_torrents_for_movie",
                AsyncMock(return_value=[picked]),
            ),
            patch.object(
                svc.torrent_service,
                "filter_deny_listed",
                AsyncMock(side_effect=lambda results: results),
            ),
            patch.object(
                svc, "_try_download_first_valid", AsyncMock(return_value=picked)
            ),
        ):
            from miramedia.movies.service import _try_auto_download_movie_id_impl

            run_async(_try_auto_download_movie_id_impl(movie.id))

        assert existing_file.id in movie_repo.movie_files
        assert movie_repo.movie_files[existing_file.id].quality == Quality.hd
        assert set(movie_repo.movie_files.keys()) >= set(original_files.keys())
