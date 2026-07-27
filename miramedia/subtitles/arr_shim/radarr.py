"""Radarr v3-compatible API shim routes."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from miramedia.database import DbSessionDependency
from miramedia.subtitles.arr_shim import radarr_service
from miramedia.subtitles.arr_shim.auth import require_shim_api_key
from miramedia.subtitles.arr_shim.fallback import add_catch_all

# Bazarr branches on Radarr major version; 5.x is current-gen.
RADARR_SHIM_VERSION = "5.0.0.0"

router = APIRouter(
    prefix="/radarr/api/v3",
    dependencies=[Depends(require_shim_api_key)],
    include_in_schema=False,
)
QUALITY_PROFILES: list[dict[str, Any]] = [
    {"id": 1, "name": "MiraMedia", "upgradeAllowed": False, "items": []}
]

legacy_router = APIRouter(
    prefix="/radarr/api",
    dependencies=[Depends(require_shim_api_key)],
    include_in_schema=False,
)


def _radarr_status_payload() -> dict[str, str]:
    return {
        "version": RADARR_SHIM_VERSION,
        "appName": "Radarr",
        "instanceName": "MiraMedia",
    }


@router.get("/system/status")
async def radarr_system_status_v3() -> dict[str, str]:
    return _radarr_status_payload()


@legacy_router.get("/system/status")
async def radarr_system_status_legacy() -> dict[str, str]:
    return _radarr_status_payload()


@router.get("/movie")
async def list_movies(db: DbSessionDependency) -> list[dict[str, Any]]:
    return await radarr_service.list_movies(db)


@router.get("/movie/{movie_id}")
async def get_movie(movie_id: int, db: DbSessionDependency) -> dict[str, Any]:
    return await radarr_service.get_movie(db, movie_id)


@router.get("/rootfolder")
async def list_rootfolders() -> list[dict[str, Any]]:
    return await asyncio.to_thread(radarr_service.list_rootfolders)


@router.get("/tag")
async def list_tags() -> list[dict[str, Any]]:
    return radarr_service.list_tags()


@router.get("/history")
async def list_history(
    event_type: Annotated[int, Query(alias="eventType")],
    movie_id: Annotated[int, Query(alias="movieId")],
) -> list[dict[str, Any]]:
    _ = event_type, movie_id
    return radarr_service.list_history()


@router.get("/qualityprofile")
async def list_quality_profiles() -> list[dict[str, Any]]:
    """Bazarr reads profile ids off series rows; one static profile is enough."""
    return QUALITY_PROFILES


@legacy_router.get("/qualityprofile")
async def list_quality_profiles_legacy() -> list[dict[str, Any]]:
    return QUALITY_PROFILES


add_catch_all(legacy_router, prefix="Radarr")
