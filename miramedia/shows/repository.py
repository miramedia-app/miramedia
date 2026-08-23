from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from typing import cast as typing_cast
from uuid import UUID

from sqlalchemy import delete, func, not_, or_, select, tuple_, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload, selectinload
from sqlalchemy.sql import Select
from sqlalchemy.sql.base import ExecutableOption

from miramedia.exceptions import ConflictError, NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.media_filters import apply_list_filters, apply_sort
from miramedia.media_state import ProgressStatus
from miramedia.shows import log
from miramedia.shows.models import Episode, EpisodeFile, Season, Show
from miramedia.shows.schemas import Episode as EpisodeSchema
from miramedia.shows.schemas import (
    EpisodeAttributeChange,
    EpisodeId,
    EpisodeIntegrityContext,
    EpisodeNumber,
    SeasonId,
    SeasonNumber,
    ShowId,
)
from miramedia.shows.schemas import EpisodeFile as EpisodeFileSchema
from miramedia.shows.schemas import Season as SeasonSchema
from miramedia.shows.schemas import Show as ShowSchema
from miramedia.torrents.integrity import (
    integrity_audit_snapshot_where,
    integrity_mismatch_action_snapshot_where,
)
from miramedia.torrents.models import Torrent
from miramedia.torrents.schemas import Quality, TorrentId
from miramedia.torrents.schemas import Torrent as TorrentSchema


@dataclass(frozen=True, slots=True)
class ShowMatchCandidate:
    id: ShowId
    name: str
    year: int | None


_SHOW_INTEGRITY_COLUMNS = (
    Show.id,
    Show.name,
    Show.overview,
    Show.year,
    Show.ended,
    Show.external_id,
    Show.metadata_provider,
    Show.continuous_download,
    Show.skipped,
    Show.library,
    Show.original_language,
    Show.imdb_id,
    Show.vote_average,
    Show.content_rating,
    Show.genres,
    Show.cast,
    Show.preferred_quality,
    Show.preferred_codec,
    Show.subtitle_languages,
    Show.last_metadata_check,
    Show.metadata_failure_backoff_until,
    Show.auto_download_backoff_until,
    Show.wanted_episode_count,
    Show.downloaded_episode_count,
    Show.list_progress_status,
)


def _show_schema_from_row_mapping(row: Mapping[str, Any]) -> ShowSchema:
    """Build a ShowSchema from a scalar column mapping (no seasons graph)."""
    payload = dict(row)
    payload["id"] = ShowId(payload["id"])
    status = payload.get("list_progress_status")
    if status is not None and not isinstance(status, ProgressStatus):
        payload["list_progress_status"] = ProgressStatus(status)
    return ShowSchema.model_validate(payload)


def _full_show_eager_loads() -> tuple[ExecutableOption, ...]:
    """Eager-load chain: Show -> seasons -> episodes -> episode_files."""
    return (
        selectinload(Show.seasons)
        .selectinload(Season.episodes)
        .selectinload(Episode.episode_files),
    )


def _show_summary_eager_loads() -> tuple[ExecutableOption, ...]:
    """Minimal eager-loads for list/search previews.

    Returns just the columns needed to render a row — name, year, status,
    poster — without pulling season/episode/file rows. New code that only
    needs to know "does this show exist?" or "show me the title bar" should
    use this in preference to :func:`_full_show_eager_loads`.

    Downstream code MUST NOT touch ``Show.seasons`` on rows loaded this way
    or async lazy-load will blow up with ``MissingGreenlet``.
    """
    return (noload(Show.seasons),)


def _apply_show_list_filters(
    stmt: Select[tuple[object, ...]],
    *,
    query: str | None = None,
    libraries: list[str] | None = None,
    excluded_libraries: list[str] | None = None,
    genres: list[str] | None = None,
    excluded_genres: list[str] | None = None,
    decades: list[int] | None = None,
    excluded_decades: list[int] | None = None,
    airing: list[str] | None = None,
    excluded_airing: list[str] | None = None,
) -> Select[tuple[object, ...]]:
    """Apply server-side filters shared by list + count endpoints."""
    stmt = apply_list_filters(
        stmt,
        name_col=Show.name,
        library_col=Show.library,
        genres_col=Show.genres,
        year_col=Show.year,
        query=query,
        libraries=libraries,
        excluded_libraries=excluded_libraries,
        genres=genres,
        excluded_genres=excluded_genres,
        decades=decades,
        excluded_decades=excluded_decades,
    )
    if airing:
        clauses = []
        if "ended" in airing:
            clauses.append(Show.ended.is_(True))
        if "continuing" in airing:
            clauses.append(Show.ended.is_(False))
        if clauses:
            stmt = stmt.where(or_(*clauses))
    if excluded_airing:
        clauses = []
        if "ended" in excluded_airing:
            clauses.append(Show.ended.is_(True))
        if "continuing" in excluded_airing:
            clauses.append(Show.ended.is_(False))
        if clauses:
            stmt = stmt.where(not_(or_(*clauses)))
    return stmt


def _apply_show_status_filters(
    stmt: Select[tuple[object, ...]],
    *,
    statuses: list[str] | None = None,
    excluded_statuses: list[str] | None = None,
) -> Select[tuple[object, ...]]:
    if statuses:
        stmt = stmt.where(Show.list_progress_status.in_(statuses))
    if excluded_statuses:
        stmt = stmt.where(not_(Show.list_progress_status.in_(excluded_statuses)))
    return stmt


def _apply_show_sort(
    stmt: Select[tuple[object, ...]], sort: str | None
) -> Select[tuple[object, ...]]:
    return apply_sort(
        stmt,
        sort,
        name_col=Show.name,
        year_col=Show.year,
        rating_col=Show.vote_average,
    )


class ShowRepository:
    """
    Repository for managing shows, seasons, and episodes in the database.
    Provides methods to retrieve, save, and delete shows and seasons.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_show_by_id(self, show_id: ShowId) -> ShowSchema:
        """
        Retrieve a show by its ID, including seasons and episodes.

        :param show_id: The ID of the show to retrieve.
        :return: A Show object if found.
        :raises NotFoundError: If the show with the given ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Show)
                .where(Show.id == show_id)
                .options(*_full_show_eager_loads())
            )
            result = (await self.db.execute(stmt)).unique().scalar_one_or_none()
            if not result:
                msg = f"Show with id {show_id} not found."
                raise NotFoundError(msg)
            return ShowSchema.model_validate(result)
        except SQLAlchemyError:
            log.exception("Database error while retrieving show %s", show_id)
            raise

    async def get_show_by_external_id(
        self, external_id: str, metadata_provider: str
    ) -> ShowSchema:
        """
        Retrieve a show by its metadata provider ID, including nested seasons and episodes.
        """
        try:
            stmt = (
                select(Show)
                .where(Show.external_id == external_id)
                .where(Show.metadata_provider == metadata_provider)
                .options(*_full_show_eager_loads())
            )
            result = (await self.db.execute(stmt)).unique().scalar_one_or_none()
            if not result:
                msg = f"Show with external_id {external_id} and provider {metadata_provider} not found."
                raise NotFoundError(msg)
            return ShowSchema.model_validate(result)
        except SQLAlchemyError:
            log.exception(
                "Database error while retrieving show by external_id %s",
                external_id,
            )
            raise

    async def show_exists_by_imdb_id(self, imdb_id: str) -> ShowSchema | None:
        """Return an existing show summary matched by imdb_id, or None."""
        try:
            stmt = (
                select(Show)
                .where(Show.imdb_id == imdb_id)
                .options(*_show_summary_eager_loads())
            )
            result = (await self.db.execute(stmt)).scalars().first()
        except SQLAlchemyError:
            log.exception("Error checking show existence for imdb_id %s", imdb_id)
            return None
        else:
            if result:
                return ShowSchema.model_validate(result)
            return None

    async def shows_existing_by_identifiers(
        self,
        imdb_ids: list[str],
        provider_keys: list[tuple[str, str]],
    ) -> list[tuple[str | None, str, str, ShowId]]:
        """Bulk lookup of shows matching search results, mirroring the scan's
        ``_resolve_existing`` three-way match: a row counts as existing when
        EITHER ``imdb_id`` OR ``external_id`` equals a result's IMDb id, OR
        ``(external_id, metadata_provider)`` equals a result's provider key.

        Native-provider shows store the IMDb id in ``external_id`` and leave the
        ``imdb_id`` column NULL (the folder is still tagged ``[imdb-tt...]``), so
        an imdb_id-only lookup wrongly flags them as not-added ("Add" vs "View").
        Returns ``(imdb_id, external_id, metadata_provider, id)`` rows so the
        caller can build the lookup maps; projects scalar columns only, so no
        seasons/episodes eager-load.
        """
        if not imdb_ids and not provider_keys:
            return []
        conditions = []
        if imdb_ids:
            conditions.append(Show.imdb_id.in_(imdb_ids))
            conditions.append(Show.external_id.in_(imdb_ids))
        if provider_keys:
            conditions.append(
                tuple_(Show.external_id, Show.metadata_provider).in_(provider_keys)
            )
        try:
            stmt = select(
                Show.imdb_id, Show.external_id, Show.metadata_provider, Show.id
            ).where(or_(*conditions))
            rows = (await self.db.execute(stmt)).all()
            return [
                (imdb_id, ext, prov, show_id) for imdb_id, ext, prov, show_id in rows
            ]
        except SQLAlchemyError:
            log.exception("Bulk show lookup by identifiers failed")
            return []

    async def native_imdb_index(self) -> dict[str, ShowId]:
        """Map IMDb id -> show id for native-provider (IMDb-keyed) rows.

        These are the library rows that can't be matched by a TMDB/TVDB search
        result's provider key (their key is ``(tt..., 'native')``). Used to
        bridge "Add vs View" after enriching a result with its IMDb id. Empty
        for libraries with no native/scan-imported shows, so the enrichment
        pass is skipped entirely for pure-TMDB users.
        """
        try:
            stmt = select(Show.external_id, Show.id).where(
                Show.metadata_provider == "native"
            )
            rows = (await self.db.execute(stmt)).all()
            return {ext: sid for ext, sid in rows if ext}
        except SQLAlchemyError:
            log.exception("Native IMDb index lookup failed")
            return {}

    async def get_shows(self) -> list[ShowSchema]:
        """
        Retrieve all shows from the database.

        :return: A list of Show objects.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = select(Show).options(*_full_show_eager_loads())
            results = (await self.db.execute(stmt)).scalars().unique().all()
            return [ShowSchema.model_validate(show) for show in results]
        except SQLAlchemyError:
            log.exception("Database error while retrieving all shows")
            raise

    async def get_show_match_candidates(self) -> list[ShowMatchCandidate]:
        """Return (id, name, year) rows for fuzzy title matching without seasons."""
        try:
            stmt = select(Show.id, Show.name, Show.year)
            rows = (await self.db.execute(stmt)).all()
            return [
                ShowMatchCandidate(id=ShowId(show_id), name=name, year=year)
                for show_id, name, year in rows
            ]
        except SQLAlchemyError:
            log.exception("Database error while retrieving show match candidates")
            raise

    async def get_all_shows_with_tree(self) -> list[Show]:
        """Return every show ORM row with seasons, episodes, and files loaded."""
        try:
            stmt = select(Show).options(*_full_show_eager_loads())
            return list((await self.db.execute(stmt)).scalars().unique().all())
        except SQLAlchemyError:
            log.exception("Database error while retrieving all shows for arr shim")
            raise

    async def get_show_with_tree_by_id(self, show_id: UUID) -> Show | None:
        """Return one show ORM row with the full tree loaded, or ``None``."""
        try:
            stmt = (
                select(Show)
                .where(Show.id == show_id)
                .options(*_full_show_eager_loads())
            )
            return (await self.db.execute(stmt)).unique().scalar_one_or_none()
        except SQLAlchemyError:
            log.exception(
                "Database error while retrieving show %s for arr shim", show_id
            )
            raise

    async def get_episode_with_show_tree(self, episode_id: UUID) -> Episode | None:
        """Return one episode with season, show, and files eager-loaded.

        ``Show.seasons`` is **not** loaded. Callers must not touch it or async
        lazy-load will raise ``MissingGreenlet``.
        """
        try:
            stmt = (
                select(Episode)
                .where(Episode.id == episode_id)
                .options(
                    selectinload(Episode.episode_files),
                    selectinload(Episode.season).selectinload(Season.show),
                )
            )
            return (await self.db.execute(stmt)).unique().scalar_one_or_none()
        except SQLAlchemyError:
            log.exception(
                "Database error while retrieving episode %s for arr shim", episode_id
            )
            raise

    async def get_episode_file_with_show_tree(
        self, file_id: UUID
    ) -> EpisodeFile | None:
        """Return one episode file with episode, season, show, and files loaded.

        ``Show.seasons`` is **not** loaded. Callers must not touch it or async
        lazy-load will raise ``MissingGreenlet``.
        """
        try:
            stmt = (
                select(EpisodeFile)
                .where(EpisodeFile.id == file_id)
                .options(
                    selectinload(EpisodeFile.episode)
                    .selectinload(Episode.season)
                    .selectinload(Season.show),
                    selectinload(EpisodeFile.episode).selectinload(
                        Episode.episode_files
                    ),
                )
            )
            return (await self.db.execute(stmt)).unique().scalar_one_or_none()
        except SQLAlchemyError:
            log.exception(
                "Database error while retrieving episode_file %s for arr shim", file_id
            )
            raise

    async def get_show_ids(self) -> list[ShowId]:
        """Return all show primary keys without eager-loading the library tree."""
        try:
            return typing_cast(
                "list[ShowId]",
                list((await self.db.execute(select(Show.id))).scalars().all()),
            )
        except SQLAlchemyError:
            log.exception("Database error while retrieving all show ids")
            raise

    async def get_shows_paginated(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        query: str | None = None,
        sort: str | None = None,
        libraries: list[str] | None = None,
        excluded_libraries: list[str] | None = None,
        genres: list[str] | None = None,
        excluded_genres: list[str] | None = None,
        decades: list[int] | None = None,
        excluded_decades: list[int] | None = None,
        airing: list[str] | None = None,
        excluded_airing: list[str] | None = None,
        statuses: list[str] | None = None,
        excluded_statuses: list[str] | None = None,
    ) -> tuple[list[Show], int]:
        """Paginated variant of :meth:`get_shows` for list endpoints.

        Pushes LIMIT/OFFSET to SQL instead of slicing a fully-hydrated
        in-memory list. Returns ORM rows (not :class:`ShowSchema`) so
        callers avoid Pydantic touching unloaded ``seasons``.
        """
        try:
            count_stmt = _apply_show_status_filters(
                _apply_show_list_filters(
                    select(func.count()).select_from(Show),
                    query=query,
                    libraries=libraries,
                    excluded_libraries=excluded_libraries,
                    genres=genres,
                    excluded_genres=excluded_genres,
                    decades=decades,
                    excluded_decades=excluded_decades,
                    airing=airing,
                    excluded_airing=excluded_airing,
                ),
                statuses=statuses,
                excluded_statuses=excluded_statuses,
            )
            stmt = _apply_show_sort(
                _apply_show_status_filters(
                    _apply_show_list_filters(
                        select(Show).options(*_show_summary_eager_loads()),
                        query=query,
                        libraries=libraries,
                        excluded_libraries=excluded_libraries,
                        genres=genres,
                        excluded_genres=excluded_genres,
                        decades=decades,
                        excluded_decades=excluded_decades,
                        airing=airing,
                        excluded_airing=excluded_airing,
                    ),
                    statuses=statuses,
                    excluded_statuses=excluded_statuses,
                ),
                sort,
            )
            stmt = stmt.offset(offset).limit(limit)
            total = (await self.db.scalar(count_stmt)) or 0
            rows = (await self.db.execute(stmt)).scalars().unique().all()
            return list(rows), int(total)
        except SQLAlchemyError:
            log.exception("Database error while paginating shows")
            raise

    async def count_shows_filtered(
        self,
        *,
        query: str | None = None,
        libraries: list[str] | None = None,
        excluded_libraries: list[str] | None = None,
        genres: list[str] | None = None,
        excluded_genres: list[str] | None = None,
        decades: list[int] | None = None,
        excluded_decades: list[int] | None = None,
        airing: list[str] | None = None,
        excluded_airing: list[str] | None = None,
        statuses: list[str] | None = None,
        excluded_statuses: list[str] | None = None,
    ) -> int:
        try:
            stmt = _apply_show_status_filters(
                _apply_show_list_filters(
                    select(func.count()).select_from(Show),
                    query=query,
                    libraries=libraries,
                    excluded_libraries=excluded_libraries,
                    genres=genres,
                    excluded_genres=excluded_genres,
                    decades=decades,
                    excluded_decades=excluded_decades,
                    airing=airing,
                    excluded_airing=excluded_airing,
                ),
                statuses=statuses,
                excluded_statuses=excluded_statuses,
            )
            return int((await self.db.scalar(stmt)) or 0)
        except SQLAlchemyError:
            log.exception("Database error while counting shows")
            raise

    async def get_show_facets(self) -> dict[str, list]:
        """Return filter option values for show grids without loading episodes."""
        try:
            rows = (
                await self.db.execute(select(Show.library, Show.genres, Show.year))
            ).all()
            libraries: set[str] = set()
            genres: set[str] = set()
            decades: set[int] = set()
            for library, row_genres, year in rows:
                if library:
                    libraries.add(library)
                for genre in row_genres or []:
                    genres.add(genre)
                if year is not None:
                    decades.add((year // 10) * 10)
            return {
                "libraries": sorted(libraries),
                "genres": sorted(genres),
                "decades": sorted(decades, reverse=True),
            }
        except SQLAlchemyError:
            log.exception("Database error while loading show facets")
            raise

    async def get_total_downloaded_episodes_count(self) -> int:
        try:
            stmt = select(func.count(Episode.id)).select_from(Episode).join(EpisodeFile)
            return (await self.db.execute(stmt)).scalar_one_or_none() or 0
        except SQLAlchemyError:
            log.exception("Database error while calculating downloaded episodes count")
            raise

    async def save_show(self, show: ShowSchema) -> ShowSchema:
        """
        Save a new show or update an existing one in the database.

        :param show: The Show object to save.
        :return: The saved Show object.
        :raises ValueError: If a show with the same primary key already exists (on insert).
        :raises SQLAlchemyError: If a database error occurs.
        """
        # Native/Cinemeta media are keyed on the IMDb id (external_id == 'tt...').
        # Mirror it into imdb_id when the provider didn't populate the column so
        # IMDb-keyed rows always carry it — keeps "Add vs View" matching and
        # cross-provider dedup working without relying on external_id fallbacks.
        if not show.imdb_id and show.external_id.startswith("tt"):
            show.imdb_id = show.external_id
        # Use a fresh query with eager loads so any return path has relationships loaded.
        db_show = None
        if show.id:
            stmt = (
                select(Show)
                .where(Show.id == show.id)
                .options(*_full_show_eager_loads())
            )
            db_show = (await self.db.execute(stmt)).unique().scalar_one_or_none()

        if db_show:  # Update existing show
            db_show.external_id = show.external_id
            db_show.metadata_provider = show.metadata_provider
            db_show.name = show.name
            db_show.overview = show.overview
            db_show.year = show.year
            db_show.original_language = show.original_language
            db_show.imdb_id = show.imdb_id
            existing_seasons = {season.number: season for season in db_show.seasons}
            for season in show.seasons:
                db_season = existing_seasons.get(season.number)
                if db_season is None:
                    continue
                existing_episodes = {
                    episode.number: episode for episode in db_season.episodes
                }
                for episode in season.episodes:
                    db_episode = existing_episodes.get(episode.number)
                    if db_episode is not None and episode.air_date is not None:
                        db_episode.air_date = episode.air_date
                    if db_episode is not None and episode.air_time is not None:
                        db_episode.air_time = episode.air_time
        else:  # Insert new show
            db_show = Show(
                id=show.id,
                external_id=show.external_id,
                metadata_provider=show.metadata_provider,
                name=show.name,
                overview=show.overview,
                year=show.year,
                ended=show.ended,
                original_language=show.original_language,
                imdb_id=show.imdb_id,
                seasons=[
                    Season(
                        id=season.id,
                        show_id=show.id,
                        number=season.number,
                        skipped=season.skipped,
                        episodes=[
                            Episode(
                                id=episode.id,
                                season_id=season.id,
                                number=episode.number,
                                title=episode.title,
                                overview=episode.overview,
                                air_date=episode.air_date,
                                air_time=episode.air_time,
                                skipped=episode.skipped,
                            )
                            for episode in season.episodes
                        ],
                    )
                    for season in show.seasons
                ],
            )
            self.db.add(db_show)

        try:
            await self.db.commit()
            # Re-fetch with eager loads so model_validate can traverse relationships.
            stmt = (
                select(Show)
                .where(Show.id == db_show.id)
                .options(*_full_show_eager_loads())
            )
            db_show = (await self.db.execute(stmt)).unique().scalar_one()
            return ShowSchema.model_validate(db_show)
        except IntegrityError as e:
            await self.db.rollback()
            msg = f"Show with this primary key or unique constraint violation: {e.orig}"
            raise ConflictError(msg) from e
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception("Database error while saving show %s", show.name)
            raise

    async def delete_show(self, show_id: ShowId) -> None:
        """
        Delete a show by its ID.

        :param show_id: The ID of the show to delete.
        :raises NotFoundError: If the show with the given ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            show = await self.db.get(Show, show_id)
            if not show:
                msg = f"Show with id {show_id} not found."
                raise NotFoundError(msg)
            await self.db.delete(show)
            await self.db.commit()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception("Database error while deleting show %s", show_id)
            raise

    async def get_season(self, season_id: SeasonId) -> SeasonSchema:
        """
        Retrieve a season by its ID.

        :param season_id: The ID of the season to get.
        :return: A Season object.
        :raises NotFoundError: If the season with the given ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Season)
                .where(Season.id == season_id)
                .options(
                    selectinload(Season.episodes).selectinload(Episode.episode_files),
                    selectinload(Season.show),
                )
            )
            season = (await self.db.execute(stmt)).unique().scalar_one_or_none()
            if not season:
                msg = f"Season with id {season_id} not found."
                raise NotFoundError(msg)
            return SeasonSchema.model_validate(season)
        except SQLAlchemyError:
            log.exception("Database error while retrieving season %s", season_id)
            raise

    async def get_episode(self, episode_id: EpisodeId) -> EpisodeSchema:
        """
        Retrieve an episode by its ID.

        :param episode_id: The ID of the episode to get.
        :return: An Episode object.
        :raises NotFoundError: If the episode with the given ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Episode)
                .where(Episode.id == episode_id)
                .options(selectinload(Episode.episode_files))
            )
            episode = (await self.db.execute(stmt)).unique().scalar_one_or_none()
            if not episode:
                msg = f"Episode with id {episode_id} not found."
                raise NotFoundError(msg)
            return EpisodeSchema.model_validate(episode)
        except SQLAlchemyError as e:
            log.error("Database error while retrieving episode %s: %s", episode_id, e)
            raise

    async def get_season_by_episode(self, episode_id: EpisodeId) -> SeasonSchema:
        try:
            stmt = (
                select(Season)
                .join(Season.episodes)
                .where(Episode.id == episode_id)
                .options(
                    selectinload(Season.episodes).selectinload(Episode.episode_files),
                    selectinload(Season.show),
                )
            )

            season = (await self.db.execute(stmt)).unique().scalar_one_or_none()

            if not season:
                msg = f"Season not found for episode {episode_id}"
                raise NotFoundError(msg)

            return SeasonSchema.model_validate(season)

        except SQLAlchemyError as e:
            log.error(
                "Database error while retrieving season for episode %s: %s",
                episode_id,
                e,
            )
            raise

    async def get_episodes_with_seasons(
        self, episode_ids: list[EpisodeId]
    ) -> dict[EpisodeId, tuple[SeasonSchema, EpisodeSchema]]:
        """Batch-fetch episodes and their parent seasons for import matching."""
        if not episode_ids:
            return {}
        try:
            stmt = (
                select(Episode)
                .where(Episode.id.in_(episode_ids))
                .options(
                    selectinload(Episode.episode_files),
                    selectinload(Episode.season)
                    .selectinload(Season.episodes)
                    .selectinload(Episode.episode_files),
                )
            )
            episodes = (await self.db.execute(stmt)).unique().scalars().all()
            result: dict[EpisodeId, tuple[SeasonSchema, EpisodeSchema]] = {}
            for episode in episodes:
                season = episode.season
                if season is None:
                    continue
                result[EpisodeId(episode.id)] = (
                    SeasonSchema.model_validate(season),
                    EpisodeSchema.model_validate(episode),
                )
        except SQLAlchemyError:
            log.exception(
                "Database error while batch-retrieving episodes %s", episode_ids
            )
            raise
        else:
            return result

    async def get_season_by_number(
        self, season_number: int, show_id: ShowId
    ) -> SeasonSchema:
        """
        Retrieve a season by its number and show ID.

        :param season_number: The number of the season.
        :param show_id: The ID of the show.
        :return: A Season object.
        :raises NotFoundError: If the season is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Season)
                .where(Season.show_id == show_id)
                .where(Season.number == season_number)
                .options(
                    selectinload(Season.episodes).selectinload(Episode.episode_files),
                    selectinload(Season.show),
                )
            )
            result = (await self.db.execute(stmt)).unique().scalar_one_or_none()
            if not result:
                msg = f"Season number {season_number} for show_id {show_id} not found."
                raise NotFoundError(msg)
            return SeasonSchema.model_validate(result)
        except SQLAlchemyError:
            log.exception(
                "Database error retrieving season %s for show %s",
                season_number,
                show_id,
            )
            raise

    async def add_episode_files(
        self, episode_files: list[EpisodeFileSchema]
    ) -> list[EpisodeFileSchema]:
        """Insert many episode-file rows in one transaction (single commit)."""
        db_models = [
            EpisodeFile(**episode_file.model_dump()) for episode_file in episode_files
        ]
        try:
            self.db.add_all(db_models)
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            log.exception(
                "Integrity error while adding %d episode files", len(db_models)
            )
            raise
        except SQLAlchemyError as e:
            await self.db.rollback()
            log.error("Database error while adding episode files: %s", e)
            raise
        return [EpisodeFileSchema.model_validate(model) for model in db_models]

    async def add_episode_file(
        self, episode_file: EpisodeFileSchema
    ) -> EpisodeFileSchema:
        """
        Adds an episode file record to the database.

        :param episode_file: The EpisodeFile object to add.
        :return: The added EpisodeFile object.
        :raises IntegrityError: If the record violates constraints.
        :raises SQLAlchemyError: If a database error occurs.
        """
        results = await self.add_episode_files([episode_file])
        return results[0]

    async def get_episode_file_by_id(self, file_id: UUID) -> EpisodeFileSchema | None:
        """Load a single episode file row by its surrogate id, or ``None``."""
        try:
            db_model = await self.db.get(EpisodeFile, file_id)
            if db_model is None:
                return None
            return EpisodeFileSchema.model_validate(db_model)
        except SQLAlchemyError:
            log.exception("Database error while retrieving episode_file %s", file_id)
            raise

    async def update_episode_file_import_status(
        self,
        *,
        file_id: UUID,
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        """Persist a new import outcome for the given episode file row."""
        await self.update_episode_file_import_status_bulk(
            file_ids=[file_id],
            status=status,
            error=error,
        )

    async def update_episode_file_import_status_bulk(
        self,
        *,
        file_ids: list[UUID],
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        """Persist the same import outcome for many episode file rows."""
        if not file_ids:
            return
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        try:
            stmt = (
                update(EpisodeFile)
                .where(EpisodeFile.id.in_(file_ids))
                .values(
                    import_status=status,
                    import_error=error,
                    last_attempt_at=now,
                    attempt_count=EpisodeFile.attempt_count + 1,
                    imported_at=(
                        now
                        if status == ImportOutcome.imported
                        else EpisodeFile.imported_at
                    ),
                )
            )
            await self.db.execute(stmt)
            await self.db.flush()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(
                "Failed to bulk-update import status for %d episode files",
                len(file_ids),
            )
            raise

    async def get_orphaned_failed_episode_files(self) -> list[EpisodeFileSchema]:
        """Episode files stuck ``failed_*`` with no torrent left to surface them.

        These are "ghost" failures: a torrent's cleanup (FK ``ON DELETE SET
        NULL``) detached the row after an overlapping import marked it failed,
        so the imports page (torrent-centric) can never show or retry them, yet
        the dashboard's file-count badge still counts them.
        """
        stmt = select(EpisodeFile).where(
            EpisodeFile.torrent_id.is_(None),
            EpisodeFile.import_status.in_(
                (ImportOutcome.failed_io, ImportOutcome.failed_no_match)
            ),
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return [EpisodeFileSchema.model_validate(r) for r in rows]

    async def finalize_episode_file_import(
        self,
        *,
        file_id: UUID,
        quality: Quality,
        codec: str,
        hdr: bool,
        source: str,
        variant: str,
        extra: str,
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        """Stamp the detected naming components + import outcome on a row.

        Sets every naming column (quality/codec/hdr/source/variant/extra) plus
        the import bookkeeping (``last_attempt_at``, ``attempt_count`` bump, and
        ``imported_at`` when the outcome is ``imported``).
        """
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        try:
            stmt = (
                update(EpisodeFile)
                .where(EpisodeFile.id == file_id)
                .values(
                    quality=quality,
                    codec=codec,
                    hdr=hdr,
                    source=source,
                    variant=variant,
                    extra=extra,
                    import_status=status,
                    import_error=error,
                    last_attempt_at=now,
                    attempt_count=EpisodeFile.attempt_count + 1,
                    imported_at=(
                        now
                        if status == ImportOutcome.imported
                        else EpisodeFile.imported_at
                    ),
                )
            )
            await self.db.execute(stmt)
            await self.db.flush()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception("Failed to finalize import for episode_file %s", file_id)
            raise

    async def set_episode_file_sha1(
        self,
        *,
        file_id: UUID,
        sha1: str | None,
    ) -> None:
        """Persist (or clear) the integrity-audit SHA1 for an episode file."""
        try:
            stmt = (
                update(EpisodeFile).where(EpisodeFile.id == file_id).values(sha1=sha1)
            )
            await self.db.execute(stmt)
            await self.db.flush()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception("Failed to set sha1 for episode_file %s", file_id)
            raise

    async def count_sha1_mismatch_files(self) -> int:
        """Count imported episode files with a SHA1 mismatch stamp."""
        stmt = (
            select(func.count())
            .select_from(EpisodeFile)
            .where(
                EpisodeFile.import_status == ImportOutcome.imported,
                EpisodeFile.import_error.like("sha1 mismatch%"),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def list_sha1_mismatch_files(
        self, *, offset: int = 0, limit: int
    ) -> list[EpisodeFileSchema]:
        """Imported episode files whose integrity audit recorded a SHA1 mismatch.

        Contract: ``import_error`` prefix ``sha1 mismatch%`` must stay in sync
        with ``verify_imported_files_task`` in ``miramedia/scheduler.py``.
        Rows are ordered by ``id`` ascending for stable segmented pagination.
        """
        stmt = (
            select(EpisodeFile)
            .where(
                EpisodeFile.import_status == ImportOutcome.imported,
                EpisodeFile.import_error.like("sha1 mismatch%"),
            )
            .order_by(EpisodeFile.id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return [EpisodeFileSchema.model_validate(r) for r in rows]

    async def get_sha1_mismatch_episode_files_by_ids(
        self, file_ids: list[UUID]
    ) -> dict[UUID, EpisodeFileSchema]:
        """Batch-load mismatch episode files still matching the list predicate."""
        if not file_ids:
            return {}
        stmt = select(EpisodeFile).where(
            EpisodeFile.id.in_(file_ids),
            EpisodeFile.import_status == ImportOutcome.imported,
            EpisodeFile.import_error.like("sha1 mismatch%"),
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return {row.id: EpisodeFileSchema.model_validate(row) for row in rows}

    async def get_shows_by_ids(
        self, show_ids: list[ShowId]
    ) -> dict[ShowId, ShowSchema]:
        """Batch-load shows by primary key for integrity path resolution."""
        if not show_ids:
            return {}
        stmt = select(*_SHOW_INTEGRITY_COLUMNS).where(Show.id.in_(show_ids))
        rows = (await self.db.execute(stmt)).mappings().all()
        return {ShowId(row["id"]): _show_schema_from_row_mapping(row) for row in rows}

    async def get_episode_ids_with_imported_files(self) -> list[EpisodeId]:
        """Episode IDs with at least one imported episode file row.

        Used by bulk subtitle scans to avoid loading full show/season trees.
        """
        try:
            stmt = (
                select(Episode.id)
                .distinct()
                .join(EpisodeFile, EpisodeFile.episode_id == Episode.id)
                .where(EpisodeFile.import_status == ImportOutcome.imported)
            )
            return typing_cast(
                "list[EpisodeId]",
                list((await self.db.execute(stmt)).scalars().all()),
            )
        except SQLAlchemyError:
            log.exception(
                "Database error while retrieving episode ids with imported files"
            )
            raise

    async def batch_episodes_with_context(
        self, episode_ids: list[EpisodeId]
    ) -> dict[EpisodeId, EpisodeIntegrityContext]:
        """Batch-load episode number + season number + show name for mismatch rows."""
        if not episode_ids:
            return {}
        stmt = (
            select(
                Episode.id,
                Episode.number,
                Season.number,
                Season.show_id,
                Show.name,
            )
            .join(Season, Episode.season_id == Season.id)
            .join(Show, Season.show_id == Show.id)
            .where(Episode.id.in_(episode_ids))
        )
        rows = (await self.db.execute(stmt)).all()
        return {
            EpisodeId(episode_id): EpisodeIntegrityContext(
                episode_number=int(episode_number),
                season_number=int(season_number),
                show_id=ShowId(show_id),
                show_name=show_name,
            )
            for episode_id, episode_number, season_number, show_id, show_name in rows
        }

    async def apply_integrity_baseline_if_current(
        self,
        file_id: UUID,
        *,
        expected_sha1: None,
        expected_import_error: str | None,
        new_sha1: str,
    ) -> bool:
        """Set ``sha1`` when the row still matches the pre-hash snapshot."""
        try:
            stmt = (
                update(EpisodeFile)
                .where(
                    integrity_audit_snapshot_where(
                        EpisodeFile,
                        file_id,
                        expected_sha1=expected_sha1,
                        expected_import_error=expected_import_error,
                    )
                )
                .values(sha1=new_sha1)
            )
            result = await self.db.execute(stmt)
            await self.db.flush()
            return bool(result.rowcount)
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(
                "Failed to baseline integrity sha1 for episode_file %s", file_id
            )
            raise

    async def stamp_integrity_mismatch_if_current(
        self,
        file_id: UUID,
        *,
        expected_sha1: str,
        expected_import_error: str | None,
        import_error: str,
    ) -> bool:
        """Stamp a mismatch error when the row still matches the pre-hash snapshot."""
        try:
            stmt = (
                update(EpisodeFile)
                .where(
                    integrity_audit_snapshot_where(
                        EpisodeFile,
                        file_id,
                        expected_sha1=expected_sha1,
                        expected_import_error=expected_import_error,
                    )
                )
                .values(import_error=import_error)
            )
            result = await self.db.execute(stmt)
            await self.db.flush()
            return bool(result.rowcount)
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(
                "Failed to stamp integrity mismatch for episode_file %s", file_id
            )
            raise

    async def clear_file_integrity_state(
        self,
        file_id: UUID,
        *,
        expected_sha1: str | None,
        expected_import_error: str,
        reset_sha1: bool,
    ) -> bool:
        """Clear mismatch state when the row still matches the action snapshot.

        Returns ``True`` when a row was updated. Returns ``False`` when the row
        is unknown or no longer matches the observed mismatch fields.
        """
        values: dict[str, object] = {"import_error": None}
        if reset_sha1:
            values["sha1"] = None
        try:
            stmt = (
                update(EpisodeFile)
                .where(
                    integrity_mismatch_action_snapshot_where(
                        EpisodeFile,
                        file_id,
                        expected_sha1=expected_sha1,
                        expected_import_error=expected_import_error,
                    )
                )
                .values(**values)
            )
            result = await self.db.execute(stmt)
            await self.db.flush()
            return bool(result.rowcount)
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(
                "Failed to clear integrity state for episode_file %s", file_id
            )
            raise

    async def remove_episode_files_by_torrent_id(self, torrent_id: TorrentId) -> int:
        """
        Removes episode file records associated with a given torrent ID.

        :param torrent_id: The ID of the torrent whose episode files are to be removed.
        :return: The number of episode files removed.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = delete(EpisodeFile).where(EpisodeFile.torrent_id == torrent_id)
            result = await self.db.execute(stmt)
            await self.db.commit()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(
                "Database error removing episode files for torrent_id %s",
                torrent_id,
            )
            raise
        return result.rowcount

    async def set_show_library(self, show_id: ShowId, library: str) -> None:
        """
        Sets the library for a show.

        :param show_id: The ID of the show to update.
        :param library: The library path to set for the show.
        :raises NotFoundError: If the show with the given ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            show = await self.db.get(Show, show_id)
            if not show:
                msg = f"Show with id {show_id} not found."
                raise NotFoundError(msg)
            show.library = library
            await self.db.commit()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception("Database error setting library for show %s", show_id)
            raise

    async def get_episode_files_by_season_id(
        self, season_id: SeasonId
    ) -> list[EpisodeFileSchema]:
        """
        Retrieve all episode files for a given season ID.

        :param season_id: The ID of the season.
        :return: A list of EpisodeFile objects.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(EpisodeFile).join(Episode).where(Episode.season_id == season_id)
            )
            results = (await self.db.execute(stmt)).scalars().all()
            return [EpisodeFileSchema.model_validate(ef) for ef in results]
        except SQLAlchemyError:
            log.exception(
                "Database error retrieving episode files for season_id %s",
                season_id,
            )
            raise

    async def get_episode_files_by_season_ids(
        self, season_ids: list[SeasonId]
    ) -> dict[SeasonId, list[EpisodeFileSchema]]:
        """Retrieve episode files for multiple seasons in one query."""
        if not season_ids:
            return {}
        try:
            stmt = (
                select(EpisodeFile, Episode.season_id)
                .join(Episode, Episode.id == EpisodeFile.episode_id)
                .where(Episode.season_id.in_(season_ids))
            )
            rows = (await self.db.execute(stmt)).all()
            grouped: dict[SeasonId, list[EpisodeFileSchema]] = {
                season_id: [] for season_id in season_ids
            }
            for episode_file, season_id in rows:
                grouped[SeasonId(season_id)].append(
                    EpisodeFileSchema.model_validate(episode_file)
                )
        except SQLAlchemyError:
            log.exception(
                "Database error retrieving episode files for season_ids %s",
                season_ids,
            )
            raise
        else:
            return grouped

    async def delete_episode_file(self, file_id: UUID) -> None:
        """Delete a specific episode file record from the database."""
        stmt = delete(EpisodeFile).where(EpisodeFile.id == file_id)
        await self.db.execute(stmt)
        await self.db.commit()

    async def get_episode_files_by_episode_id(
        self, episode_id: EpisodeId
    ) -> list[EpisodeFileSchema]:
        """
        Retrieve all episode files for a given episode ID.

        :param episode_id: The ID of the episode.
        :return: A list of EpisodeFile objects.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = select(EpisodeFile).where(EpisodeFile.episode_id == episode_id)
            results = (await self.db.execute(stmt)).scalars().all()
            return [EpisodeFileSchema.model_validate(sf) for sf in results]
        except SQLAlchemyError as e:
            log.error(
                "Database error retrieving episode files for episode_id %s: %s",
                episode_id,
                e,
            )
            raise

    async def get_torrents_by_show_id(self, show_id: ShowId) -> list[TorrentSchema]:
        """
        Retrieve all torrents associated with a given show ID.

        :param show_id: The ID of the show.
        :return: A list of Torrent objects.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Torrent)
                .distinct()
                .join(EpisodeFile, EpisodeFile.torrent_id == Torrent.id)
                .join(Episode, Episode.id == EpisodeFile.episode_id)
                .join(Season, Season.id == Episode.season_id)
                .where(Season.show_id == show_id)
            )
            results = (await self.db.execute(stmt)).scalars().unique().all()
            return [TorrentSchema.model_validate(torrent) for torrent in results]
        except SQLAlchemyError:
            log.exception("Database error retrieving torrents for show_id %s", show_id)
            raise

    async def get_seasons_by_torrent_id(
        self, torrent_id: TorrentId
    ) -> list[SeasonNumber]:
        """
        Retrieve season numbers associated with a given torrent ID.

        :param torrent_id: The ID of the torrent.
        :return: A list of SeasonNumber objects.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Season.number)
                .distinct()
                .join(Episode, Episode.season_id == Season.id)
                .join(EpisodeFile, EpisodeFile.episode_id == Episode.id)
                .where(EpisodeFile.torrent_id == torrent_id)
            )
            results = (await self.db.execute(stmt)).scalars().unique().all()
            return [SeasonNumber(x) for x in results]
        except SQLAlchemyError:
            log.exception(
                "Database error retrieving season numbers for torrent_id %s",
                torrent_id,
            )
            raise

    async def get_episodes_by_torrent_id(
        self, torrent_id: TorrentId
    ) -> list[EpisodeNumber]:
        """
        Retrieve episode numbers associated with a given torrent ID.

        :param torrent_id: The ID of the torrent.
        :return: A list of EpisodeNumber objects.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Episode.number)
                .join(EpisodeFile, EpisodeFile.episode_id == Episode.id)
                .where(EpisodeFile.torrent_id == torrent_id)
                .order_by(Episode.number)
            )

            episode_numbers = (await self.db.execute(stmt)).scalars().all()

            return [EpisodeNumber(n) for n in sorted(set(episode_numbers))]

        except SQLAlchemyError as e:
            log.error(
                "Database error retrieving episodes for torrent_id %s: %s",
                torrent_id,
                e,
            )
            raise

    async def get_show_by_season_id(self, season_id: SeasonId) -> ShowSchema:
        """
        Retrieve a show by one of its season's ID.

        :param season_id: The ID of the season to retrieve the show for.
        :return: A Show object.
        :raises NotFoundError: If the show for the given season ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Show)
                .join(Season, Show.id == Season.show_id)
                .where(Season.id == season_id)
                .options(*_full_show_eager_loads())
            )
            result = (await self.db.execute(stmt)).unique().scalar_one_or_none()
            if not result:
                msg = f"Show for season_id {season_id} not found."
                raise NotFoundError(msg)
            return ShowSchema.model_validate(result)
        except SQLAlchemyError:
            log.exception("Database error retrieving show by season_id %s", season_id)
            raise

    async def add_season_to_show(
        self, show_id: ShowId, season_data: SeasonSchema, *, skipped: bool = False
    ) -> SeasonSchema:
        """
        Adds a new season and its episodes to a show.
        If the season number already exists for the show, it returns the existing season.

        :param show_id: The ID of the show to add the season to.
        :param season_data: The SeasonSchema object for the new season.
        :param skipped: Whether the season and its episodes should be created as skipped.
        :return: The added or existing SeasonSchema object.
        :raises NotFoundError: If the show is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        db_show = await self.db.get(Show, show_id)
        if not db_show:
            msg = f"Show with id {show_id} not found."
            raise NotFoundError(msg)

        stmt = (
            select(Season)
            .where(Season.show_id == show_id)
            .where(Season.number == season_data.number)
            .options(
                selectinload(Season.episodes).selectinload(Episode.episode_files),
            )
        )
        existing_db_season = (await self.db.execute(stmt)).scalar_one_or_none()
        if existing_db_season:
            return SeasonSchema.model_validate(existing_db_season)

        db_season = Season(
            id=season_data.id,
            show_id=show_id,
            number=season_data.number,
            skipped=skipped,
            episodes=[
                Episode(
                    id=ep_schema.id,
                    number=ep_schema.number,
                    title=ep_schema.title,
                    air_date=ep_schema.air_date,
                    air_time=ep_schema.air_time,
                    skipped=skipped,
                )
                for ep_schema in season_data.episodes
            ],
        )

        self.db.add(db_season)
        await self.db.commit()
        # Re-fetch with eager loads
        stmt = (
            select(Season)
            .where(Season.id == db_season.id)
            .options(
                selectinload(Season.episodes).selectinload(Episode.episode_files),
            )
        )
        db_season = (await self.db.execute(stmt)).unique().scalar_one()
        return SeasonSchema.model_validate(db_season)

    async def add_episode_to_season(
        self, season_id: SeasonId, episode_data: EpisodeSchema, *, skipped: bool = False
    ) -> EpisodeSchema:
        """
        Adds a new episode to a season.
        If the episode number already exists for the season, it returns the existing episode.

        :param season_id: The ID of the season to add the episode to.
        :param episode_data: The EpisodeSchema object for the new episode.
        :param skipped: Whether the episode should be created as skipped.
        :return: The added or existing EpisodeSchema object.
        :raises NotFoundError: If the season is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        db_season = await self.db.get(Season, season_id)
        if not db_season:
            msg = f"Season with id {season_id} not found."
            raise NotFoundError(msg)

        stmt = (
            select(Episode)
            .where(Episode.season_id == season_id)
            .where(Episode.number == episode_data.number)
            .options(selectinload(Episode.episode_files))
        )
        existing_db_episode = (await self.db.execute(stmt)).scalar_one_or_none()
        if existing_db_episode:
            return EpisodeSchema.model_validate(existing_db_episode)

        db_episode = Episode(
            id=episode_data.id,
            season_id=season_id,
            number=episode_data.number,
            title=episode_data.title,
            air_date=episode_data.air_date,
            air_time=episode_data.air_time,
            skipped=skipped,
        )

        self.db.add(db_episode)
        await self.db.commit()
        stmt = (
            select(Episode)
            .where(Episode.id == db_episode.id)
            .options(selectinload(Episode.episode_files))
        )
        db_episode = (await self.db.execute(stmt)).unique().scalar_one()
        return EpisodeSchema.model_validate(db_episode)

    async def add_episodes_to_season(
        self,
        season_id: SeasonId,
        episodes: list[EpisodeSchema],
        *,
        skipped: bool = False,
    ) -> list[EpisodeSchema]:
        """Insert many episodes for a season in one transaction (single commit)."""
        db_season = await self.db.get(Season, season_id)
        if not db_season:
            msg = f"Season with id {season_id} not found."
            raise NotFoundError(msg)

        if not episodes:
            return []

        numbers = [episode.number for episode in episodes]
        stmt = select(Episode.number).where(
            Episode.season_id == season_id,
            Episode.number.in_(numbers),
        )
        existing_numbers = set((await self.db.execute(stmt)).scalars())
        new_models = [
            Episode(
                id=episode.id,
                season_id=season_id,
                number=episode.number,
                title=episode.title,
                air_date=episode.air_date,
                air_time=episode.air_time,
                skipped=skipped,
            )
            for episode in episodes
            if episode.number not in existing_numbers
        ]
        if new_models:
            self.db.add_all(new_models)
            await self.db.commit()
        return [EpisodeSchema.model_validate(model) for model in new_models]

    async def update_show_skipped(self, show_id: ShowId, skipped: bool) -> None:
        db_show = await self.db.get(Show, show_id)
        if not db_show:
            msg = f"Show with id {show_id} not found."
            raise NotFoundError(msg)
        db_show.skipped = skipped
        await self.db.flush()

    async def update_episode_skipped(
        self, episode_id: EpisodeId, skipped: bool
    ) -> None:
        db_episode = await self.db.get(Episode, episode_id)
        if not db_episode:
            msg = f"Episode with id {episode_id} not found."
            raise NotFoundError(msg)
        db_episode.skipped = skipped
        await self.db.flush()

    async def update_episodes_skipped_bulk(
        self, episode_ids: list[EpisodeId], skipped: bool
    ) -> None:
        if not episode_ids:
            return
        try:
            stmt = (
                update(Episode)
                .where(Episode.id.in_(episode_ids))
                .values(skipped=skipped)
            )
            await self.db.execute(stmt)
            await self.db.flush()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(
                "Failed to bulk-update skipped for %d episodes", len(episode_ids)
            )
            raise

    async def update_season_skipped(self, season_id: SeasonId, skipped: bool) -> None:
        db_season = await self.db.get(Season, season_id)
        if not db_season:
            msg = f"Season with id {season_id} not found."
            raise NotFoundError(msg)
        db_season.skipped = skipped
        await self.db.flush()

    async def get_show_auto_download_candidate_flags(
        self,
    ) -> list[tuple[ShowId, bool, bool | None]]:
        """Scalar (id, skipped, continuous_download) rows for sweep candidate selection."""
        stmt = select(Show.id, Show.skipped, Show.continuous_download)
        rows = (await self.db.execute(stmt)).all()
        return [(ShowId(row[0]), row[1], row[2]) for row in rows]

    async def get_show_ids_due_for_metadata(
        self, *, older_than: datetime, limit: int = 200
    ) -> list[ShowId]:
        """Show PKs whose metadata refresh is due (SQL-filtered, bounded)."""
        from datetime import UTC, datetime

        from sqlalchemy import or_

        now = datetime.now(UTC)
        stmt = (
            select(Show.id)
            .where(
                or_(
                    Show.last_metadata_check.is_(None),
                    Show.last_metadata_check < older_than,
                ),
                or_(
                    Show.metadata_failure_backoff_until.is_(None),
                    Show.metadata_failure_backoff_until <= now,
                ),
            )
            .order_by(Show.last_metadata_check.asc().nulls_first())
            .limit(limit)
        )
        return typing_cast(
            "list[ShowId]", list((await self.db.execute(stmt)).scalars().all())
        )

    async def stamp_metadata_check(self, show_id: ShowId) -> None:
        from datetime import UTC, datetime

        db_show = await self.db.get(Show, show_id)
        if db_show:
            db_show.last_metadata_check = datetime.now(UTC)
            db_show.metadata_failure_backoff_until = None
            await self.db.flush()

    async def mark_metadata_failure(
        self, show_id: ShowId, backoff_until: datetime
    ) -> None:
        from datetime import UTC, datetime

        db_show = await self.db.get(Show, show_id)
        if db_show:
            db_show.last_metadata_check = datetime.now(UTC)
            db_show.metadata_failure_backoff_until = backoff_until
            await self.db.flush()

    async def set_auto_download_backoff(self, show_id: ShowId, until: datetime) -> None:
        db_show = await self.db.get(Show, show_id)
        if db_show:
            db_show.auto_download_backoff_until = until
            await self.db.flush()

    async def update_show_attributes(
        self,
        show_id: ShowId,
        name: str | None = None,
        overview: str | None = None,
        year: int | None = None,
        ended: bool | None = None,
        continuous_download: bool | None = ...,
        external_id: str | None = None,
        imdb_id: str | None = None,
        preferred_quality: list[str] | None = ...,
        preferred_codec: list[str] | None = ...,
        subtitle_languages: list[str] | None = ...,
        vote_average: float | None = ...,
        content_rating: str | None = ...,
        genres: list[str] | None = ...,
        cast: list[str] | None = ...,
    ) -> tuple[ShowSchema, bool]:
        """
        Update attributes of an existing show.

        Returns a tuple of (updated show, whether any fields changed).
        """
        # Fetch with eager loads so we can return a fully-validated schema.
        stmt = select(Show).where(Show.id == show_id).options(*_full_show_eager_loads())
        db_show = (await self.db.execute(stmt)).unique().scalar_one_or_none()
        if not db_show:
            msg = f"Show with id {show_id} not found."
            raise NotFoundError(msg)

        updated = False

        def _lists_equal(a: list | None, b: list | None) -> bool:
            if a is None and b is None:
                return True
            if a is None or b is None:
                return False
            return sorted(a) == sorted(b)

        def _floats_equal(a: float | None, b: float | None) -> bool:
            if a is None and b is None:
                return True
            if a is None or b is None:
                return False
            return round(a, 2) == round(b, 2)

        if name is not None and db_show.name != name:
            db_show.name = name
            updated = True
        if overview is not None and db_show.overview != overview:
            db_show.overview = overview
            updated = True
        if year is not None and db_show.year != year:
            db_show.year = year
            updated = True
        if ended is not None and db_show.ended != ended:
            db_show.ended = ended
            updated = True
        if (
            continuous_download is not ...
            and db_show.continuous_download != continuous_download
        ):
            db_show.continuous_download = continuous_download
            updated = True
        if external_id is not None and db_show.external_id != external_id:
            db_show.external_id = external_id
            updated = True
        if imdb_id is not None and db_show.imdb_id != imdb_id:
            db_show.imdb_id = imdb_id
            updated = True
        if preferred_quality is not ... and not _lists_equal(
            db_show.preferred_quality, preferred_quality
        ):
            db_show.preferred_quality = preferred_quality
            updated = True
        if preferred_codec is not ... and not _lists_equal(
            db_show.preferred_codec, preferred_codec
        ):
            db_show.preferred_codec = preferred_codec
            updated = True
        if subtitle_languages is not ... and not _lists_equal(
            db_show.subtitle_languages, subtitle_languages
        ):
            db_show.subtitle_languages = subtitle_languages
            updated = True
        if vote_average is not ... and not _floats_equal(
            db_show.vote_average, vote_average
        ):
            db_show.vote_average = vote_average
            updated = True
        if content_rating is not ... and db_show.content_rating != content_rating:
            db_show.content_rating = content_rating
            updated = True
        if genres is not ... and not _lists_equal(db_show.genres, genres):
            db_show.genres = genres
            updated = True
        if cast is not ... and not _lists_equal(db_show.cast, cast):
            db_show.cast = cast
            updated = True
        if updated:
            await self.db.flush()
            # Re-fetch fully eager-loaded for validation.
            stmt = (
                select(Show)
                .where(Show.id == show_id)
                .options(*_full_show_eager_loads())
            )
            db_show = (await self.db.execute(stmt)).unique().scalar_one()
        return ShowSchema.model_validate(db_show), updated

    async def update_episodes_attributes_bulk(
        self, changes: Sequence[EpisodeAttributeChange]
    ) -> None:
        """Apply metadata attribute updates for many episodes in one round-trip."""
        if not changes:
            return
        episode_ids = [change.episode_id for change in changes]
        try:
            stmt = select(Episode).where(Episode.id.in_(episode_ids))
            db_episodes = list((await self.db.execute(stmt)).scalars().all())
            by_id = {episode.id: episode for episode in db_episodes}
            for episode_id in episode_ids:
                if episode_id not in by_id:
                    msg = f"Episode with id {episode_id} not found."
                    raise NotFoundError(msg)

            updated = False
            for change in changes:
                db_episode = by_id[change.episode_id]
                if change.title is not None and db_episode.title != change.title:
                    db_episode.title = change.title
                    updated = True
                if (
                    change.overview is not None
                    and db_episode.overview != change.overview
                ):
                    db_episode.overview = change.overview
                    updated = True
                if (
                    change.air_date is not None
                    and db_episode.air_date != change.air_date
                ):
                    db_episode.air_date = change.air_date
                    updated = True
                if (
                    change.air_time is not None
                    and db_episode.air_time != change.air_time
                ):
                    db_episode.air_time = change.air_time
                    updated = True

            if updated:
                await self.db.flush()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(
                "Failed to bulk-update attributes for %d episodes", len(changes)
            )
            raise

    async def update_episode_attributes(
        self,
        episode_id: EpisodeId,
        title: str | None = None,
        overview: str | None = None,
        air_date: date | None = None,
        air_time: time | None = None,
    ) -> EpisodeSchema:
        """Update attributes of an existing episode.

        ``air_date`` / ``air_time`` take ``... | None``; passing ``None`` here
        means "no change" (we don't clear these via this path). The sentinel is
        reserved by comparing against the actual current value during the
        metadata refresh path.
        """
        stmt = (
            select(Episode)
            .where(Episode.id == episode_id)
            .options(selectinload(Episode.episode_files))
        )
        db_episode = (await self.db.execute(stmt)).unique().scalar_one_or_none()
        if not db_episode:
            msg = f"Episode with id {episode_id} not found."
            raise NotFoundError(msg)

        updated = False
        if title is not None and db_episode.title != title:
            db_episode.title = title
            updated = True
        if overview is not None and db_episode.overview != overview:
            db_episode.overview = overview
            updated = True
        if air_date is not None and db_episode.air_date != air_date:
            db_episode.air_date = air_date
            updated = True
        if air_time is not None and db_episode.air_time != air_time:
            db_episode.air_time = air_time
            updated = True

        if updated:
            await self.db.flush()
            stmt = (
                select(Episode)
                .where(Episode.id == episode_id)
                .options(
                    selectinload(Episode.episode_files),
                    selectinload(Episode.season).selectinload(Season.show),
                )
            )
            db_episode = (await self.db.execute(stmt)).unique().scalar_one()
            log.debug(
                "Updated episode S%02dE%02d for show %s",
                db_episode.season.number,
                db_episode.number,
                db_episode.season.show.name,
            )
        return EpisodeSchema.model_validate(db_episode)
