from logging.config import fileConfig

from sqlalchemy import (
    engine_from_config,
    pool,
)

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata

from miramedia.auth.api_tokens import UserApiToken  # noqa: E402, F401
from miramedia.auth.db import OAuthAccount, User  # noqa: E402
from miramedia.database import Base  # noqa: E402
from miramedia.imports.models import (  # noqa: E402, F401
    IgnoredImportPath,
    ImportBatch,
    ScanResultCache,
    ScanRun,
)
from miramedia.indexers.models import (  # noqa: E402, F401
    IndexerQueryResult,
    IndexerSite,
)
from miramedia.logs.models import ActivityLog  # noqa: E402
from miramedia.media_inventory import MediaFileInventory  # noqa: E402
from miramedia.movies.models import Movie, MovieFile  # noqa: E402
from miramedia.notifications.models import Notification  # noqa: E402
from miramedia.requests.models import MediaRequest  # noqa: E402, F401
from miramedia.settings.models import SystemConfigOverride  # noqa: E402, F401
from miramedia.shows.models import (  # noqa: E402
    Episode,
    EpisodeFile,
    Season,
    Show,
)
from miramedia.subtitles.arr_ids import ArrIdMap  # noqa: E402, F401
from miramedia.subtitles.models import SubtitleRecord  # noqa: E402, F401
from miramedia.torrents.models import (  # noqa: E402, F401
    ManualParseToken,
    Torrent,
    TorrentBlock,
    TorrentHistory,
)

target_metadata = Base.metadata

# this is to keep pycharm from complaining about/optimizing unused imports
# noinspection PyStatementEffect
__all__ = [
    "ActivityLog",
    "Episode",
    "EpisodeFile",
    "ImportBatch",
    "IndexerQueryResult",
    "MediaFileInventory",
    "Movie",
    "MovieFile",
    "Notification",
    "OAuthAccount",
    "ScanResultCache",
    "ScanRun",
    "Season",
    "Show",
    "Torrent",
    "User",
]


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


from miramedia.database.migration_url import (  # noqa: E402
    migration_url_for_alembic_config,
)

config.set_main_option("sqlalchemy.url", migration_url_for_alembic_config())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    def include_object(
        _object: object | None,
        name: str | None,
        type_: str | None,
        _reflected: bool | None,
        _compare_to: object | None,
    ) -> bool:
        if type_ == "table" and name == "apscheduler_jobs":
            return False
        return True

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
