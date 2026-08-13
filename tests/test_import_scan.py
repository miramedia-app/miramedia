"""Characterization tests for library scan matching and orchestration phases."""

from __future__ import annotations

import asyncio
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from miramedia.imports.scan import scan_libraries
from miramedia.imports.scan_matching import (
    ProviderSearchCollaborator,
    ScanFilesystemCollector,
    assemble_scan_candidates,
    build_existing_media_indexes,
    detect_name_year,
    resolve_existing_media,
)
from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.torrents.schemas import MediaType
from tests.fakes.repositories import make_show


def _make_search_result(
    *,
    name: str,
    year: int | None = None,
    external_id: str = "123",
    metadata_provider: str = "tmdb",
    imdb_id: str | None = None,
) -> MetaDataProviderSearchResult:
    return MetaDataProviderSearchResult(
        poster_path=None,
        overview="overview",
        name=name,
        external_id=external_id,
        imdb_id=imdb_id,
        year=year,
        metadata_provider=metadata_provider,
        added=False,
    )


class _StubProvider:
    def __init__(
        self,
        name: str,
        *,
        show_results: list[MetaDataProviderSearchResult] | None = None,
        movie_results: list[MetaDataProviderSearchResult] | None = None,
        fail: bool = False,
    ) -> None:
        self._name = name
        self._show_results = show_results or []
        self._movie_results = movie_results or []
        self._fail = fail

    @property
    def name(self) -> str:
        return self._name

    def search_show(self, query: str | None = None, skip: int = 0):  # noqa: ARG002
        if self._fail:
            msg = "provider down"
            raise RuntimeError(msg)
        return self._show_results

    def search_movie(self, query: str | None = None, skip: int = 0):  # noqa: ARG002
        if self._fail:
            msg = "provider down"
            raise RuntimeError(msg)
        return self._movie_results


def test_detect_name_year_parses_parenthetical_year() -> None:
    name, year = detect_name_year("Breaking Bad (2008) [tmdbid-1396]")
    assert name == "Breaking Bad"
    assert year == 2008


def test_detect_name_year_strips_provider_tag_without_year() -> None:
    name, year = detect_name_year("Some Title [imdb-tt1234567]")
    assert name == "Some Title"
    assert year is None


def test_resolve_existing_by_imdb_tag() -> None:
    show = make_show(name="Tagged Show")
    show = show.model_copy(update={"imdb_id": "tt9999999"})
    indexes = build_existing_media_indexes([show], [])
    hit = resolve_existing_media(
        MediaType.show,
        "Tagged Show (2008) [imdb-tt9999999]",
        indexes,
    )
    assert hit is show


def test_resolve_existing_by_external_provider_tag() -> None:
    show = make_show(name="Native Show")
    show = show.model_copy(
        update={"external_id": "1396", "metadata_provider": "tmdb", "imdb_id": None}
    )
    indexes = build_existing_media_indexes([show], [])
    hit = resolve_existing_media(
        MediaType.show,
        "Native Show (2008) [tmdbid-1396]",
        indexes,
    )
    assert hit is show


def test_resolve_existing_tt_external_for_imdb_provider_tag() -> None:
    show = make_show(name="TT External")
    show = show.model_copy(
        update={
            "external_id": "tt1234567",
            "metadata_provider": "native",
            "imdb_id": None,
        }
    )
    indexes = build_existing_media_indexes([show], [])
    hit = resolve_existing_media(
        MediaType.show,
        "TT External (2020) [imdb-tt1234567]",
        indexes,
    )
    assert hit is show


def test_assemble_candidates_promotes_provider_tag_match_to_confidence_one() -> None:
    show = make_show(name="Exact Match", year=2020)
    candidates = assemble_scan_candidates(
        f"Exact Match (2020) [nativeid-{show.external_id}]",
        MediaType.show,
        [show],
        [],
        show,
    )
    assert candidates[0].media_id == show.id
    assert candidates[0].confidence == 1.0


def test_assemble_candidates_fuzzy_name_year_match() -> None:
    show = make_show(name="Bend It Like Beckham", year=2002)
    candidates = assemble_scan_candidates(
        "Bend It Like Beckham (2002)",
        MediaType.show,
        [show],
        [],
        None,
    )
    assert candidates
    assert candidates[0].media_id == show.id
    assert candidates[0].confidence > 0.3


def test_provider_search_merges_duplicate_imdb_candidates() -> None:
    dup = _make_search_result(
        name="Shared Title",
        year=2020,
        external_id="1",
        metadata_provider="tmdb",
        imdb_id="tt1111111",
    )
    providers = [
        _StubProvider("a", show_results=[dup]),
        _StubProvider(
            "b",
            show_results=[
                _make_search_result(
                    name="Shared Title Alt",
                    year=2020,
                    external_id="2",
                    metadata_provider="tvdb",
                    imdb_id="tt1111111",
                )
            ],
        ),
    ]
    collab = ProviderSearchCollaborator(providers, max_results=5)
    results = collab.search(MediaType.show, "Shared Title", 2020)
    assert len(results) == 1
    assert results[0].imdb_id == "tt1111111"


def test_provider_search_keeps_higher_confidence_on_duplicate_key() -> None:
    weak = _make_search_result(
        name="Weak Match Extra Words",
        year=1999,
        external_id="10",
        metadata_provider="tmdb",
    )
    strong = _make_search_result(
        name="Strong Match",
        year=2020,
        external_id="10",
        metadata_provider="tmdb",
    )
    collab = ProviderSearchCollaborator(
        [_StubProvider("tmdb", show_results=[weak, strong])],
        max_results=5,
    )
    results = collab.search(MediaType.show, "Strong Match", 2020)
    assert len(results) == 1
    assert results[0].name == "Strong Match"
    assert results[0].confidence > 0.3


def test_provider_search_orders_by_confidence_descending() -> None:
    low = _make_search_result(name="Low Match Words", year=2020, external_id="1")
    high = _make_search_result(name="High Match", year=2020, external_id="2")
    collab = ProviderSearchCollaborator(
        [_StubProvider("tmdb", show_results=[low, high])],
        max_results=5,
    )
    results = collab.search(MediaType.show, "High Match", 2020)
    assert len(results) == 2
    assert results[0].confidence >= results[1].confidence


def test_provider_search_skips_low_confidence_hits() -> None:
    noise = _make_search_result(
        name="Completely Different", year=1990, external_id="99"
    )
    collab = ProviderSearchCollaborator(
        [_StubProvider("tmdb", show_results=[noise])],
        max_results=5,
    )
    results = collab.search(MediaType.show, "Target Title", 2024)
    assert results == []


def test_provider_search_continues_when_provider_raises() -> None:
    good = _make_search_result(name="Survivor", year=2021, external_id="7")
    collab = ProviderSearchCollaborator(
        [
            _StubProvider("bad", fail=True),
            _StubProvider("good", show_results=[good]),
        ],
        max_results=5,
    )
    results = collab.search(MediaType.show, "Survivor", 2021)
    assert len(results) == 1
    assert results[0].name == "Survivor"


@pytest.fixture
def scan_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    show_root = tmp_path / "shows"
    movie_root = tmp_path / "movies"
    show_root.mkdir()
    movie_root.mkdir()

    misc = types.SimpleNamespace(
        show_directory=show_root,
        movie_directory=movie_root,
        show_libraries=(),
        movie_libraries=(),
        auto_pick_confidence_threshold=0.8,
    )
    imports = types.SimpleNamespace(
        provider_search_on_scan=False,
        provider_search_max_results=5,
    )
    monkeypatch.setattr(
        "miramedia.config.MiraMediaConfig",
        lambda: types.SimpleNamespace(misc=misc, imports=imports),
    )
    monkeypatch.setattr(
        "miramedia.imports.scan.MiraMediaConfig",
        lambda: types.SimpleNamespace(misc=misc, imports=imports),
    )
    return show_root, movie_root


def test_ignored_paths_excluded_from_scan_results(
    scan_config: tuple[Path, Path],
) -> None:
    show_root, _ = scan_config
    visible = show_root / "Visible Show (2020)"
    ignored = show_root / "Ignored Show (2021)"
    visible.mkdir()
    ignored.mkdir()
    (visible / "ep.mkv").touch()
    (ignored / "ep.mkv").touch()

    collab = ProviderSearchCollaborator([], max_results=5)
    indexes = build_existing_media_indexes([], [])
    collector = ScanFilesystemCollector(
        roots=[(show_root, "Default")],
        media_type=MediaType.show,
        ignored_paths={str(ignored.absolute())},
        ignored_abs={str(ignored.absolute())},
        existing_shows=[],
        existing_movies=[],
        indexes=indexes,
        show_imported_stems={},
        movie_imported_stems={},
        provider_search=collab,
        provider_search_on_scan=False,
        auto_pick_threshold=0.8,
    )
    items = collector.collect()
    directories = {item.directory for item in items}
    assert str(visible) in directories
    assert str(ignored) not in directories


def test_scan_libraries_releases_services_before_filesystem_walk(
    scan_config: tuple[Path, Path],
) -> None:
    show_root, movie_root = scan_config
    show_dir = show_root / "Pending Show (2022)"
    show_dir.mkdir()
    (show_dir / "S01").mkdir()
    (show_dir / "S01" / "show.mkv").touch()
    movie_dir = movie_root / "Pending Movie (2023)"
    movie_dir.mkdir()
    (movie_dir / "movie.mkv").touch()

    order: list[str] = []

    async def snapshot_shows() -> list:
        order.append("snapshot_shows")
        return []

    async def snapshot_movies() -> list:
        order.append("snapshot_movies")
        return []

    async def snapshot_movie_files(_ids: list) -> dict:
        order.append("snapshot_movie_files")
        return {}

    show_service = MagicMock()
    show_service.get_all_shows = AsyncMock(side_effect=snapshot_shows)
    movie_service = MagicMock()
    movie_service.get_all_movies = AsyncMock(side_effect=snapshot_movies)
    movie_service.movie_repository.get_movie_files_for_movies = AsyncMock(
        side_effect=snapshot_movie_files
    )

    def _collect_stub(*_args, **_kwargs) -> list:
        order.append("collect")
        return []

    with patch(
        "miramedia.imports.scan.ScanFilesystemCollector.collect",
        side_effect=_collect_stub,
    ):
        asyncio.run(
            scan_libraries(
                set(),
                show_service=show_service,
                movie_service=movie_service,
            )
        )

    assert order.index("snapshot_shows") < order.index("collect")
    assert order.index("snapshot_movies") < order.index("collect")
    assert order.index("snapshot_movie_files") < order.index("collect")


def test_scan_libraries_end_to_end_name_year_directory(
    scan_config: tuple[Path, Path],
) -> None:
    show_root, _ = scan_config
    show = make_show(name="Documentary Special", year=2022)
    show_dir = show_root / "Documentary Special (2022)"
    show_dir.mkdir()
    (show_dir / "film.mkv").touch()

    show_service = MagicMock()
    show_service.get_all_shows = AsyncMock(return_value=[show])
    movie_service = MagicMock()
    movie_service.get_all_movies = AsyncMock(return_value=[])
    movie_service.movie_repository.get_movie_files_for_movies = AsyncMock(
        return_value={}
    )

    with patch(
        "miramedia.metadata.dependencies.get_all_enabled_providers",
        return_value=[],
    ):
        response = asyncio.run(
            scan_libraries(
                set(),
                show_service=show_service,
                movie_service=movie_service,
            )
        )

    show_items = [
        item for item in response.items if item.media_type_hint == MediaType.show
    ]
    assert len(show_items) == 1
    assert show_items[0].detected_name == "Documentary Special"
    assert show_items[0].detected_year == 2022
    assert show_items[0].candidates[0].media_id == show.id
