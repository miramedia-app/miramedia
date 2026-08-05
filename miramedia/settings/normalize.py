"""Pure transforms for stored settings overrides (no DB side effects)."""

from __future__ import annotations

import copy
from typing import Any


def _convert_scoring_rule_list(legacy: list) -> list[dict[str, Any]]:
    return [
        {
            "name": r.get("name", ""),
            "keywords": r.get("keywords", []),
            "score_modifier": int(r.get("score_modifier", 0) or 0),
            "enabled": r.get("enabled", True),
        }
        for r in legacy
        if r.get("name")
    ]


def migrate_native_metadata_enabled(data: dict) -> dict:
    """Legacy ``metadata.native.enabled`` → split into tvmaze + cinemeta flags."""
    result = copy.deepcopy(data)
    if "enabled" in result:
        legacy = result.pop("enabled")
        if legacy is not None:
            result.setdefault("tvmaze", {}).setdefault("enabled", legacy)
            result.setdefault("cinemeta", {}).setdefault("enabled", legacy)
    return result


def migrate_requests_section(data: dict) -> dict:
    """Strip removed fields and map legacy master ``enabled`` → ``native.enabled``."""
    result = copy.deepcopy(data)
    result.pop("auto_approve_superuser", None)
    if "enabled" in result:
        legacy_master = result.pop("enabled")
        if legacy_master:
            native = result.setdefault("native", {})
            if isinstance(native, dict):
                native.setdefault("enabled", True)
    return result


def migrate_subtitles_section(data: dict) -> dict:
    """Legacy ``subtitles.enabled = false`` → disable native + bazarr backends."""
    result = copy.deepcopy(data)
    if "enabled" in result:
        legacy_master = result.pop("enabled")
        if legacy_master is False:
            native = result.get("native") or {}
            if isinstance(native, dict):
                native.setdefault("enabled", False)
                result["native"] = native
            bazarr = result.get("bazarr") or {}
            if isinstance(bazarr, dict):
                bazarr.setdefault("enabled", False)
                result["bazarr"] = bazarr
    return result


def migrate_indexer_scoring_rules(data: dict) -> dict:
    """Map legacy scoring-rule lists onto ``quality_options`` / ``codec_options``."""
    result = copy.deepcopy(data)
    if "quality_scoring_rules" in result:
        legacy_quality = result.pop("quality_scoring_rules")
        if "quality_options" not in result:
            result["quality_options"] = _convert_scoring_rule_list(legacy_quality or [])
    if "codec_scoring_rules" in result:
        legacy_codec = result.pop("codec_scoring_rules")
        if "codec_options" not in result:
            result["codec_options"] = _convert_scoring_rule_list(legacy_codec or [])
    return result


def normalize_legacy_overrides(overrides: dict) -> dict:
    """Deep-copy read transform for legacy key migrations (no persistence)."""
    result = copy.deepcopy(overrides or {})

    indexers = result.get("indexers")
    if isinstance(indexers, dict):
        native = indexers.get("native")
        if isinstance(native, dict):
            legacy_cf: dict | None = None
            if "cloudflare_solver" in native:
                legacy_cf = native.pop("cloudflare_solver") or {}
            if "cloudflare_bypass" in native:
                legacy_cf = (legacy_cf or {}) | (native.pop("cloudflare_bypass") or {})
            if legacy_cf is not None:
                legacy_cf.pop("enabled", None)
                existing = result.get("cloudflare", {})
                result["cloudflare"] = {**legacy_cf, **existing}
        if isinstance(indexers, dict):
            migrated_indexers = migrate_indexer_scoring_rules(indexers)
            result["indexers"] = migrated_indexers
            indexers = migrated_indexers
        for key in ("quality_options", "codec_options"):
            opts = indexers.get(key)
            if isinstance(opts, list) and any(
                isinstance(o, dict) and "score_modifier" not in o for o in opts
            ):
                enabled_count = sum(
                    1 for o in opts if isinstance(o, dict) and o.get("enabled", True)
                )
                e_idx = 0
                for o in opts:
                    if not isinstance(o, dict):
                        continue
                    if "score_modifier" not in o:
                        if o.get("enabled", True):
                            o["score_modifier"] = (enabled_count - e_idx) * 100
                            e_idx += 1
                        else:
                            o["score_modifier"] = 0
        promoted_timeout: int | None = None
        for sub in ("prowlarr", "jackett", "native"):
            node = indexers.get(sub, {})
            if isinstance(node, dict) and "timeout_seconds" in node:
                value = node.pop("timeout_seconds")
                if promoted_timeout is None and value is not None:
                    promoted_timeout = value
        if promoted_timeout is not None and "timeout_seconds" not in indexers:
            indexers["timeout_seconds"] = promoted_timeout

    metadata = result.get("metadata")
    if isinstance(metadata, dict):
        meta_native = metadata.get("native")
        if isinstance(meta_native, dict):
            metadata["native"] = migrate_native_metadata_enabled(meta_native)

    torrents = result.get("torrents")
    if isinstance(torrents, dict):
        tor_native = torrents.get("native")
        if isinstance(tor_native, dict) and "download_path" in tor_native:
            del tor_native["download_path"]

    notifications = result.get("notifications")
    if isinstance(notifications, dict) and "enabled" in notifications:
        del notifications["enabled"]

    requests = result.get("requests")
    if isinstance(requests, dict):
        result["requests"] = migrate_requests_section(requests)

    subtitles = result.get("subtitles")
    if isinstance(subtitles, dict):
        result["subtitles"] = migrate_subtitles_section(subtitles)

    return result


def normalize_stored_overrides(overrides: dict | None) -> dict:
    """Normalize legacy keys and strip restart-only fields for effective reads."""
    from miramedia.settings.validation import strip_restart_only_overrides

    return strip_restart_only_overrides(normalize_legacy_overrides(overrides or {}))
