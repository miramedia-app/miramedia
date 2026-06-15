import logging
from types import EllipsisType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from miramedia.config import MiraMediaConfig
from miramedia.shows.models import Show

log = logging.getLogger(__name__)


async def cleanup_stale_show_preferences(
    db: AsyncSession, config: MiraMediaConfig
) -> None:
    """Filter Show.preferred_quality / preferred_codec entries to only enabled
    option names. An empty list (after filtering a non-empty list) is reset to
    NULL so the show falls back to the global default. NULL and [] (explicit
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
        # Original had names but none survived → fall back to global default.
        return filtered or None

    q_cleared = 0
    stmt = select(Show).where(Show.preferred_quality.isnot(None))
    shows = (await db.execute(stmt)).scalars().all()
    for show in shows:
        new = _filter(show.preferred_quality, enabled_quality)
        if new is not ...:
            log.info(
                "Cleaning Show.preferred_quality=%r → %r on %s",
                show.preferred_quality,
                new,
                show.name,
            )
            show.preferred_quality = new
            q_cleared += 1

    c_cleared = 0
    stmt = select(Show).where(Show.preferred_codec.isnot(None))
    shows = (await db.execute(stmt)).scalars().all()
    for show in shows:
        new = _filter(show.preferred_codec, enabled_codec)
        if new is not ...:
            log.info(
                "Cleaning Show.preferred_codec=%r → %r on %s",
                show.preferred_codec,
                new,
                show.name,
            )
            show.preferred_codec = new
            c_cleared += 1

    if q_cleared or c_cleared:
        await db.commit()
        log.info(
            "Cleaned %d stale Show quality + %d stale Show codec preferences",
            q_cleared,
            c_cleared,
        )
