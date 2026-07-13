import logging
from datetime import date, datetime
from typing import cast as typing_cast
from uuid import UUID

from sqlalchemy import delete, func, or_, select, tuple_, update
from sqlalchemy.exc import (
    IntegrityError,
    SQLAlchemyError,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from sqlalchemy.sql.base import ExecutableOption

from miramedia.exceptions import ConflictError, NotFoundError
from miramedia.file_status import ImportOutcome
from miramedia.media_filters import apply_list_filters, apply_sort
from miramedia.media_status import MediaStatus
from miramedia.movies.models import Movie, MovieFile
from miramedia.movies.schemas import (
    Movie as MovieSchema,
)
from miramedia.movies.schemas import (
    MovieFile as MovieFileSchema,
)
from miramedia.movies.schemas import (
    MovieId,
)
from miramedia.torrents.integrity import (
    integrity_audit_snapshot_where,
    integrity_mismatch_action_snapshot_where,
)
from miramedia.torrents.models import Torrent
from miramedia.torrents.schemas import Quality, TorrentId
from miramedia.torrents.schemas import Torrent as TorrentSchema

log = logging.getLogger(__name__)


def _movie_summary_eager_loads() -> tuple[ExecutableOption, ...]:
    """Minimal eager-loads for list/search previews.

    Movies have no nested relationships that get auto-loaded on
    ``model_validate`` (unlike ``Show.seasons``), so the helper returning
    an empty tuple matches the default. It exists for parity with
    :func:`miramedia.shows.repository._show_summary_eager_loads` and so
    new movie code can opt in explicitly when fetching summary rows.
    """
    return ()


def _apply_movie_list_filters(
    stmt: Select[tuple[object, ...]],
    *,
    query: str | None = None,
    libraries: list[str] | None = None,
    excluded_libraries: list[str] | None = None,
    genres: list[str] | None = None,
    excluded_genres: list[str] | None = None,
    decades: list[int] | None = None,
    excluded_decades: list[int] | None = None,
) -> Select[tuple[object, ...]]:
    """Apply server-side filters shared by list + count endpoints."""
    return apply_list_filters(
        stmt,
        name_col=Movie.name,
        library_col=Movie.library,
        genres_col=Movie.genres,
        year_col=Movie.year,
        query=query,
        libraries=libraries,
        excluded_libraries=excluded_libraries,
        genres=genres,
        excluded_genres=excluded_genres,
        decades=decades,
        excluded_decades=excluded_decades,
    )


def _apply_movie_status_filters(
    stmt: Select[tuple[object, ...]],
    *,
    statuses: list[str] | None = None,
    excluded_statuses: list[str] | None = None,
) -> Select[tuple[object, ...]]:
    if statuses:
        wanted = MediaStatus.downloaded in statuses
        not_wanted = "not_downloaded" in statuses
        if wanted and not not_wanted:
            stmt = stmt.where(Movie.downloaded.is_(True))
        elif not_wanted and not wanted:
            stmt = stmt.where(Movie.downloaded.is_(False))
    if excluded_statuses:
        if MediaStatus.downloaded in excluded_statuses:
            stmt = stmt.where(Movie.downloaded.is_(False))
        if "not_downloaded" in excluded_statuses:
            stmt = stmt.where(Movie.downloaded.is_(True))
    return stmt


def _apply_movie_sort(
    stmt: Select[tuple[object, ...]], sort: str | None
) -> Select[tuple[object, ...]]:
    return apply_sort(
        stmt,
        sort,
        name_col=Movie.name,
        year_col=Movie.year,
        rating_col=Movie.vote_average,
    )


class MovieRepository:
    """
    Repository for managing movies in the database.
    Provides methods to retrieve, save, and delete movies.
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_movie_by_id(self, movie_id: MovieId) -> MovieSchema:
        """
        Retrieve a movie by its ID.

        :param movie_id: The ID of the movie to retrieve.
        :return: A Movie object if found.
        :raises NotFoundError: If the movie with the given ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = select(Movie).where(Movie.id == movie_id)
            result = (await self.db.execute(stmt)).unique().scalar_one_or_none()
            if not result:
                msg = f"Movie with id {movie_id} not found."
                raise NotFoundError(msg)
            return MovieSchema.model_validate(result)
        except SQLAlchemyError:
            log.exception(f"Database error while retrieving movie {movie_id}")
            raise

    async def get_movie_by_external_id(
        self, external_id: str, metadata_provider: str
    ) -> MovieSchema:
        """Retrieve a movie by its metadata provider ID."""
        try:
            stmt = (
                select(Movie)
                .where(Movie.external_id == external_id)
                .where(Movie.metadata_provider == metadata_provider)
            )
            result = (await self.db.execute(stmt)).unique().scalar_one_or_none()
            if not result:
                msg = f"Movie with external_id {external_id} and metadata_provider {metadata_provider} not found."
                raise NotFoundError(msg)
            return MovieSchema.model_validate(result)
        except SQLAlchemyError:
            log.exception(
                f"Database error while retrieving movie by external_id {external_id}"
            )
            raise

    async def movie_exists_by_imdb_id(self, imdb_id: str) -> MovieSchema | None:
        """Check if a movie exists by imdb_id."""
        try:
            stmt = select(Movie).where(Movie.imdb_id == imdb_id)
            result = (await self.db.execute(stmt)).scalars().first()
        except SQLAlchemyError:
            log.exception(f"Error checking movie existence for imdb_id {imdb_id}")
            return None
        else:
            if result:
                return MovieSchema.model_validate(result)
            return None

    async def movies_existing_by_identifiers(
        self,
        imdb_ids: list[str],
        provider_keys: list[tuple[str, str]],
    ) -> list[tuple[str | None, str, str, MovieId]]:
        """Bulk lookup of movies matching search results, mirroring the scan's
        ``_resolve_existing`` three-way match: a row counts as existing when
        EITHER ``imdb_id`` OR ``external_id`` equals a result's IMDb id, OR
        ``(external_id, metadata_provider)`` equals a result's provider key.

        Native-provider movies store the IMDb id in ``external_id`` and leave the
        ``imdb_id`` column NULL (the folder is still tagged ``[imdb-tt...]``), so
        an imdb_id-only lookup wrongly flags them as not-added ("Add" vs "View").
        Returns ``(imdb_id, external_id, metadata_provider, id)`` rows so the
        caller can build the lookup maps; projects scalar columns only.
        """
        if not imdb_ids and not provider_keys:
            return []
        conditions = []
        if imdb_ids:
            conditions.append(Movie.imdb_id.in_(imdb_ids))
            conditions.append(Movie.external_id.in_(imdb_ids))
        if provider_keys:
            conditions.append(
                tuple_(Movie.external_id, Movie.metadata_provider).in_(provider_keys)
            )
        try:
            stmt = select(
                Movie.imdb_id, Movie.external_id, Movie.metadata_provider, Movie.id
            ).where(or_(*conditions))
            rows = (await self.db.execute(stmt)).all()
            return [
                (imdb_id, ext, prov, movie_id) for imdb_id, ext, prov, movie_id in rows
            ]
        except SQLAlchemyError:
            log.exception("Bulk movie lookup by identifiers failed")
            return []

    async def native_imdb_index(self) -> dict[str, MovieId]:
        """Map IMDb id -> movie id for native-provider (IMDb-keyed) rows.

        Bridges "Add vs View" for native/scan-imported movies that a TMDB/TVDB
        search result (no imdb_id) can't match by provider key. Empty for
        pure-TMDB libraries, so the enrichment pass is skipped there.
        """
        try:
            stmt = select(Movie.external_id, Movie.id).where(
                Movie.metadata_provider == "native"
            )
            rows = (await self.db.execute(stmt)).all()
            return {ext: mid for ext, mid in rows if ext}
        except SQLAlchemyError:
            log.exception("Native IMDb index lookup failed")
            return {}

    async def get_movies(self) -> list[MovieSchema]:
        """
        Retrieve all movies from the database.

        :return: A list of Movie objects.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = select(Movie)
            results = (await self.db.execute(stmt)).scalars().unique().all()
            return [MovieSchema.model_validate(movie) for movie in results]
        except SQLAlchemyError:
            log.exception("Database error while retrieving all movies")
            raise

    async def get_movie_ids(self) -> list[MovieId]:
        """Return all movie primary keys without loading full rows."""
        try:
            return typing_cast(
                "list[MovieId]",
                list((await self.db.execute(select(Movie.id))).scalars().all()),
            )
        except SQLAlchemyError:
            log.exception("Database error while retrieving all movie ids")
            raise

    async def get_movie_auto_download_candidate_flags(
        self,
    ) -> list[tuple[MovieId, bool, bool | None]]:
        """Scalar (id, skipped, continuous_download) rows for sweep candidate selection."""
        stmt = select(Movie.id, Movie.skipped, Movie.continuous_download)
        rows = (await self.db.execute(stmt)).all()
        return [(MovieId(row[0]), row[1], row[2]) for row in rows]

    async def get_movies_paginated(
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
        statuses: list[str] | None = None,
        excluded_statuses: list[str] | None = None,
    ) -> tuple[list[MovieSchema], int]:
        """Paginated variant of :meth:`get_movies` for list endpoints."""
        try:
            count_stmt = _apply_movie_status_filters(
                _apply_movie_list_filters(
                    select(func.count()).select_from(Movie),
                    query=query,
                    libraries=libraries,
                    excluded_libraries=excluded_libraries,
                    genres=genres,
                    excluded_genres=excluded_genres,
                    decades=decades,
                    excluded_decades=excluded_decades,
                ),
                statuses=statuses,
                excluded_statuses=excluded_statuses,
            )
            stmt = _apply_movie_sort(
                _apply_movie_status_filters(
                    _apply_movie_list_filters(
                        select(Movie),
                        query=query,
                        libraries=libraries,
                        excluded_libraries=excluded_libraries,
                        genres=genres,
                        excluded_genres=excluded_genres,
                        decades=decades,
                        excluded_decades=excluded_decades,
                    ),
                    statuses=statuses,
                    excluded_statuses=excluded_statuses,
                ),
                sort,
            )
            stmt = stmt.offset(offset).limit(limit)
            total = (await self.db.scalar(count_stmt)) or 0
            rows = (await self.db.execute(stmt)).scalars().unique().all()
            return [MovieSchema.model_validate(m) for m in rows], int(total)
        except SQLAlchemyError:
            log.exception("Database error while paginating movies")
            raise

    async def count_movies_filtered(
        self,
        *,
        query: str | None = None,
        libraries: list[str] | None = None,
        excluded_libraries: list[str] | None = None,
        genres: list[str] | None = None,
        excluded_genres: list[str] | None = None,
        decades: list[int] | None = None,
        excluded_decades: list[int] | None = None,
        statuses: list[str] | None = None,
        excluded_statuses: list[str] | None = None,
    ) -> int:
        try:
            stmt = _apply_movie_status_filters(
                _apply_movie_list_filters(
                    select(func.count()).select_from(Movie),
                    query=query,
                    libraries=libraries,
                    excluded_libraries=excluded_libraries,
                    genres=genres,
                    excluded_genres=excluded_genres,
                    decades=decades,
                    excluded_decades=excluded_decades,
                ),
                statuses=statuses,
                excluded_statuses=excluded_statuses,
            )
            return int((await self.db.scalar(stmt)) or 0)
        except SQLAlchemyError:
            log.exception("Database error while counting movies")
            raise

    async def get_movie_facets(self) -> dict[str, list]:
        """Return filter option values for movie grids without loading files."""
        try:
            rows = (
                await self.db.execute(select(Movie.library, Movie.genres, Movie.year))
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
            log.exception("Database error while loading movie facets")
            raise

    async def save_movie(self, movie: MovieSchema) -> MovieSchema:
        """
        Save a new movie or update an existing one in the database.

        :param movie: The Movie object to save.
        :return: The saved Movie object.
        :raises ValueError: If a movie with the same primary key already exists (on insert).
        :raises SQLAlchemyError: If a database error occurs.
        """
        # Native/Cinemeta media are keyed on the IMDb id (external_id == 'tt...').
        # Mirror it into imdb_id when the provider didn't populate the column so
        # IMDb-keyed rows always carry it — keeps "Add vs View" matching and
        # cross-provider dedup working without relying on external_id fallbacks.
        if not movie.imdb_id and movie.external_id.startswith("tt"):
            movie.imdb_id = movie.external_id
        log.debug(f"Attempting to save movie: {movie.name} (ID: {movie.id})")
        db_movie = await self.db.get(Movie, movie.id) if movie.id else None

        if db_movie:  # Update existing movie
            log.debug(f"Updating existing movie with ID: {movie.id}")
            db_movie.external_id = movie.external_id
            db_movie.metadata_provider = movie.metadata_provider
            db_movie.name = movie.name
            db_movie.overview = movie.overview
            db_movie.year = movie.year
            db_movie.release_date = movie.release_date
            db_movie.original_language = movie.original_language
            db_movie.imdb_id = movie.imdb_id
        else:  # Insert new movie
            log.debug(f"Creating new movie: {movie.name}")
            db_movie = Movie(**movie.model_dump())
            self.db.add(db_movie)

        try:
            await self.db.commit()
            await self.db.refresh(db_movie)
            log.info(f"Successfully saved movie: {db_movie.name} (ID: {db_movie.id})")
            return MovieSchema.model_validate(db_movie)
        except IntegrityError as e:
            await self.db.rollback()
            log.exception(f"Integrity error while saving movie {movie.name}")
            msg = (
                f"Movie with this primary key or unique constraint violation: {e.orig}"
            )
            raise ConflictError(msg) from e
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(f"Database error while saving movie {movie.name}")
            raise

    async def delete_movie(self, movie_id: MovieId) -> None:
        """
        Delete a movie by its ID.

        :param movie_id: The ID of the movie to delete.
        :raises NotFoundError: If the movie with the given ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        log.debug(f"Attempting to delete movie with id: {movie_id}")
        try:
            movie = await self.db.get(Movie, movie_id)
            if not movie:
                log.warning(f"Movie with id {movie_id} not found for deletion.")
                msg = f"Movie with id {movie_id} not found."
                raise NotFoundError(msg)
            await self.db.delete(movie)
            await self.db.commit()
            log.info(f"Successfully deleted movie with id: {movie_id}")
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(f"Database error while deleting movie {movie_id}")
            raise

    async def set_movie_library(self, movie_id: MovieId, library: str) -> None:
        """
        Sets the library for a movie.

        :param movie_id: The ID of the movie to update.
        :param library: The library path to set for the movie.
        :raises NotFoundError: If the movie with the given ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            movie = await self.db.get(Movie, movie_id)
            if not movie:
                msg = f"movie with id {movie_id} not found."
                raise NotFoundError(msg)
            movie.library = library
            await self.db.commit()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(f"Database error setting library for movie {movie_id}")
            raise

    async def update_movie_skipped(self, movie_id: MovieId, skipped: bool) -> None:
        try:
            movie = await self.db.get(Movie, movie_id)
            if not movie:
                msg = f"movie with id {movie_id} not found."
                raise NotFoundError(msg)
            movie.skipped = skipped
            await self.db.flush()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(f"Database error setting skipped for movie {movie_id}")
            raise

    async def add_movie_file(self, movie_file: MovieFileSchema) -> MovieFileSchema:
        """
        Adds a movie file record to the database.

        :param movie_file: The MovieFile object to add.
        :return: The added MovieFile object.
        :raises IntegrityError: If the record violates constraints.
        :raises SQLAlchemyError: If a database error occurs.
        """
        db_model = MovieFile(**movie_file.model_dump())
        try:
            self.db.add(db_model)
            await self.db.commit()
            await self.db.refresh(db_model)
            return MovieFileSchema.model_validate(db_model)
        except IntegrityError:
            await self.db.rollback()
            log.exception("Integrity error while adding movie file")
            raise
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception("Database error while adding movie file")
            raise

    async def get_movie_file_by_id(self, file_id: UUID) -> MovieFileSchema | None:
        """Retrieve a single movie file row by its surrogate id."""
        try:
            stmt = select(MovieFile).where(MovieFile.id == file_id)
            result = (await self.db.execute(stmt)).scalar_one_or_none()
            if result is None:
                return None
            return MovieFileSchema.model_validate(result)
        except SQLAlchemyError:
            log.exception("Database error retrieving movie_file %s", file_id)
            raise

    async def finalize_movie_file_import(
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
        """Persist all naming components + quality + import outcome for a row.

        Sets the detected naming columns, the (possibly re-detected) quality,
        the import status, stamps ``last_attempt_at``/``attempt_count``, and
        ``imported_at`` when the outcome is ``imported``.
        """
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        try:
            stmt = (
                update(MovieFile)
                .where(MovieFile.id == file_id)
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
                    attempt_count=MovieFile.attempt_count + 1,
                    imported_at=(
                        now
                        if status == ImportOutcome.imported
                        else MovieFile.imported_at
                    ),
                )
            )
            await self.db.execute(stmt)
            await self.db.flush()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception("Failed to finalize import for movie_file %s", file_id)
            raise

    async def update_movie_file_import_status(
        self,
        *,
        file_id: UUID,
        status: ImportOutcome,
        error: str | None = None,
    ) -> None:
        """Persist a new import outcome for the given movie file row."""
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        try:
            stmt = (
                update(MovieFile)
                .where(MovieFile.id == file_id)
                .values(
                    import_status=status,
                    import_error=error,
                    last_attempt_at=now,
                    attempt_count=MovieFile.attempt_count + 1,
                    imported_at=(
                        now
                        if status == ImportOutcome.imported
                        else MovieFile.imported_at
                    ),
                )
            )
            await self.db.execute(stmt)
            await self.db.flush()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception("Failed to update import status for movie_file %s", file_id)
            raise

    async def get_orphaned_failed_movie_files(self) -> list[MovieFileSchema]:
        """Movie files stuck ``failed_*`` with no torrent left to surface them.

        See :meth:`ShowRepository.get_orphaned_failed_episode_files` — same
        "ghost" failure: a detached row (FK ``ON DELETE SET NULL``) the imports
        page can never show, yet the dashboard file-count badge still counts.
        """
        stmt = select(MovieFile).where(
            MovieFile.torrent_id.is_(None),
            MovieFile.import_status.in_(
                (ImportOutcome.failed_io, ImportOutcome.failed_no_match)
            ),
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return [MovieFileSchema.model_validate(r) for r in rows]

    async def set_movie_file_sha1(
        self,
        *,
        file_id: UUID,
        sha1: str | None,
    ) -> None:
        """Persist (or clear) the integrity-audit SHA1 for a movie file."""
        try:
            stmt = update(MovieFile).where(MovieFile.id == file_id).values(sha1=sha1)
            await self.db.execute(stmt)
            await self.db.flush()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception("Failed to set sha1 for movie_file %s", file_id)
            raise

    async def count_sha1_mismatch_files(self) -> int:
        """Count imported movie files with a SHA1 mismatch stamp."""
        stmt = (
            select(func.count())
            .select_from(MovieFile)
            .where(
                MovieFile.import_status == ImportOutcome.imported,
                MovieFile.import_error.like("sha1 mismatch%"),
            )
        )
        return int((await self.db.execute(stmt)).scalar_one())

    async def list_sha1_mismatch_files(
        self, *, offset: int = 0, limit: int
    ) -> list[MovieFileSchema]:
        """Imported movie files whose integrity audit recorded a SHA1 mismatch.

        Contract: ``import_error`` prefix ``sha1 mismatch%`` must stay in sync
        with ``verify_imported_files_task`` in ``miramedia/scheduler.py``.
        MovieFile has no ORM ``movie`` relationship; title is resolved by the
        service via ``movie_id``. Rows are ordered by ``id`` ascending.
        """
        stmt = (
            select(MovieFile)
            .where(
                MovieFile.import_status == ImportOutcome.imported,
                MovieFile.import_error.like("sha1 mismatch%"),
            )
            .order_by(MovieFile.id)
            .offset(offset)
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return [MovieFileSchema.model_validate(r) for r in rows]

    async def get_sha1_mismatch_movie_files_by_ids(
        self, file_ids: list[UUID]
    ) -> dict[UUID, MovieFileSchema]:
        """Batch-load mismatch movie files still matching the list predicate."""
        if not file_ids:
            return {}
        stmt = select(MovieFile).where(
            MovieFile.id.in_(file_ids),
            MovieFile.import_status == ImportOutcome.imported,
            MovieFile.import_error.like("sha1 mismatch%"),
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        return {row.id: MovieFileSchema.model_validate(row) for row in rows}

    async def get_movies_by_ids(
        self, movie_ids: list[MovieId]
    ) -> dict[MovieId, MovieSchema]:
        """Batch-load movies by primary key for integrity path resolution."""
        if not movie_ids:
            return {}
        stmt = select(Movie).where(Movie.id.in_(movie_ids))
        rows = (await self.db.execute(stmt)).scalars().all()
        return {MovieId(row.id): MovieSchema.model_validate(row) for row in rows}

    async def get_movie_names_by_ids(
        self, movie_ids: list[MovieId]
    ) -> dict[MovieId, str]:
        """Batch-load movie titles for integrity-mismatch listing."""
        if not movie_ids:
            return {}
        stmt = select(Movie.id, Movie.name).where(Movie.id.in_(movie_ids))
        rows = (await self.db.execute(stmt)).all()
        return {MovieId(movie_id): name for movie_id, name in rows}

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
                update(MovieFile)
                .where(
                    integrity_audit_snapshot_where(
                        MovieFile,
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
                "Failed to baseline integrity sha1 for movie_file %s", file_id
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
                update(MovieFile)
                .where(
                    integrity_audit_snapshot_where(
                        MovieFile,
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
                "Failed to stamp integrity mismatch for movie_file %s", file_id
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
                update(MovieFile)
                .where(
                    integrity_mismatch_action_snapshot_where(
                        MovieFile,
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
            log.exception("Failed to clear integrity state for movie_file %s", file_id)
            raise

    async def remove_movie_files_by_torrent_id(self, torrent_id: TorrentId) -> int:
        """
        Removes movie file records associated with a given torrent ID.

        :param torrent_id: The ID of the torrent whose movie files are to be removed.
        :return: The number of movie files removed.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = delete(MovieFile).where(MovieFile.torrent_id == torrent_id)
            result = await self.db.execute(stmt)
            await self.db.commit()
        except SQLAlchemyError:
            await self.db.rollback()
            log.exception(
                f"Database error removing movie files for torrent_id {torrent_id}"
            )
            raise

        return result.rowcount

    async def delete_movie_file(self, file_id: UUID) -> None:
        """Delete a specific movie file record from the database."""
        stmt = delete(MovieFile).where(MovieFile.id == file_id)
        await self.db.execute(stmt)
        await self.db.commit()

    async def get_movie_files_by_movie_id(
        self, movie_id: MovieId
    ) -> list[MovieFileSchema]:
        """
        Retrieve all movie files for a given movie ID.

        :param movie_id: The ID of the movie.
        :return: A list of MovieFile objects.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = select(MovieFile).where(MovieFile.movie_id == movie_id)
            results = (await self.db.execute(stmt)).scalars().all()
            return [MovieFileSchema.model_validate(sf) for sf in results]
        except SQLAlchemyError:
            log.exception(
                f"Database error retrieving movie files for movie_id {movie_id}"
            )
            raise

    async def get_torrents_by_movie_id(self, movie_id: MovieId) -> list[TorrentSchema]:
        """
        Retrieve all torrents associated with a given movie ID.

        :param movie_id: The ID of the movie.
        :return: A list of Torrent objects.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Torrent)
                .distinct()
                .join(MovieFile, MovieFile.torrent_id == Torrent.id)
                .where(MovieFile.movie_id == movie_id)
            )
            results = (await self.db.execute(stmt)).scalars().unique().all()
            return [TorrentSchema.model_validate(t) for t in results]
        except SQLAlchemyError:
            log.exception(f"Database error retrieving torrents for movie_id {movie_id}")
            raise

    async def get_movie_files_for_movies(
        self, movie_ids: list[MovieId]
    ) -> dict[MovieId, list[MovieFileSchema]]:
        """Bulk: load movie_files for many movies in one query.

        Lets list-view callers avoid N+1 round-trips through
        ``get_movie_files_by_movie_id``.
        """
        if not movie_ids:
            return {}
        try:
            stmt = select(MovieFile).where(MovieFile.movie_id.in_(movie_ids))
            results = (await self.db.execute(stmt)).scalars().all()
            grouped: dict[MovieId, list[MovieFileSchema]] = {
                mid: [] for mid in movie_ids
            }
            for mf in results:
                grouped.setdefault(mf.movie_id, []).append(
                    MovieFileSchema.model_validate(mf)
                )
        except SQLAlchemyError:
            log.exception("Bulk movie_files lookup failed")
            raise
        else:
            return grouped

    async def get_torrents_for_movies(
        self, movie_ids: list[MovieId]
    ) -> dict[MovieId, list[tuple[TorrentSchema, str]]]:
        """Bulk: load (torrent, variant) per movie in one query.

        First variant encountered for a (movie, torrent) pair wins, matching
        the per-movie behaviour in ``get_torrents_for_movie`` where
        ``movie_files[0].variant`` is used.
        """
        if not movie_ids:
            return {}
        try:
            stmt = (
                select(Torrent, MovieFile.movie_id, MovieFile.variant)
                .join(MovieFile, MovieFile.torrent_id == Torrent.id)
                .where(MovieFile.movie_id.in_(movie_ids))
            )
            rows = (await self.db.execute(stmt)).all()
            grouped: dict[MovieId, list[tuple[TorrentSchema, str]]] = {
                mid: [] for mid in movie_ids
            }
            # Dedupe: same torrent can appear via multiple movie_files; keep first.
            seen: dict[tuple[MovieId, TorrentId], bool] = {}
            for torrent, movie_id, variant in rows:
                key = (movie_id, torrent.id)
                if key in seen:
                    continue
                seen[key] = True
                grouped.setdefault(movie_id, []).append(
                    (TorrentSchema.model_validate(torrent), variant or "")
                )
        except SQLAlchemyError:
            log.exception("Bulk torrents-by-movie lookup failed")
            raise
        else:
            return grouped

    async def get_movie_by_torrent_id(self, torrent_id: TorrentId) -> MovieSchema:
        """
        Retrieve a movie by a torrent ID.

        :param torrent_id: The ID of the torrent to retrieve the movie for.
        :return: A Movie object.
        :raises NotFoundError: If the movie for the given torrent ID is not found.
        :raises SQLAlchemyError: If a database error occurs.
        """
        try:
            stmt = (
                select(Movie)
                .join(MovieFile, Movie.id == MovieFile.movie_id)
                .where(MovieFile.torrent_id == torrent_id)
            )
            result = (await self.db.execute(stmt)).unique().scalar_one_or_none()
            if not result:
                msg = f"Movie for torrent_id {torrent_id} not found."
                raise NotFoundError(msg)
            return MovieSchema.model_validate(result)
        except SQLAlchemyError:
            log.exception(f"Database error retrieving movie by torrent_id {torrent_id}")
            raise

    async def get_movie_ids_due_for_metadata(
        self, *, older_than: datetime, limit: int = 200
    ) -> list[MovieId]:
        """Movie PKs whose metadata refresh is due (SQL-filtered, bounded)."""
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        stmt = (
            select(Movie.id)
            .where(
                or_(
                    Movie.last_metadata_check.is_(None),
                    Movie.last_metadata_check < older_than,
                ),
                or_(
                    Movie.metadata_failure_backoff_until.is_(None),
                    Movie.metadata_failure_backoff_until <= now,
                ),
            )
            .order_by(Movie.last_metadata_check.asc().nulls_first())
            .limit(limit)
        )
        return typing_cast(
            "list[MovieId]", list((await self.db.execute(stmt)).scalars().all())
        )

    async def stamp_metadata_check(self, movie_id: MovieId) -> None:
        from datetime import UTC, datetime

        db_movie = await self.db.get(Movie, movie_id)
        if db_movie:
            db_movie.last_metadata_check = datetime.now(UTC)
            db_movie.metadata_failure_backoff_until = None
            await self.db.flush()

    async def mark_metadata_failure(
        self, movie_id: MovieId, backoff_until: datetime
    ) -> None:
        from datetime import UTC, datetime

        db_movie = await self.db.get(Movie, movie_id)
        if db_movie:
            db_movie.last_metadata_check = datetime.now(UTC)
            db_movie.metadata_failure_backoff_until = backoff_until
            await self.db.flush()

    async def set_auto_download_backoff(
        self, movie_id: MovieId, until: datetime
    ) -> None:
        db_movie = await self.db.get(Movie, movie_id)
        if db_movie:
            db_movie.auto_download_backoff_until = until
            await self.db.flush()

    async def update_movie_attributes(
        self,
        movie_id: MovieId,
        name: str | None = None,
        overview: str | None = None,
        year: int | None = None,
        release_date: date | None = None,
        imdb_id: str | None = None,
        continuous_download: bool | None = ...,
        vote_average: float | None = ...,
        content_rating: str | None = ...,
        runtime: int | None = ...,
        genres: list[str] | None = ...,
        cast: list[str] | None = ...,
        preferred_quality: list[str] | None = ...,
        preferred_codec: list[str] | None = ...,
        subtitle_languages: list[str] | None = ...,
    ) -> tuple[MovieSchema, bool]:
        """
        Update attributes of an existing movie.

        Returns a tuple of (updated movie, whether any fields changed).
        """
        db_movie = await self.db.get(Movie, movie_id)
        if not db_movie:
            msg = f"Movie with id {movie_id} not found."
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

        if name is not None and db_movie.name != name:
            db_movie.name = name
            updated = True
        if overview is not None and db_movie.overview != overview:
            db_movie.overview = overview
            updated = True
        if year is not None and db_movie.year != year:
            db_movie.year = year
            updated = True
        if release_date is not None and db_movie.release_date != release_date:
            db_movie.release_date = release_date
            updated = True
        if imdb_id is not None and db_movie.imdb_id != imdb_id:
            db_movie.imdb_id = imdb_id
            updated = True
        if (
            continuous_download is not ...
            and db_movie.continuous_download != continuous_download
        ):
            db_movie.continuous_download = continuous_download
            updated = True
        if vote_average is not ... and not _floats_equal(
            db_movie.vote_average, vote_average
        ):
            db_movie.vote_average = vote_average
            updated = True
        if content_rating is not ... and db_movie.content_rating != content_rating:
            db_movie.content_rating = content_rating
            updated = True
        if runtime is not ... and db_movie.runtime != runtime:
            db_movie.runtime = runtime
            updated = True
        if genres is not ... and not _lists_equal(db_movie.genres, genres):
            db_movie.genres = genres
            updated = True
        if cast is not ... and not _lists_equal(db_movie.cast, cast):
            db_movie.cast = cast
            updated = True
        if preferred_quality is not ... and not _lists_equal(
            db_movie.preferred_quality, preferred_quality
        ):
            db_movie.preferred_quality = preferred_quality
            updated = True
        if preferred_codec is not ... and not _lists_equal(
            db_movie.preferred_codec, preferred_codec
        ):
            db_movie.preferred_codec = preferred_codec
            updated = True
        if subtitle_languages is not ... and not _lists_equal(
            db_movie.subtitle_languages, subtitle_languages
        ):
            db_movie.subtitle_languages = subtitle_languages
            updated = True

        if updated:
            await self.db.flush()
            await self.db.refresh(db_movie)
        return MovieSchema.model_validate(db_movie), updated
