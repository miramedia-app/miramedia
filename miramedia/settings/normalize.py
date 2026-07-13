"""Pure transforms for stored settings overrides (no DB side effects)."""

from __future__ import annotations

import copy

from miramedia.settings.validation import strip_restart_only_overrides


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
        if "quality_scoring_rules" in indexers:
            legacy = indexers.pop("quality_scoring_rules") or []
            indexers["quality_options"] = [
                {
                    "name": r.get("name", ""),
                    "keywords": r.get("keywords", []),
                    "score_modifier": int(r.get("score_modifier", 0) or 0),
                    "enabled": r.get("enabled", True),
                }
                for r in legacy
                if r.get("name")
            ]
        if "codec_scoring_rules" in indexers:
            legacy = indexers.pop("codec_scoring_rules") or []
            indexers["codec_options"] = [
                {
                    "name": r.get("name", ""),
                    "keywords": r.get("keywords", []),
                    "score_modifier": int(r.get("score_modifier", 0) or 0),
                    "enabled": r.get("enabled", True),
                }
                for r in legacy
                if r.get("name")
            ]
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
        if isinstance(meta_native, dict) and "enabled" in meta_native:
            legacy_val = meta_native.pop("enabled")
            meta_native.setdefault("tvmaze", {}).setdefault("enabled", legacy_val)
            meta_native.setdefault("cinemeta", {}).setdefault("enabled", legacy_val)

    torrents = result.get("torrents")
    if isinstance(torrents, dict):
        tor_native = torrents.get("native")
        if isinstance(tor_native, dict) and "download_path" in tor_native:
            del tor_native["download_path"]

    notifications = result.get("notifications")
    if isinstance(notifications, dict) and "enabled" in notifications:
        del notifications["enabled"]

    requests = result.get("requests")
    if isinstance(requests, dict) and "enabled" in requests:
        legacy_master = requests.pop("enabled")
        if legacy_master:
            req_native = requests.setdefault("native", {})
            if isinstance(req_native, dict):
                req_native.setdefault("enabled", True)

    subtitles = result.get("subtitles")
    if isinstance(subtitles, dict) and "enabled" in subtitles:
        legacy_master = subtitles.pop("enabled")
        if legacy_master is False:
            sub_native = subtitles.setdefault("native", {})
            if isinstance(sub_native, dict):
                sub_native.setdefault("enabled", False)
            sub_bazarr = subtitles.setdefault("bazarr", {})
            if isinstance(sub_bazarr, dict):
                sub_bazarr.setdefault("enabled", False)

    return result


def normalize_stored_overrides(overrides: dict | None) -> dict:
    """Normalize legacy keys and strip restart-only fields for effective reads."""
    return strip_restart_only_overrides(normalize_legacy_overrides(overrides or {}))
