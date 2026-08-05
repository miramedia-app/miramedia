"""Envelope + HMAC signature determinism tests."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.webhook_design_spike.envelope_signing import (
    CONTENT_TYPE,
    EVENT_TYPE,
    WEBHOOK_USER_AGENT,
    build_envelope,
    canonical_body_bytes,
    sign_body,
    signing_headers,
    verify_signature,
)


def test_envelope_shape_and_canonical_bytes() -> None:
    env = build_envelope(
        "[MiraMedia] Movie Downloaded",
        "Movie Example has been successfully downloaded and imported.",
        event_id="11111111-2222-4333-8444-555555555555",
        when=datetime(2026, 8, 3, 18, 0, 0, tzinfo=UTC),
    )
    assert env.type == EVENT_TYPE
    assert env.version == 1
    body = canonical_body_bytes(env)
    assert body == (
        b'{"version":1,"id":"11111111-2222-4333-8444-555555555555",'
        b'"type":"notification.message","time":"2026-08-03T18:00:00.000Z",'
        b'"source":"miramedia","data":{"title":"[MiraMedia] Movie Downloaded",'
        b'"message":"Movie Example has been successfully downloaded and imported."}}'
    )


def test_signature_determinism_and_verify() -> None:
    env = build_envelope(
        "t",
        "m",
        event_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        when=datetime(2026, 1, 1, tzinfo=UTC),
    )
    body = canonical_body_bytes(env)
    secret = "test-signing-secret-not-real"
    ts = 1_700_000_000
    sig = sign_body(secret, event_id=env.id, timestamp=ts, body=body)
    assert sig.startswith("v1=")
    assert verify_signature(
        secret, event_id=env.id, timestamp=ts, body=body, signature_header=sig
    )
    assert not verify_signature(
        secret,
        event_id=env.id,
        timestamp=ts,
        body=body,
        signature_header="v1=" + ("0" * 64),
    )
    headers = signing_headers(secret, env, timestamp=ts, body=body)
    assert headers["X-MiraMedia-Webhook-Id"] == env.id
    assert headers["X-MiraMedia-Webhook-Timestamp"] == str(ts)
    assert headers["X-MiraMedia-Webhook-Signature"] == sig


def test_constants_for_client_headers() -> None:
    assert CONTENT_TYPE.startswith("application/json")
    assert WEBHOOK_USER_AGENT.startswith("MiraMedia-Webhook/")
