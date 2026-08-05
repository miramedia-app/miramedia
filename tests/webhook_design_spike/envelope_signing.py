"""Design-spike: outbound webhook envelope + HMAC signing (pure; no network I/O).

Plan 239 evidence only — lives under tests/, not imported by NotificationManager,
config, or routes. A future implementation slice may promote this into miramedia/.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

ENVELOPE_VERSION = 1
EVENT_TYPE = "notification.message"
SOURCE = "miramedia"
SIGNATURE_PREFIX = "v1="
WEBHOOK_USER_AGENT = "MiraMedia-Webhook/1"
CONTENT_TYPE = "application/json; charset=utf-8"


@dataclass(frozen=True, slots=True)
class WebhookEnvelope:
    version: int
    id: str
    type: str
    time: str
    source: str
    title: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "id": self.id,
            "type": self.type,
            "time": self.time,
            "source": self.source,
            "data": {
                "title": self.title,
                "message": self.message,
            },
        }


def build_envelope(
    title: str,
    message: str,
    *,
    event_id: str | None = None,
    when: datetime | None = None,
) -> WebhookEnvelope:
    ts = when or datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    else:
        ts = ts.astimezone(UTC)
    # Millisecond Zulu form for stable examples/tests.
    time_str = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"
    return WebhookEnvelope(
        version=ENVELOPE_VERSION,
        id=event_id or str(uuid.uuid4()),
        type=EVENT_TYPE,
        time=time_str,
        source=SOURCE,
        title=title,
        message=message,
    )


def canonical_body_bytes(envelope: WebhookEnvelope) -> bytes:
    """Deterministic UTF-8 JSON used as POST body and signature input."""
    return json.dumps(
        envelope.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_body(
    secret: str,
    *,
    event_id: str,
    timestamp: int,
    body: bytes,
) -> str:
    """Return ``v1=<hex>`` HMAC-SHA256 over ``{id}.{timestamp}.{body}``."""
    if not secret:
        msg = "signing secret must be non-empty"
        raise ValueError(msg)
    material = f"{event_id}.{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), material, hashlib.sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def verify_signature(
    secret: str,
    *,
    event_id: str,
    timestamp: int,
    body: bytes,
    signature_header: str,
) -> bool:
    expected = sign_body(secret, event_id=event_id, timestamp=timestamp, body=body)
    return hmac.compare_digest(expected, signature_header)


def signing_headers(
    secret: str,
    envelope: WebhookEnvelope,
    *,
    timestamp: int,
    body: bytes | None = None,
) -> dict[str, str]:
    raw = body if body is not None else canonical_body_bytes(envelope)
    return {
        "X-MiraMedia-Webhook-Id": envelope.id,
        "X-MiraMedia-Webhook-Timestamp": str(timestamp),
        "X-MiraMedia-Webhook-Signature": sign_body(
            secret, event_id=envelope.id, timestamp=timestamp, body=raw
        ),
    }
