"""Corpus tests for import title normalization and candidate ranking."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from miramedia.imports.matching import (
    _normalize_title_for_matching,
    find_candidate_media_matches,
    score_title_match_with_breakdown,
)
from miramedia.imports.schemas import MatchBreakdown

MATCH_BREAKDOWN_KEYS = frozenset(MatchBreakdown.model_fields)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Show.Name.S01E02.1080p.WEB-DL.x264-GRP", "Show Name GRP"),
        ("Breaking Bad", "Breaking Bad"),
        ("the.office", "the office"),
        ("Dune.2021.2160p.HDR.REMUX", "Dune 2021"),
        ("Breaking.Bad.2008.1080p", "Breaking Bad 2008"),
        ("Show.S02", "Show"),
        ("Title [Group]", "Title"),
        ("Title (UK)", "Title"),
        ("foo-bar_baz.test", "foo bar baz test"),
        ("Movie.4K.UHD.HEVC.10bit.REMUX", "Movie"),
        ("[RARBG] Show Name", "Show Name"),
        ("Show Name (2020)", "Show Name"),
    ],
)
def test_normalize_title_for_matching(raw: str, expected: str) -> None:
    assert _normalize_title_for_matching(raw) == expected


@pytest.fixture
def matching_library() -> tuple[list[SimpleNamespace], list[SimpleNamespace]]:
    shows = [
        SimpleNamespace(id=uuid.uuid4(), name="Breaking Bad", year=2008),
        SimpleNamespace(id=uuid.uuid4(), name="The Office", year=2005),
        SimpleNamespace(id=uuid.uuid4(), name="The Office", year=2001),
    ]
    movies = [
        SimpleNamespace(id=uuid.uuid4(), name="Dune", year=2021),
        SimpleNamespace(id=uuid.uuid4(), name="Dune", year=1984),
    ]
    return shows, movies


def test_exact_title_ranks_first_with_high_confidence(
    matching_library: tuple[list[SimpleNamespace], list[SimpleNamespace]],
) -> None:
    shows, movies = matching_library
    results = find_candidate_media_matches("Breaking Bad", shows, movies)
    assert len(results) >= 1
    top = results[0]
    assert top["media_name"] == "Breaking Bad"
    assert top["media_year"] == 2008
    assert top["confidence"] >= 0.9


def test_punctuation_variant_ranks_offices_above_unrelated(
    matching_library: tuple[list[SimpleNamespace], list[SimpleNamespace]],
) -> None:
    shows, movies = matching_library
    results = find_candidate_media_matches("the.office", shows, movies)
    names = {(r["media_name"], r["media_year"]) for r in results}
    assert ("The Office", 2005) in names
    assert ("The Office", 2001) in names
    assert not any(r["media_name"] == "Breaking Bad" for r in results)
    assert all(r["confidence"] > 0.3 for r in results)


def test_year_disambiguated_office_prefers_matching_year(
    matching_library: tuple[list[SimpleNamespace], list[SimpleNamespace]],
) -> None:
    shows, movies = matching_library
    results = find_candidate_media_matches("The Office 2001", shows, movies)
    assert len(results) >= 1
    assert results[0]["media_name"] == "The Office"
    assert results[0]["media_year"] == 2001
    assert not any(r["media_year"] == 2005 for r in results)


def test_release_name_query_prefers_matching_dune_year(
    matching_library: tuple[list[SimpleNamespace], list[SimpleNamespace]],
) -> None:
    shows, movies = matching_library
    results = find_candidate_media_matches("Dune.2021.2160p.HDR.REMUX", shows, movies)
    assert len(results) >= 1
    assert results[0]["media_name"] == "Dune"
    assert results[0]["media_year"] == 2021
    assert not any(r["media_year"] == 1984 for r in results)


def test_garbage_query_returns_no_matches(
    matching_library: tuple[list[SimpleNamespace], list[SimpleNamespace]],
) -> None:
    shows, movies = matching_library
    results = find_candidate_media_matches("asdkjhqwe", shows, movies)
    assert results == []


@pytest.mark.parametrize("max_results", [1, 2])
def test_max_results_truncates_ranked_candidates(
    matching_library: tuple[list[SimpleNamespace], list[SimpleNamespace]],
    max_results: int,
) -> None:
    shows, movies = matching_library
    results = find_candidate_media_matches(
        "office", shows, movies, max_results=max_results
    )
    assert len(results) == max_results
    assert all(r["media_name"] == "The Office" for r in results)


def test_find_candidate_breakdown_keys_match_schema(
    matching_library: tuple[list[SimpleNamespace], list[SimpleNamespace]],
) -> None:
    shows, movies = matching_library
    results = find_candidate_media_matches("Breaking Bad", shows, movies)
    assert results
    assert frozenset(results[0]["breakdown"]) == MATCH_BREAKDOWN_KEYS


def test_score_title_match_breakdown_keys_match_schema() -> None:
    confidence, breakdown = score_title_match_with_breakdown(
        "The Office", 2001, "The Office", 2001
    )
    assert confidence > 0.0
    assert frozenset(breakdown) == MATCH_BREAKDOWN_KEYS


@pytest.mark.parametrize(
    ("query", "query_year", "candidate_name", "candidate_year", "min_confidence"),
    [
        ("Breaking Bad", None, "Breaking Bad", 2008, 0.9),
        ("the office", None, "The Office", 2005, 0.9),
        ("The Office", 2001, "The Office", 2001, 0.9),
        ("Dune", 2021, "Dune", 2021, 0.9),
    ],
)
def test_score_title_match_high_confidence_cases(
    query: str,
    query_year: int | None,
    candidate_name: str,
    candidate_year: int | None,
    min_confidence: float,
) -> None:
    confidence, _breakdown = score_title_match_with_breakdown(
        query, query_year, candidate_name, candidate_year
    )
    assert confidence >= min_confidence


@pytest.mark.parametrize(
    ("query", "query_year", "matching_year", "mismatch_year"),
    [
        ("The Office", 2001, 2001, 2005),
        ("Dune", 2021, 2021, 1984),
    ],
)
def test_score_title_match_year_boost_prefers_matching_year(
    query: str,
    query_year: int,
    matching_year: int,
    mismatch_year: int,
) -> None:
    match_confidence, _ = score_title_match_with_breakdown(
        query, query_year, query, matching_year
    )
    mismatch_confidence, _ = score_title_match_with_breakdown(
        query, query_year, query, mismatch_year
    )
    assert match_confidence > mismatch_confidence
