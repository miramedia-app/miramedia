"""Fake MiraMediaConfig for filesystem and scheduler orchestration tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def fake_config(
    *,
    show_directory: Path,
    movie_directory: Path | None = None,
    completed_directory: Path | None = None,
) -> SimpleNamespace:
    """Minimal config object with subtitles disabled and sane naming defaults."""
    completed = completed_directory or (show_directory.parent / "completed")
    naming = SimpleNamespace(
        show_folder_format="{title} ({year}) {provider_tag}",
        season_folder_format="Season {season_number}",
        movie_folder_format="{title} ({year}) {provider_tag}",
        movie_file_format="{title} ({year}){suffix}",
        episode_file_format="{show_title} S{season_number:02d}E{episode_number:02d}{suffix}",
    )
    misc = SimpleNamespace(
        show_directory=show_directory,
        movie_directory=movie_directory or show_directory.parent / "movies",
        torrent_directory=completed,
        show_libraries=[],
        movie_libraries=[],
        continuous_download=True,
        download_specials=False,
        cleanup_after_import=False,
        naming=naming,
        integrity_check_enabled=False,
        integrity_check_interval_hours=24,
    )
    misc.effective_completed_path = completed  # mirrors @property on real MiscConfig

    subtitles = SimpleNamespace(
        enabled=False,
        native=SimpleNamespace(enabled=False),
    )
    return SimpleNamespace(misc=misc, subtitles=subtitles)


def fake_scheduler_config(
    *,
    requests_enabled: bool = True,
    integrity_check_enabled: bool = True,
) -> SimpleNamespace:
    """Config stub for scheduler task bodies."""
    return SimpleNamespace(
        requests=SimpleNamespace(enabled=requests_enabled),
        misc=SimpleNamespace(
            integrity_check_enabled=integrity_check_enabled,
            integrity_check_interval_hours=24,
            show_libraries=[],
            movie_libraries=[],
            show_directory=Path("fake-shows"),
            movie_directory=Path("fake-movies"),
        ),
    )
