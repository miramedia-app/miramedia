"""Payload digest helpers for idempotent viewing-state proposals."""

from __future__ import annotations

import hashlib

from miramedia.viewing_sync.schemas import ExternalViewingEvent


def payload_digest_for_event(event: ExternalViewingEvent) -> str:
    parts = [
        event.connector,
        event.connector_user_id,
        event.connector_item_id,
        str(event.position_ms),
        str(event.duration_ms),
        "1" if event.remote_played else "0",
        event.remote_at.isoformat() if event.remote_at else "",
    ]
    digest_input = "|".join(parts).encode()
    return hashlib.sha256(digest_input).hexdigest()
