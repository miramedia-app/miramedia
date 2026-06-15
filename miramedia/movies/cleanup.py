import logging
from types import EllipsisType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import MiraMediaConfig
from miramedia.movies.models import Movie

log = logging.getLogger(__name__)


async def cleanup_stale_movie_preferences(
    db: AsyncSession, config: MiraMediaConfig
) -> None:
    """Filter Movie.preferred_quality / preferred_codec entries to only enabled
    option names. An empty list (after filtering a non-empty list) is reset to
    NULL so the movie falls back to the global default. NULL and [] (explicit
    "Any" picked by the user) are left alone.
    """
    enabled_quality = {
        opt.name for opt in config.indexers.quality_options if opt.enabled
    }
    enabled_codec = {opt.name for opt in config.indexers.codec_options if opt.enabled}

    def _filter(
        value: list[str] | None, enabled: set[str]
    ) -> list[str] | None | EllipsisType:
        if value is None or value == []:
            return ...
        filtered = [n for n in value if n in enabled]
        if filtered == list(value):
            return ...
        return filtered or None

    q_cleared = 0
    quality_stmt = select(Movie).where(Movie.preferred_quality.isnot(None))
    quality_movies = (await db.execute(quality_stmt)).scalars().unique().all()
    for movie in quality_movies:
        new = _filter(movie.preferred_quality, enabled_quality)
        if new is not ...:
            log.info(
                "Cleaning Movie.preferred_quality=%r → %r on %s",
                movie.preferred_quality,
                new,
                movie.name,
            )
            movie.preferred_quality = new
            q_cleared += 1

    c_cleared = 0
    codec_stmt = select(Movie).where(Movie.preferred_codec.isnot(None))
    codec_movies = (await db.execute(codec_stmt)).scalars().unique().all()
    for movie in codec_movies:
        new = _filter(movie.preferred_codec, enabled_codec)
        if new is not ...:
            log.info(
                "Cleaning Movie.preferred_codec=%r → %r on %s",
                movie.preferred_codec,
                new,
                movie.name,
            )
            movie.preferred_codec = new
            c_cleared += 1

    if q_cleared or c_cleared:
        await db.commit()
        log.info(
            "Cleaned %d stale Movie quality + %d stale Movie codec preferences",
            q_cleared,
            c_cleared,
        )
