import asyncio
import importlib
import importlib.util
import pkgutil
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# R3-04: dynamic model discovery — replaces the manual 20-import list so new
# modules are auto-registered for autogenerate without editing this file.
# Each module's ``models`` (if present) is imported for its Base.metadata
# side-effect; import failures for modules without models are ignored.
import src.app.modules as _modules_pkg
from src.app.core.config import settings
from src.app.core.db.database import Base
from src.app.core.db.token_blacklist import TokenBlacklist  # noqa: F401

for _mod in pkgutil.iter_modules(_modules_pkg.__path__):
    _name = f"src.app.modules.{_mod.name}.models"
    if importlib.util.find_spec(_name) is None:
        continue
    importlib.import_module(_name)

# Debug: Print registered tables
# print("Registered tables:", Base.metadata.tables.keys())


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

config.set_main_option(
    "sqlalchemy.url",
    f"{settings.POSTGRES_ASYNC_PREFIX}{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD.get_secret_value()}@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}",
)


if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Note: We don't import app.models here because it causes duplicate table
# registration errors due to circular imports and multiple import paths.
# For autogenerate, models need to be imported. For simple migrations,
# the migration scripts themselves define the schema changes.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine, though an Engine is acceptable here as well.  By
    skipping the Engine creation we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine and associate a connection with the context."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    # asyncio already imported at top of file
    run_migrations_online()
