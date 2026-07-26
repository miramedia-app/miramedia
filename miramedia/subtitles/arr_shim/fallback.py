"""Catch-all handling for unmatched Sonarr/Radarr shim paths.

Two problems this solves, both found against a live Bazarr container:

1. Bazarr builds some URLs with a trailing slash (``/api/v3/series/``). Nothing
   redirects them: the SPA static mount sits at ``/`` and matches every
   unclaimed path, so Starlette never gets to emit its usual
   redirect-on-missing-slash. Bazarr then parsed the SPA's HTML as JSON and
   logged ``Expecting value: line 1 column 1``.
2. Bazarr probes arr endpoints the shim does not implement. Those also fell
   through to the SPA and came back as **HTML with status 200**, which reads as
   a successful response to every arr client.

A catch-all under each shim prefix keeps both cases inside the shim: trailing
slashes redirect to the canonical path, everything else is an honest JSON 404.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse


def add_catch_all(router: APIRouter, *, prefix: str) -> None:
    """Register a terminal catch-all on ``router`` (prefix ``/{arr}/api``).

    Must be registered on the *legacy* (shorter-prefix) router and after the
    real routes, so concrete paths always win route resolution.
    """

    @router.api_route(
        "/{rest:path}",
        methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"],
        include_in_schema=False,
    )
    async def shim_catch_all(rest: str, request: Request) -> RedirectResponse:
        if rest.endswith("/"):
            target = str(request.url.replace(path=request.url.path.rstrip("/")))
            return RedirectResponse(
                target, status_code=status.HTTP_307_TEMPORARY_REDIRECT
            )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{prefix} shim does not implement this endpoint",
        )
