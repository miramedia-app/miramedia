"""OAuth account ORM metadata invariants."""

from __future__ import annotations

from sqlalchemy import UniqueConstraint

from miramedia.auth.db import OAuthAccount

_UNIQUE_INDEX_NAME = "uq_oauth_account_oauth_name_account_id"


def test_oauth_account_declares_named_provider_account_unique_index() -> None:
    table = OAuthAccount.__table__
    unique_indexes = [ix for ix in table.indexes if ix.unique]
    composite_unique = [
        ix
        for ix in unique_indexes
        if [column.name for column in ix.columns] == ["oauth_name", "account_id"]
    ]

    assert len(composite_unique) == 1
    index = composite_unique[0]
    assert index.name == _UNIQUE_INDEX_NAME
    assert [column.name for column in index.columns] == ["oauth_name", "account_id"]

    assert not any(
        isinstance(constraint, UniqueConstraint)
        and [column.name for column in constraint.columns]
        == ["oauth_name", "account_id"]
        for constraint in table.constraints
    )
