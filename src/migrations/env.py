import asyncio
from logging.config import fileConfig
import os

from alembic import context

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import all models for autogenerate to detect them
# Core models
from src.app.core.config import settings
from src.app.core.db.database import Base
from src.app.core.db.token_blacklist import TokenBlacklist  # noqa: F401
from src.app.modules.ai import models  # noqa: F401

# Import all modules models to register them with Base.metadata
from src.app.modules.api_keys import models as api_keys_models  # noqa: F401
from src.app.modules.audit_logs import models as audit_logs_models  # noqa: F401
from src.app.modules.auth import models as auth_models  # noqa: F401
from src.app.modules.billing import models as billing_models  # noqa: F401
from src.app.modules.feature_flags import models as feature_flags_models  # noqa: F401
from src.app.modules.files import models as files_models  # noqa: F401
from src.app.modules.magic_link import models as magic_link_models  # noqa: F401
from src.app.modules.notifications import models as notifications_models  # noqa: F401
from src.app.modules.passkey import models as passkey_models  # noqa: F401
from src.app.modules.posts import models as posts_models  # noqa: F401
from src.app.modules.rate_limits import models as rate_limits_models  # noqa: F401
from src.app.modules.rbac import models as rbac_models  # noqa: F401
from src.app.modules.sso import models as sso_models  # noqa: F401
from src.app.modules.sso_settings import models as sso_settings_models  # noqa: F401
from src.app.modules.storage_settings import models as storage_settings_models  # noqa: F401
from src.app.modules.tenants import models as tenants_models  # noqa: F401
from src.app.modules.tiers import models as tiers_models  # noqa: F401
from src.app.modules.user_preferences import models as user_preferences_models  # noqa: F401
from src.app.modules.users import models as users_models  # noqa: F401
from src.app.modules.webhooks import models as webhooks_models  # noqa: F401

# Debug: Print registered tables
# print("Registered tables:", Base.metadata.tables.keys())


# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

config.set_main_option(
    "sqlalchemy.url",
    f"{settings.POSTGRES_ASYNC_PREFIX}{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}",
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
        connection=connection,
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
    run_migrations_online()
