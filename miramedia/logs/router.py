from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from starlette import status

from miramedia.auth.users import current_superuser
from miramedia.logs.dependencies import log_repository_dep
from miramedia.logs.schemas import ActivityLogRead, PaginatedResponse

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/system",
    tags=["system"],
    dependencies=[Depends(current_superuser)],
)


@router.get("/logs")
async def get_logs(
    repo: log_repository_dep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    level: Annotated[str | None, Query()] = None,
    module: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> PaginatedResponse[ActivityLogRead]:
    items, total = await repo.get_paginated(
        offset=offset,
        limit=limit,
        level=level,
        module=module,
        search=search,
        start=start,
        end=end,
    )
    return PaginatedResponse[ActivityLogRead](
        items=[ActivityLogRead.model_validate(item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/logs/export")
async def export_logs(
    repo: log_repository_dep,
    level: Annotated[str | None, Query()] = None,
    module: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> StreamingResponse:
    """Stream logs as newline-delimited JSON. Honors the same filters as ``GET /logs``.

    Capped at 100k rows to keep the response bounded — narrow the filters for fuller dumps.
    """

    async def _generate() -> AsyncIterator[bytes]:
        async for row in repo.iter_filtered(
            level=level, module=module, search=search, start=start, end=end
        ):
            payload = ActivityLogRead.model_validate(row).model_dump(mode="json")
            yield (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")

    filename = f"miramedia-logs-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.ndjson"
    return StreamingResponse(
        _generate(),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/logs", status_code=status.HTTP_204_NO_CONTENT)
async def clear_all_logs(repo: log_repository_dep) -> None:
    """Delete all activity logs."""
    # Drain the handler's in-memory queue first so pending entries
    # don't get flushed into the DB after the delete.
    from miramedia.logs.handler import DatabaseLogHandler

    for handler in logging.getLogger().handlers:
        if isinstance(handler, DatabaseLogHandler):
            handler.drain()

    deleted = await repo.delete_all()
    log.info(f"Cleared all activity logs ({deleted} entries)")
