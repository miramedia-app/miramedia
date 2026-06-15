import logging

from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.settings.models import SystemConfigOverride

log = logging.getLogger(__name__)

SINGLETON_ID = 1


class SettingsRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_overrides(self) -> dict:
        row = await self.db.get(SystemConfigOverride, SINGLETON_ID)
        if row is None:
            return {}
        overrides = row.overrides or {}

        # Migrate renamed/removed config keys from older versions
        dirty = False
        indexers = overrides.get("indexers", {})
        native = indexers.get("native", {})
        # Promote indexers.native.cloudflare_{solver,bypass} -> top-level cloudflare.
        legacy_cf: dict | None = None
        if "cloudflare_solver" in native:
            legacy_cf = native.pop("cloudflare_solver") or {}
            dirty = True
        if "cloudflare_bypass" in native:
            legacy_cf = (legacy_cf or {}) | (native.pop("cloudflare_bypass") or {})
            dirty = True
        if legacy_cf is not None:
            legacy_cf.pop("enabled", None)
            existing = overrides.get("cloudflare", {})
            overrides["cloudflare"] = {**legacy_cf, **existing}
            dirty = True
        # quality_scoring_rules -> quality_options (preserve score_modifier)
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
            dirty = True
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
            dirty = True
        # Backfill score_modifier on existing quality_options/codec_options that
        # were saved before this field existed. Default to the old positional
        # weight (enabled_count - idx) * 100 so existing behavior is preserved.
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
                dirty = True
        # metadata.native.enabled -> metadata.native.{tvmaze,cinemeta}.enabled
        metadata = overrides.get("metadata", {})
        meta_native = metadata.get("native", {})
        if "enabled" in meta_native:
            legacy_val = meta_native.pop("enabled")
            meta_native.setdefault("tvmaze", {}).setdefault("enabled", legacy_val)
            meta_native.setdefault("cinemeta", {}).setdefault("enabled", legacy_val)
            dirty = True
        # torrents.native.download_path -> dropped (resume data now lives under misc.torrent_directory)
        torrents = overrides.get("torrents", {})
        tor_native = torrents.get("native", {})
        if "download_path" in tor_native:
            del tor_native["download_path"]
            dirty = True
        # Per-provider indexer timeout -> unified indexers.timeout_seconds
        # First provider with a non-default value wins; others are dropped.
        promoted_timeout: int | None = None
        for sub in ("prowlarr", "jackett", "native"):
            node = indexers.get(sub, {})
            if "timeout_seconds" in node:
                value = node.pop("timeout_seconds")
                if promoted_timeout is None and value is not None:
                    promoted_timeout = value
                dirty = True
        if promoted_timeout is not None and "timeout_seconds" not in indexers:
            indexers["timeout_seconds"] = promoted_timeout
            overrides["indexers"] = indexers
            dirty = True
        # notifications.enabled -> dropped (no master toggle; was briefly added then removed)
        notifications = overrides.get("notifications", {})
        if "enabled" in notifications:
            del notifications["enabled"]
            dirty = True
        # requests.enabled -> requests.native.enabled (master toggle removed; derived now)
        requests = overrides.get("requests", {})
        if "enabled" in requests:
            legacy_master = requests.pop("enabled")
            if legacy_master:
                req_native = requests.setdefault("native", {})
                req_native.setdefault("enabled", True)
            dirty = True
        # subtitles.enabled -> dropped; if it was False, flip both backends off
        subtitles = overrides.get("subtitles", {})
        if "enabled" in subtitles:
            legacy_master = subtitles.pop("enabled")
            if legacy_master is False:
                sub_native = subtitles.setdefault("native", {})
                sub_native.setdefault("enabled", False)
                sub_bazarr = subtitles.setdefault("bazarr", {})
                sub_bazarr.setdefault("enabled", False)
            dirty = True
        if dirty:
            row.overrides = overrides
            await self.db.commit()

        return overrides

    async def save_overrides(self, overrides: dict) -> dict:
        row = await self.db.get(SystemConfigOverride, SINGLETON_ID)
        if row is None:
            row = SystemConfigOverride(id=SINGLETON_ID, overrides=overrides)
            self.db.add(row)
        else:
            row.overrides = overrides
        await self.db.commit()
        await self.db.refresh(row)
        return row.overrides

    async def reset_overrides(self) -> None:
        row = await self.db.get(SystemConfigOverride, SINGLETON_ID)
        if row is not None:
            row.overrides = {}
            await self.db.commit()

    async def clear_override_path(self, path: list[str]) -> dict:
        """Remove a single override at the given dotted path. Returns updated overrides.

        Prunes empty parent dicts left after removal so the diff stays minimal.
        """
        if not path:
            return await self.get_overrides()
        overrides = await self.get_overrides()
        node = overrides
        stack: list[tuple[dict, str]] = []
        for key in path[:-1]:
            if not isinstance(node, dict) or key not in node:
                return overrides
            stack.append((node, key))
            node = node[key]
        if not isinstance(node, dict) or path[-1] not in node:
            return overrides
        del node[path[-1]]
        for parent, key in reversed(stack):
            if isinstance(parent[key], dict) and not parent[key]:
                del parent[key]
        return await self.save_overrides(overrides)
