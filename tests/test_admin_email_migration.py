"""Unit tests for one-shot admin_emails superuser migration."""

from __future__ import annotations

import asyncio
from typing import Self
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import InvalidRequestError, SQLAlchemyError

from miramedia.auth.startup_migrations import (
    AdminEmailPromotionResult,
    normalized_admin_emails,
)


def test_normalized_admin_emails_strips_and_lowercases() -> None:
    assert normalized_admin_emails([" Admin@Example.com ", "", "  "]) == [
        "admin@example.com"
    ]


def test_normalized_admin_emails_empty_list() -> None:
    assert normalized_admin_emails([]) == []
    assert normalized_admin_emails(None) == []


def _session_with_marker(*, has_marker: bool) -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    marker = MagicMock(name="migration_marker") if has_marker else None
    begin_active = False

    async def _get(_model: type, _name: str) -> MagicMock | None:
        assert begin_active  # database access before db.begin()
        return marker

    session.get = AsyncMock(side_effect=_get)

    class _BeginContext:
        async def __aenter__(self) -> None:
            nonlocal begin_active
            begin_active = True

        async def __aexit__(self, *_args: object) -> bool:
            nonlocal begin_active
            begin_active = False
            return False

    session.begin = MagicMock(return_value=_BeginContext())
    return session


class _AutobeginSession:
    """Minimal AsyncSession stand-in that autobegins on first database access."""

    def __init__(self, *, has_marker: bool) -> None:
        self._transaction = False
        self._marker = MagicMock(name="migration_marker") if has_marker else None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> bool:
        if self._transaction:
            self._transaction = False
        return False

    def _autobegin(self) -> None:
        if not self._transaction:
            self._transaction = True

    async def get(self, _model: type, _name: str) -> MagicMock | None:
        self._autobegin()
        return self._marker

    def begin(self) -> _AutobeginBeginContext:
        if self._transaction:
            raise InvalidRequestError
        return _AutobeginBeginContext(self)


class _AutobeginBeginContext:
    def __init__(self, session: _AutobeginSession) -> None:
        self._session = session

    async def __aenter__(self) -> None:
        if self._session._transaction:
            raise InvalidRequestError
        self._session._transaction = True

    async def __aexit__(self, *_args: object) -> bool:
        self._session._transaction = False
        return False


def test_migrate_empty_admin_emails_is_noop() -> None:
    from miramedia.auth.users import migrate_admin_emails_to_superuser_flag

    session = _session_with_marker(has_marker=False)

    with (
        patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg,
        patch("miramedia.database.SessionLocal", return_value=session),
        patch(
            "miramedia.auth.startup_migrations.acquire_admin_emails_promotion_lock",
            new_callable=AsyncMock,
        ) as lock,
        patch(
            "miramedia.auth.startup_migrations.record_admin_emails_promotion_complete",
            new_callable=AsyncMock,
        ) as record,
    ):
        mock_cfg.return_value.auth.admin_emails = []
        asyncio.run(migrate_admin_emails_to_superuser_flag())

    session.begin.assert_called_once()
    lock.assert_awaited_once()
    record.assert_not_awaited()


def test_migrate_skips_promotion_when_marker_exists() -> None:
    from miramedia.auth.users import migrate_admin_emails_to_superuser_flag

    session = _session_with_marker(has_marker=True)

    with (
        patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg,
        patch("miramedia.database.SessionLocal", return_value=session),
        patch(
            "miramedia.auth.startup_migrations.acquire_admin_emails_promotion_lock",
            new_callable=AsyncMock,
        ) as lock,
        patch(
            "miramedia.auth.startup_migrations.log_stale_admin_emails_warning"
        ) as warn,
        patch(
            "miramedia.auth.startup_migrations.promote_users_for_admin_emails",
            new_callable=AsyncMock,
        ) as promote,
        patch(
            "miramedia.auth.startup_migrations.record_admin_emails_promotion_complete",
            new_callable=AsyncMock,
        ) as record,
    ):
        mock_cfg.return_value.auth.admin_emails = ["admin@example.com"]
        asyncio.run(migrate_admin_emails_to_superuser_flag())

    lock.assert_awaited_once()
    promote.assert_not_awaited()
    record.assert_not_awaited()
    warn.assert_called_once()


def test_migrate_promotes_once_and_records_marker() -> None:
    from miramedia.auth.users import migrate_admin_emails_to_superuser_flag

    session = _session_with_marker(has_marker=False)

    with (
        patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg,
        patch("miramedia.database.SessionLocal", return_value=session),
        patch(
            "miramedia.auth.startup_migrations.acquire_admin_emails_promotion_lock",
            new_callable=AsyncMock,
        ) as lock,
        patch(
            "miramedia.auth.startup_migrations.promote_users_for_admin_emails",
            new_callable=AsyncMock,
            return_value=AdminEmailPromotionResult(
                promoted=["admin@example.com"],
                matched_emails=["admin@example.com"],
            ),
        ) as promote,
        patch(
            "miramedia.auth.startup_migrations.record_admin_emails_promotion_complete",
            new_callable=AsyncMock,
        ) as record,
        patch(
            "miramedia.auth.startup_migrations.log_admin_emails_deprecation_warning"
        ) as warn,
    ):
        mock_cfg.return_value.auth.admin_emails = ["admin@example.com"]
        asyncio.run(migrate_admin_emails_to_superuser_flag())

    lock.assert_awaited_once()
    promote.assert_awaited_once()
    record.assert_awaited_once()
    warn.assert_called_once()


def test_migrate_defers_marker_when_no_users_match() -> None:
    from miramedia.auth.users import migrate_admin_emails_to_superuser_flag

    session = _session_with_marker(has_marker=False)

    with (
        patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg,
        patch("miramedia.database.SessionLocal", return_value=session),
        patch(
            "miramedia.auth.startup_migrations.acquire_admin_emails_promotion_lock",
            new_callable=AsyncMock,
        ),
        patch(
            "miramedia.auth.startup_migrations.promote_users_for_admin_emails",
            new_callable=AsyncMock,
            return_value=AdminEmailPromotionResult(promoted=[], matched_emails=[]),
        ),
        patch(
            "miramedia.auth.startup_migrations.record_admin_emails_promotion_complete",
            new_callable=AsyncMock,
        ) as record,
        patch(
            "miramedia.auth.startup_migrations.log_admin_emails_deprecation_warning"
        ) as warn,
        patch("miramedia.auth.users.log") as user_log,
    ):
        mock_cfg.return_value.auth.admin_emails = ["admin@example.com"]
        asyncio.run(migrate_admin_emails_to_superuser_flag())

    record.assert_not_awaited()
    warn.assert_not_called()
    user_log.info.assert_called_once_with(
        "No registered users match auth.admin_emails; one-shot promotion "
        "is deferred until a matching user registers."
    )


def test_migrate_records_marker_when_matched_user_already_superuser() -> None:
    from miramedia.auth.users import migrate_admin_emails_to_superuser_flag

    session = _session_with_marker(has_marker=False)

    with (
        patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg,
        patch("miramedia.database.SessionLocal", return_value=session),
        patch(
            "miramedia.auth.startup_migrations.acquire_admin_emails_promotion_lock",
            new_callable=AsyncMock,
        ),
        patch(
            "miramedia.auth.startup_migrations.promote_users_for_admin_emails",
            new_callable=AsyncMock,
            return_value=AdminEmailPromotionResult(
                promoted=[],
                matched_emails=["admin@example.com"],
            ),
        ),
        patch(
            "miramedia.auth.startup_migrations.record_admin_emails_promotion_complete",
            new_callable=AsyncMock,
        ) as record,
        patch(
            "miramedia.auth.startup_migrations.log_admin_emails_deprecation_warning"
        ) as warn,
    ):
        mock_cfg.return_value.auth.admin_emails = ["admin@example.com"]
        asyncio.run(migrate_admin_emails_to_superuser_flag())

    record.assert_awaited_once()
    warn.assert_called_once()


def test_migrate_does_not_raise_when_session_autobegins_on_query() -> None:
    """Regression: completeness checks before db.begin() crash on autobegin."""
    from miramedia.auth.users import migrate_admin_emails_to_superuser_flag

    session = _AutobeginSession(has_marker=False)

    with (
        patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg,
        patch("miramedia.database.SessionLocal", return_value=session),
        patch(
            "miramedia.auth.startup_migrations.acquire_admin_emails_promotion_lock",
            new_callable=AsyncMock,
        ),
        patch(
            "miramedia.auth.startup_migrations.promote_users_for_admin_emails",
            new_callable=AsyncMock,
            return_value=AdminEmailPromotionResult(
                promoted=["admin@example.com"],
                matched_emails=["admin@example.com"],
            ),
        ),
        patch(
            "miramedia.auth.startup_migrations.record_admin_emails_promotion_complete",
            new_callable=AsyncMock,
        ),
        patch("miramedia.auth.startup_migrations.log_admin_emails_deprecation_warning"),
    ):
        mock_cfg.return_value.auth.admin_emails = ["admin@example.com"]
        asyncio.run(migrate_admin_emails_to_superuser_flag())


def test_migrate_failure_does_not_record_marker() -> None:
    from miramedia.auth.users import migrate_admin_emails_to_superuser_flag

    session = _session_with_marker(has_marker=False)

    with (
        patch("miramedia.auth.users.MiraMediaConfig") as mock_cfg,
        patch("miramedia.database.SessionLocal", return_value=session),
        patch(
            "miramedia.auth.startup_migrations.acquire_admin_emails_promotion_lock",
            new_callable=AsyncMock,
        ),
        patch(
            "miramedia.auth.startup_migrations.promote_users_for_admin_emails",
            new_callable=AsyncMock,
            side_effect=SQLAlchemyError("promote failed"),
        ),
        patch(
            "miramedia.auth.startup_migrations.record_admin_emails_promotion_complete",
            new_callable=AsyncMock,
        ) as record,
    ):
        mock_cfg.return_value.auth.admin_emails = ["admin@example.com"]
        with pytest.raises(SQLAlchemyError, match="promote failed"):
            asyncio.run(migrate_admin_emails_to_superuser_flag())

    record.assert_not_awaited()


def test_startup_catches_migration_failure() -> None:
    from miramedia.startup import start_persistence

    async def run() -> None:
        session = _mock_db_session()
        with (
            patch("miramedia.database.init_engine"),
            patch("miramedia.logging.attach_db_handler"),
            patch("miramedia.database.SessionLocalBackground", return_value=session),
            patch("miramedia.database.get_engine") as mock_engine,
            patch(
                "miramedia.indexers.seed.seed_preloaded_sites", new_callable=AsyncMock
            ),
            patch(
                "miramedia.torrents.repository.TorrentRepository"
            ) as mock_torrent_repo,
            patch(
                "miramedia.settings.repository.SettingsRepository"
            ) as mock_settings_repo,
            patch(
                "miramedia.shows.cleanup.cleanup_stale_show_preferences",
                new_callable=AsyncMock,
            ),
            patch(
                "miramedia.movies.cleanup.cleanup_stale_movie_preferences",
                new_callable=AsyncMock,
            ),
            patch(
                "miramedia.auth.runtime.initialize_auth_runtime", new_callable=AsyncMock
            ),
            patch(
                "miramedia.auth.users.migrate_admin_emails_to_superuser_flag",
                new_callable=AsyncMock,
                side_effect=RuntimeError("migration failed"),
            ),
            patch(
                "miramedia.auth.users.create_default_admin_user", new_callable=AsyncMock
            ),
            patch("miramedia.logging.apply_development_log_level"),
            patch.dict("os.environ", {"MIRAMEDIA_EVENT_BRIDGE_DISABLED": "true"}),
        ):
            eng = MagicMock()
            conn = MagicMock()
            conn.execute = AsyncMock()
            conn.__aenter__ = AsyncMock(return_value=conn)
            conn.__aexit__ = AsyncMock(return_value=False)
            eng.connect = MagicMock(return_value=conn)
            mock_engine.return_value = eng
            mock_torrent_repo.return_value.delete_orphaned_torrents = AsyncMock()
            mock_settings_repo.return_value.get_overrides_with_revision = AsyncMock(
                return_value=({}, 0)
            )
            await start_persistence()

    asyncio.run(run())


def _mock_db_session() -> MagicMock:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    return session
