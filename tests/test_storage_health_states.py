"""Precedence for displayed storage-health states (evidence 387 §5)."""

from __future__ import annotations

import pytest

from miramedia.file_status import ImportOutcome
from miramedia.storage.states import apply_path_overlay, classify_sql_state


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (
            {
                "import_status": ImportOutcome.imported,
                "import_error": "sha1 mismatch (expected a…, got b…)",
                "sha1": "abc",
                "torrent_id": "t",
            },
            "corrupt",
        ),
        (
            {
                "import_status": ImportOutcome.imported,
                "import_error": None,
                "sha1": None,
                "torrent_id": "t",
            },
            "unknown",
        ),
        (
            {
                "import_status": ImportOutcome.imported,
                "import_error": None,
                "sha1": "abc",
                "torrent_id": "t",
            },
            "healthy",
        ),
        (
            {
                "import_status": ImportOutcome.failed_io,
                "import_error": "io",
                "sha1": None,
                "torrent_id": None,
            },
            "orphaned",
        ),
        (
            {
                "import_status": ImportOutcome.failed_no_match,
                "import_error": "no match",
                "sha1": None,
                "torrent_id": None,
            },
            "orphaned",
        ),
        (
            {
                "import_status": ImportOutcome.pending,
                "import_error": None,
                "sha1": None,
                "torrent_id": "t",
            },
            "pending",
        ),
        (
            {
                "import_status": ImportOutcome.failed_io,
                "import_error": "io",
                "sha1": None,
                "torrent_id": "t",
            },
            "pending",
        ),
        (
            {
                "import_status": ImportOutcome.ambiguous,
                "import_error": None,
                "sha1": None,
                "torrent_id": "t",
            },
            "pending",
        ),
        (
            {
                "import_status": ImportOutcome.imported,
                "import_error": "sha1 mismatch (expected a…, got b…)",
                "sha1": None,
                "torrent_id": "t",
            },
            "corrupt",
        ),
    ],
)
def test_sql_state_precedence(facts: dict, expected: str) -> None:
    assert classify_sql_state(**facts) == expected


def test_mismatch_with_null_sha1_is_corrupt_not_unknown() -> None:
    assert (
        classify_sql_state(
            import_status=ImportOutcome.imported,
            import_error="sha1 mismatch (expected a…, got b…)",
            sha1=None,
            torrent_id="t",
        )
        == "corrupt"
    )


def test_unknown_is_never_healthy() -> None:
    assert (
        classify_sql_state(
            import_status=ImportOutcome.imported,
            import_error=None,
            sha1=None,
            torrent_id="t",
        )
        != "healthy"
    )


def test_mismatch_plus_missing_path_and_ok_root_is_missing() -> None:
    sql = classify_sql_state(
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="abc",
        torrent_id="t",
    )
    assert apply_path_overlay(sql, library_ok=True, path=None) == "missing"


def test_imported_missing_path_ok_root_is_missing() -> None:
    for sha1, error in ((None, None), ("abc", None)):
        sql = classify_sql_state(
            import_status=ImportOutcome.imported,
            import_error=error,
            sha1=sha1,
            torrent_id="t",
        )
        assert apply_path_overlay(sql, library_ok=True, path=None) == "missing"


def test_failed_root_is_inaccessible_not_missing() -> None:
    sql = classify_sql_state(
        import_status=ImportOutcome.imported,
        import_error=None,
        sha1="abc",
        torrent_id="t",
    )
    assert apply_path_overlay(sql, library_ok=False, path=None) == "inaccessible"
    assert apply_path_overlay(sql, library_ok=False, path=None) != "missing"


def test_pending_without_path_is_not_missing() -> None:
    sql = classify_sql_state(
        import_status=ImportOutcome.pending,
        import_error=None,
        sha1=None,
        torrent_id="t",
    )
    assert apply_path_overlay(sql, library_ok=True, path=None) == "pending"


def test_orphaned_without_path_stays_orphaned() -> None:
    sql = classify_sql_state(
        import_status=ImportOutcome.failed_io,
        import_error="io",
        sha1=None,
        torrent_id=None,
    )
    assert apply_path_overlay(sql, library_ok=True, path=None) == "orphaned"


def test_present_path_keeps_sql_state() -> None:
    sql = classify_sql_state(
        import_status=ImportOutcome.imported,
        import_error="sha1 mismatch (expected a…, got b…)",
        sha1="abc",
        torrent_id="t",
    )
    assert apply_path_overlay(sql, library_ok=True, path="/data/file.mkv") == "corrupt"
