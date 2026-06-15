"""Tests for /api/v1/metrics authentication gate.

The metrics endpoint is protected by ``current_superuser`` by default
(``misc.metrics_public = false``). This file verifies that an anonymous
request receives 401.

Testing the ``metrics_public = true`` opt-out is not cheaply testable at
import time (the ``config`` singleton is frozen after the first import) so
it is omitted here; the one-line conditional is covered by code review.
"""


def test_metrics_requires_auth_anonymous() -> None:
    from fastapi.testclient import TestClient

    from miramedia.database import get_session
    from miramedia.main import app

    async def _stub_session():
        # Anonymous requests must be rejected (401) by the auth layer before
        # they ever reach the DB, so yielding None is safe here. If the auth
        # stack somehow tries to use the session it will fail loudly, which
        # would mean the dependency chain changed and the test needs updating.
        yield None

    app.dependency_overrides[get_session] = _stub_session
    try:
        # raise_server_exceptions=False so a 500 surfaces as an assertion
        # failure rather than a confusing exception traceback.
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/v1/metrics")
        assert r.status_code == 401, (
            f"Expected 401 Unauthorized for anonymous /api/v1/metrics, got {r.status_code}. "
            "The metrics endpoint may not be protected — check the Instrumentator.expose() "
            "call in miramedia/main.py."
        )
    finally:
        app.dependency_overrides.clear()
