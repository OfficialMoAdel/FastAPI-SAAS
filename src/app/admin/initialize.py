from collections.abc import AsyncGenerator

from crudadmin import CRUDAdmin
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.views import register_admin_views
from src.app.core.config import EnvironmentOption, settings
from src.app.core.db.database import get_db_context


async def _get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Wrapper to provide CRUDAdmin-compatible session generator.

    CRUDAdmin expects Callable[[], AsyncGenerator[AsyncSession, None]]
    but async_get_db returns _AsyncGeneratorContextManager due to @asynccontextmanager.
    """
    async with get_db_context() as session:
        yield session


def create_admin_interface() -> CRUDAdmin | None:
    """Create and configure the admin interface."""
    if not settings.CRUD_ADMIN_ENABLED:
        return None

    session_backend = "memory"
    redis_config = None

    if settings.CRUD_ADMIN_REDIS_ENABLED:
        session_backend = "redis"
        redis_config = {
            "host": settings.CRUD_ADMIN_REDIS_HOST,
            "port": settings.CRUD_ADMIN_REDIS_PORT,
            "db": settings.CRUD_ADMIN_REDIS_DB,
            "password": (
                settings.CRUD_ADMIN_REDIS_PASSWORD.get_secret_value()
                if settings.CRUD_ADMIN_REDIS_PASSWORD
                and settings.CRUD_ADMIN_REDIS_PASSWORD.get_secret_value() != "None"
                else None
            ),
        }

    admin = CRUDAdmin(
        session=_get_db_session,  # type: ignore[arg-type]
        SECRET_KEY=settings.SECRET_KEY.get_secret_value(),
        mount_path=settings.CRUD_ADMIN_MOUNT_PATH,
        session_backend=session_backend,
        redis_config=redis_config,
        allowed_ips=settings.CRUD_ADMIN_ALLOWED_IPS_LIST if settings.CRUD_ADMIN_ALLOWED_IPS_LIST else None,
        allowed_networks=settings.CRUD_ADMIN_ALLOWED_NETWORKS_LIST
        if settings.CRUD_ADMIN_ALLOWED_NETWORKS_LIST
        else None,
        max_sessions_per_user=settings.CRUD_ADMIN_MAX_SESSIONS,
        session_timeout_minutes=settings.CRUD_ADMIN_SESSION_TIMEOUT,
        secure_cookies=settings.SESSION_SECURE_COOKIES,
        enforce_https=settings.ENVIRONMENT == EnvironmentOption.PRODUCTION,
        track_events=settings.CRUD_ADMIN_TRACK_EVENTS,
        track_sessions_in_db=settings.CRUD_ADMIN_TRACK_SESSIONS,
        initial_admin=(
            {
                "username": settings.ADMIN_USERNAME,
                # BUG FIX: get_secret_value() is called here explicitly so we can also check
                # that the secret is non-empty. SecretStr("") is truthy, so the outer
                # `and settings.ADMIN_PASSWORD` guard would pass even for empty passwords.
                "password": settings.ADMIN_PASSWORD.get_secret_value(),
            }
            if settings.ADMIN_USERNAME
            and settings.ADMIN_PASSWORD
            and settings.ADMIN_PASSWORD.get_secret_value()  # BUG FIX: reject empty string
            else None
        ),
    )

    register_admin_views(admin)

    return admin
