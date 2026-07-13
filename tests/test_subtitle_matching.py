"""Tests for subtitle language selection and cache/stem matching helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from miramedia.subtitles.schemas import SubtitleFile
from miramedia.subtitles.service import SubtitleService


def _service() -> SubtitleService:
    return SubtitleService(subtitle_repository=MagicMock())


def test_get_desired_languages_reads_config(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(
        svc,
        "_get_config",
        lambda: SimpleNamespace(
            subtitles=SimpleNamespace(desired_languages=["en", "es", "de"])
        ),
    )
    assert svc._get_desired_languages() == ["en", "es", "de"]


def test_get_desired_languages_single(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(
        svc,
        "_get_config",
        lambda: SimpleNamespace(subtitles=SimpleNamespace(desired_languages=["fr"])),
    )
    assert svc._get_desired_languages() == ["fr"]


def test_get_desired_languages_empty_list(monkeypatch) -> None:
    svc = _service()
    monkeypatch.setattr(
        svc,
        "_get_config",
        lambda: SimpleNamespace(subtitles=SimpleNamespace(desired_languages=[])),
    )
    assert svc._get_desired_languages() == []


def test_existing_subtitle_languages_for_stems(tmp_path: Path) -> None:
    (tmp_path / "Movie.en.srt").write_text("1")
    (tmp_path / "Movie.eng.srt").write_text("1")
    (tmp_path / "Movie.es.vtt").write_text("1")
    (tmp_path / "Other.en.srt").write_text("1")
    (tmp_path / "Movie.txt").write_text("ignore")
    (tmp_path / "readme.md").write_text("ignore")

    svc = _service()
    found = svc.get_existing_subtitle_languages_for_stems(tmp_path, ["Movie"])

    langs = sorted(s.language for s in found)
    names = sorted(s.file_name for s in found)
    assert langs == ["en", "eng", "es"]
    assert names == ["Movie.en.srt", "Movie.eng.srt", "Movie.es.vtt"]


def test_existing_subtitle_languages_no_matches(tmp_path: Path) -> None:
    (tmp_path / "Unrelated.en.srt").write_text("1")
    svc = _service()
    assert svc.get_existing_subtitle_languages_for_stems(tmp_path, ["Movie"]) == []


def test_existing_subtitle_languages_missing_directory(tmp_path: Path) -> None:
    svc = _service()
    missing = tmp_path / "nope"
    assert svc.get_existing_subtitle_languages_for_stems(missing, ["Movie"]) == []


def test_match_subtitles_from_cache_matches_stems() -> None:
    sub_files = [
        ("Show S01E01.en.srt", ".srt"),
        ("Show S01E01.eng.srt", ".srt"),
        ("Show S01E02.en.srt", ".srt"),
        ("trailer.en.srt", ".srt"),
    ]
    matched = SubtitleService._match_subtitles_from_cache(
        ["Show S01E01"],
        sub_files,
    )
    assert matched == [
        SubtitleFile(language="en", file_name="Show S01E01.en.srt"),
        SubtitleFile(language="eng", file_name="Show S01E01.eng.srt"),
    ]


def test_match_subtitles_from_cache_no_match() -> None:
    matched = SubtitleService._match_subtitles_from_cache(
        ["Movie Title"],
        [("Other.en.srt", ".srt")],
    )
    assert matched == []


def test_match_subtitles_from_cache_dedupes_across_stems() -> None:
    sub_files = [("Movie - 1080P.en.srt", ".srt")]
    matched = SubtitleService._match_subtitles_from_cache(
        ["Movie", "Movie - 1080P"],
        sub_files,
    )
    assert matched == [
        SubtitleFile(language="en", file_name="Movie - 1080P.en.srt"),
    ]
