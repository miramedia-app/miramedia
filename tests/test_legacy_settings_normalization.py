"""Table-driven equivalence tests for legacy settings key migrations."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from miramedia.indexers.config import IndexerConfig
from miramedia.requests.config import RequestsConfig
from miramedia.settings.normalize import (
    migrate_indexer_scoring_rules,
    migrate_native_metadata_enabled,
    migrate_requests_section,
    migrate_subtitles_section,
    normalize_legacy_overrides,
)
from miramedia.settings.schemas import (
    NativeMetadataSettingsSchema,
    RequestsSettingsSchema,
    SubtitleSettingsSchema,
)

MetadataNativeInput = dict[str, Any]
RequestsInput = dict[str, Any]
SubtitlesInput = dict[str, Any]
IndexersInput = dict[str, Any]


def _stored_metadata_native(raw: MetadataNativeInput) -> dict[str, Any]:
    return normalize_legacy_overrides({"metadata": {"native": raw}})["metadata"][
        "native"
    ]


def _api_metadata_native(raw: MetadataNativeInput) -> dict[str, Any]:
    return NativeMetadataSettingsSchema.model_validate(raw).model_dump(mode="json")


def _stored_requests(raw: RequestsInput) -> dict[str, Any]:
    return normalize_legacy_overrides({"requests": raw})["requests"]


def _api_requests(raw: RequestsInput) -> dict[str, Any]:
    return RequestsSettingsSchema.model_validate(raw).model_dump(mode="json")


def _runtime_requests(raw: RequestsInput) -> dict[str, Any]:
    return RequestsConfig.model_validate(raw).model_dump(mode="json")


def _stored_subtitles(raw: SubtitlesInput) -> dict[str, Any]:
    return normalize_legacy_overrides({"subtitles": raw})["subtitles"]


def _api_subtitles(raw: SubtitlesInput) -> dict[str, Any]:
    return SubtitleSettingsSchema.model_validate(raw).model_dump(mode="json")


def _stored_indexers(raw: IndexersInput) -> dict[str, Any]:
    return normalize_legacy_overrides({"indexers": raw})["indexers"]


def _runtime_indexers(raw: IndexersInput) -> dict[str, Any]:
    return IndexerConfig.model_validate(raw).model_dump(mode="json")


METADATA_NATIVE_CASES: list[tuple[MetadataNativeInput, dict[str, Any]]] = [
    (
        {"enabled": True},
        {"tvmaze": {"enabled": True}, "cinemeta": {"enabled": True}},
    ),
    (
        {"enabled": False},
        {"tvmaze": {"enabled": False}, "cinemeta": {"enabled": False}},
    ),
    (
        {"enabled": True, "tvmaze": {"enabled": False}},
        {"tvmaze": {"enabled": False}, "cinemeta": {"enabled": True}},
    ),
    (
        {"tvmaze": {"enabled": True}, "cinemeta": {"enabled": False}},
        {"tvmaze": {"enabled": True}, "cinemeta": {"enabled": False}},
    ),
]

REQUESTS_CASES: list[tuple[RequestsInput, dict[str, Any]]] = [
    (
        {"enabled": True},
        {"native": {"enabled": True}},
    ),
    (
        {"enabled": False},
        {},
    ),
    (
        {"enabled": True, "native": {"enabled": False}},
        {"native": {"enabled": False}},
    ),
    (
        {"auto_approve_superuser": True, "enabled": True},
        {"native": {"enabled": True}},
    ),
    (
        {"auto_approve_superuser": True},
        {},
    ),
]

SUBTITLES_CASES: list[tuple[SubtitlesInput, dict[str, Any]]] = [
    (
        {"enabled": False},
        {"native": {"enabled": False}, "bazarr": {"enabled": False}},
    ),
    (
        {"enabled": True},
        {},
    ),
    (
        {"enabled": False, "native": {"enabled": True}},
        {"native": {"enabled": True}, "bazarr": {"enabled": False}},
    ),
]

INDEXER_SCORING_CASES: list[tuple[IndexersInput, dict[str, Any]]] = [
    (
        {
            "quality_scoring_rules": [
                {
                    "name": "1080p",
                    "keywords": ["1080"],
                    "score_modifier": 100,
                    "enabled": True,
                }
            ]
        },
        {
            "quality_options": [
                {
                    "name": "1080p",
                    "keywords": ["1080"],
                    "score_modifier": 100,
                    "enabled": True,
                }
            ]
        },
    ),
    (
        {
            "quality_scoring_rules": [{"name": "1080p", "keywords": ["1080"]}],
            "quality_options": [
                {
                    "name": "4K",
                    "keywords": ["2160"],
                    "score_modifier": 400,
                    "enabled": True,
                }
            ],
        },
        {
            "quality_options": [
                {
                    "name": "4K",
                    "keywords": ["2160"],
                    "score_modifier": 400,
                    "enabled": True,
                }
            ],
        },
    ),
    (
        {
            "codec_scoring_rules": [
                {
                    "name": "H.264",
                    "keywords": ["x264"],
                    "score_modifier": 50,
                }
            ]
        },
        {
            "codec_options": [
                {
                    "name": "H.264",
                    "keywords": ["x264"],
                    "score_modifier": 50,
                    "enabled": True,
                }
            ],
        },
    ),
]


def _assert_subset(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        assert actual.get(key) == value


def _strip_none_values(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _migration_slice_requests(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "native" in data:
        native = data["native"]
        if isinstance(native, dict):
            out["native"] = {"enabled": native.get("enabled")}
        else:
            out["native"] = native
    return out


def _migration_slice_subtitles(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "native" in data:
        native = data["native"]
        if isinstance(native, dict):
            out["native"] = {"enabled": native.get("enabled")}
        else:
            out["native"] = native
    if "bazarr" in data:
        bazarr = data["bazarr"]
        if isinstance(bazarr, dict):
            out["bazarr"] = {"enabled": bazarr.get("enabled")}
        else:
            out["bazarr"] = bazarr
    return out


@pytest.mark.parametrize(("raw", "expected"), METADATA_NATIVE_CASES)
def test_metadata_native_enabled_equivalence(
    raw: MetadataNativeInput,
    expected: dict[str, Any],
) -> None:
    stored = _stored_metadata_native(raw)
    api = _api_metadata_native(raw)
    helper = migrate_native_metadata_enabled(raw)
    _assert_subset(stored, expected)
    _assert_subset(api, expected)
    _assert_subset(helper, expected)
    assert stored == api == helper


@pytest.mark.parametrize(("raw", "expected"), REQUESTS_CASES)
def test_requests_enabled_equivalence(
    raw: RequestsInput,
    expected: dict[str, Any],
) -> None:
    stored = _migration_slice_requests(_stored_requests(raw))
    api = _strip_none_values(_migration_slice_requests(_api_requests(raw)))
    runtime = _strip_none_values(_migration_slice_requests(_runtime_requests(raw)))
    helper = _migration_slice_requests(migrate_requests_section(raw))
    _assert_subset(stored, expected)
    _assert_subset(api, expected)
    _assert_subset(runtime, expected)
    _assert_subset(helper, expected)
    assert stored == helper
    if expected or raw.get("enabled"):
        assert stored == api == runtime
    assert "auto_approve_superuser" not in migrate_requests_section(
        {"auto_approve_superuser": True}
    )


@pytest.mark.parametrize(("raw", "expected"), SUBTITLES_CASES)
def test_subtitles_enabled_equivalence(
    raw: SubtitlesInput,
    expected: dict[str, Any],
) -> None:
    stored = _migration_slice_subtitles(_stored_subtitles(raw))
    api = _strip_none_values(_migration_slice_subtitles(_api_subtitles(raw)))
    helper = _migration_slice_subtitles(migrate_subtitles_section(raw))
    _assert_subset(stored, expected)
    _assert_subset(api, expected)
    _assert_subset(helper, expected)
    assert stored == helper
    assert stored == api


@pytest.mark.parametrize(("raw", "expected"), INDEXER_SCORING_CASES)
def test_indexer_scoring_rules_equivalence(
    raw: IndexersInput,
    expected: dict[str, Any],
) -> None:
    stored = _stored_indexers(raw)
    runtime = _runtime_indexers(raw)
    helper = migrate_indexer_scoring_rules(raw)
    for key, value in expected.items():
        assert stored.get(key) == value
        assert runtime.get(key) == value
        assert helper.get(key) == value
    assert "quality_scoring_rules" not in stored
    assert "codec_scoring_rules" not in stored
    assert stored == helper


@pytest.mark.parametrize(
    "helper",
    [
        migrate_native_metadata_enabled,
        migrate_requests_section,
        migrate_subtitles_section,
        migrate_indexer_scoring_rules,
    ],
)
def test_helpers_do_not_mutate_input(
    helper: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    source = {
        "enabled": True,
        "quality_scoring_rules": [{"name": "1080p", "keywords": ["1080"]}],
        "native": {"enabled": False},
        "auto_approve_superuser": True,
    }
    original = copy.deepcopy(source)
    helper(source)
    assert source == original


@pytest.mark.parametrize(
    "helper",
    [
        migrate_native_metadata_enabled,
        migrate_requests_section,
        migrate_subtitles_section,
        migrate_indexer_scoring_rules,
    ],
)
def test_helpers_are_idempotent(
    helper: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    source = {
        "enabled": True,
        "quality_scoring_rules": [
            {"name": "1080p", "keywords": ["1080"], "score_modifier": 100}
        ],
        "codec_scoring_rules": [
            {"name": "H.264", "keywords": ["x264"], "score_modifier": 50}
        ],
        "native": {"enabled": False},
        "bazarr": {"enabled": True},
        "auto_approve_superuser": True,
    }
    once = helper(source)
    twice = helper(once)
    assert once == twice


def test_metadata_native_enabled_none_is_ignored() -> None:
    raw: MetadataNativeInput = {"enabled": None}
    result = migrate_native_metadata_enabled(raw)
    assert "enabled" not in result
    assert result == {}


def test_indexer_empty_scoring_rules_list_clears_legacy_key() -> None:
    raw: IndexersInput = {"quality_scoring_rules": []}
    result = migrate_indexer_scoring_rules(raw)
    assert "quality_scoring_rules" not in result
    assert result["quality_options"] == []


def test_continue_watching_migrates_from_watchlists() -> None:
    raw = {
        "watchlists": {
            "auto_remove_watched": True,
            "continue_watching": False,
            "native": {"enabled": False, "watch_next": False},
        }
    }
    result = normalize_legacy_overrides(raw)
    assert result["playback"] == {"continue_watching": False}
    assert result["watchlists"] == {
        "auto_remove_watched": True,
        "native": {"enabled": False, "watch_next": False},
    }


def test_playback_migration_prefers_new_section_and_is_idempotent() -> None:
    raw = {
        "watchlists": {"continue_watching": False},
        "playback": {"continue_watching": True},
    }
    once = normalize_legacy_overrides(raw)
    assert once["playback"] == {"continue_watching": True}
    assert "watchlists" not in once
    assert normalize_legacy_overrides(once) == once
