"""API-key auth for the Sonarr/Radarr compatibility shim."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status

from miramedia.config import MiraMediaConfig


def _configured_shim_api_key() -> str:
    return MiraMediaConfig().subtitles.bazarr.shim_api_key


async def require_shim_api_key(request: Request) -> None:
    """Validate Bazarr's shim API key from query param or header.

    Bazarr sends ``?apikey=...`` on every request; ``X-Api-Key`` is accepted
    for manual testing. Returns 401 when the key is unset or mismatched.

    Comparison is byte-wise (UTF-8) so non-ASCII input fails closed with 401
    instead of raising from ``compare_digest``.
    """
    configured = _configured_shim_api_key()
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )

    provided = request.query_params.get("apikey") or request.headers.get("X-Api-Key")
    if provided is None or not secrets.compare_digest(
        provided.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
