"""Sonarr v3-compatible API shim routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from miramedia.database import DbSessionDependency
from miramedia.subtitles.arr_shim import sonarr_service
from miramedia.subtitles.arr_shim.auth import require_shim_api_key
from miramedia.subtitles.arr_shim.fallback import add_catch_all

# Bazarr treats Sonarr 4.x as current-gen and skips legacy languageprofile calls.
SONARR_SHIM_VERSION = "4.0.0.0"

router = APIRouter(
    prefix="/sonarr/api/v3",
    dependencies=[Depends(require_shim_api_key)],
    include_in_schema=False,
)
QUALITY_PROFILES: list[dict[str, Any]] = [
    {"id": 1, "name": "MiraMedia", "upgradeAllowed": False, "items": []}
]

legacy_router = APIRouter(
    prefix="/sonarr/api",
    dependencies=[Depends(require_shim_api_key)],
    include_in_schema=False,
)


def _sonarr_status_payload() -> dict[str, str]:
    return {
        "version": SONARR_SHIM_VERSION,
        "appName": "Sonarr",
        "instanceName": "MiraMedia",
    }


@router.get("/system/status")
async def sonarr_system_status_v3() -> dict[str, str]:
    return _sonarr_status_payload()


@legacy_router.get("/system/status")
async def sonarr_system_status_legacy() -> dict[str, str]:
    return _sonarr_status_payload()


@router.get("/series")
async def list_series(db: DbSessionDependency) -> list[dict[str, Any]]:
    return await sonarr_service.list_series(db)


@router.get("/series/{series_id}")
async def get_series(series_id: int, db: DbSessionDependency) -> dict[str, Any]:
    return await sonarr_service.get_series(db, series_id)


@router.get("/episode")
async def list_episodes(
    db: DbSessionDependency,
    series_id: Annotated[int, Query(alias="seriesId")],
    include_episode_file: Annotated[bool, Query(alias="includeEpisodeFile")] = False,
) -> list[dict[str, Any]]:
    return await sonarr_service.list_episodes(
        db,
        series_arr_id=series_id,
        include_episode_file=include_episode_file,
    )


@router.get("/episode/{episode_id}")
async def get_episode(
    episode_id: int,
    db: DbSessionDependency,
    include_episode_file: Annotated[bool, Query(alias="includeEpisodeFile")] = False,
) -> dict[str, Any]:
    return await sonarr_service.get_episode(
        db,
        episode_id,
        include_episode_file=include_episode_file,
    )


@router.get("/episodeFile")
async def list_episode_files(
    db: DbSessionDependency,
    series_id: Annotated[int, Query(alias="seriesId")],
) -> list[dict[str, Any]]:
    return await sonarr_service.list_episode_files(db, series_arr_id=series_id)


@router.get("/episodeFile/{episode_file_id}")
async def get_episode_file(
    episode_file_id: int,
    db: DbSessionDependency,
) -> dict[str, Any]:
    return await sonarr_service.get_episode_file(db, episode_file_id)


@router.get("/rootfolder")
async def list_rootfolders() -> list[dict[str, Any]]:
    return sonarr_service.list_rootfolders()


@router.get("/tag")
async def list_tags() -> list[dict[str, Any]]:
    return sonarr_service.list_tags()


@router.get("/history")
async def list_history(
    event_type: Annotated[int, Query(alias="eventType")],
    episode_id: Annotated[int, Query(alias="episodeId")],
) -> list[dict[str, Any]]:
    _ = event_type, episode_id
    return sonarr_service.list_history()


@router.get("/qualityprofile")
async def list_quality_profiles() -> list[dict[str, Any]]:
    """Bazarr reads profile ids off series rows; one static profile is enough."""
    return QUALITY_PROFILES


@legacy_router.get("/qualityprofile")
async def list_quality_profiles_legacy() -> list[dict[str, Any]]:
    return QUALITY_PROFILES


add_catch_all(legacy_router, prefix="Sonarr")
