"""Integration guard: ORM metadata matches Alembic head after upgrade."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from alembic import command
from miramedia.auth.api_tokens import UserApiToken  # noqa: F401
from miramedia.auth.db import OAuthAccount, User  # noqa: F401
from miramedia.auth.startup_migrations import AuthStartupMigration  # noqa: F401
from miramedia.database import Base
from miramedia.feeds.models import FeedItem, FeedSource  # noqa: F401
from miramedia.imports.models import (  # noqa: F401
    IgnoredImportPath,
    ImportBatch,
    ScanResultCache,
    ScanRun,
)
from miramedia.indexers.models import IndexerQueryResult, IndexerSite  # noqa: F401
from miramedia.logs.models import ActivityLog  # noqa: F401
from miramedia.media_inventory import MediaFileInventory  # noqa: F401
from miramedia.movies.models import Movie, MovieFile  # noqa: F401
from miramedia.notifications.models import Notification  # noqa: F401
from miramedia.playback.models import MediaWatchState, PlaybackProgress  # noqa: F401
from miramedia.requests.models import MediaRequest  # noqa: F401
from miramedia.settings.models import SystemConfigOverride  # noqa: F401
from miramedia.shows.models import Episode, EpisodeFile, Season, Show  # noqa: F401
from miramedia.subtitles.arr_ids import ArrIdMap  # noqa: F401
from miramedia.subtitles.models import SubtitleRecord  # noqa: F401
from miramedia.torrents.models import (  # noqa: F401
    ManualParseToken,
    Torrent,
    TorrentBlock,
    TorrentHistory,
)
from miramedia.viewing_sync.models import (  # noqa: F401
    ViewingSyncCursor,
    ViewingSyncProposal,
    ViewingSyncQuarantine,
    ViewingSyncRun,
)
from miramedia.watchlists.models import Watchlist, WatchlistItem  # noqa: F401
from tests.integration._db_url import alembic_sync_url

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(sync_url: str) -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url.replace("%", "%%"))
    return cfg


def _include_name(
    name: str | None,
    type_: str | None,
    _parent_names: dict[str, str | None],
) -> bool:
    if type_ == "table":
        # Read metadata live, not a frozen snapshot: models registered on
        # Base after this module imported (transitively, via other imports)
        # would otherwise be filtered out of the reflected side only,
        # producing phantom "add_table" drift.
        return name in Base.metadata.tables
    return True


def _include_object(
    _schema_object: object | None,
    name: str | None,
    type_: str | None,
    _reflected: bool,
    _compare_to: object | None,
) -> bool:
    if type_ == "table" and name == "apscheduler_jobs":
        return False
    return True


def _upgrade_head(sync_url: str) -> None:
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = sync_url
    try:
        command.upgrade(_alembic_config(sync_url), "head")
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


_INDEX_DIFF_OPS = frozenset({"add_index", "remove_index"})


def _flatten_diff(diff: list[object]) -> list[tuple[object, ...]]:
    """Normalize compare_metadata output (may contain nested lists)."""
    flat: list[tuple[object, ...]] = []
    for item in diff:
        if isinstance(item, list):
            flat.extend(nested for nested in item if isinstance(nested, tuple))
        elif isinstance(item, tuple):
            flat.append(item)
    return flat


def _meaningful_schema_diff(diff: list[object]) -> list[tuple[object, ...]]:
    """Drop migration-only index artifacts Alembic cannot mirror in ORM metadata."""
    return [item for item in _flatten_diff(diff) if item[0] not in _INDEX_DIFF_OPS]


def _schema_metadata_diff(sync_url: str) -> list[object]:
    engine = create_engine(sync_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            ctx = MigrationContext.configure(
                connection=connection,
                opts={
                    "target_metadata": Base.metadata,
                    "include_name": _include_name,
                    "include_object": _include_object,
                    # TEXT vs VARCHAR and similar PostgreSQL aliases are not drift.
                    "compare_type": False,
                },
            )
            return compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()


def test_orm_metadata_matches_alembic_head(integration_db_url: str) -> None:
    sync_url = alembic_sync_url(integration_db_url)
    _upgrade_head(sync_url)
    diff = _meaningful_schema_diff(_schema_metadata_diff(sync_url))
    assert diff == [], f"ORM/Alembic schema drift detected:\n{diff!r}"
