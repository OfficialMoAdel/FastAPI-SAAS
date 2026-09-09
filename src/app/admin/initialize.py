from collections.abc import AsyncGenerator

from crudadmin import CRUDAdmin
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.admin.views import register_admin_views
from src.app.core.config import EnvironmentOption, settings
from src.app.core.db.database import get_db_context


async def _get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide CRUDAdmin-compatible sessions with a cross-tenant contract.

    C-2 tenancy contract: the admin panel is an operator surface that manages
    ALL tenants, so its sessions run inside an audited `bypass_rls` context
    (fail-closed: if the bypass audit write fails, the session fails to
    yield). TenantMiddleware exempts the admin mount path, so no
    `X-Tenant-ID` header is required or honored here — every admin query
    intentionally sees all tenants. See `src/app/admin/AGENTS.md`.
    """
    from src.app.core.db.rls import bypass_rls

    async with get_db_context() as session:
        async with bypass_rls(session, reason="CRUDAdmin panel data access (cross-tenant operator view)"):
            yield session


def create_admin_interface() -> CRUDAdmin | None:
    """Create and configure the admin interface."""
    if not settings.admin.CRUD_ADMIN_ENABLED:
        return None

    session_backend = "memory"
    redis_config = None

    if settings.admin.CRUD_ADMIN_REDIS_ENABLED:
        session_backend = "redis"
        redis_config = {
            "host": settings.admin.CRUD_ADMIN_REDIS_HOST,
            "port": settings.admin.CRUD_ADMIN_REDIS_PORT,
            "db": settings.admin.CRUD_ADMIN_REDIS_DB,
            "password": (
                settings.admin.CRUD_ADMIN_REDIS_PASSWORD.get_secret_value()
                if settings.admin.CRUD_ADMIN_REDIS_PASSWORD
                and settings.admin.CRUD_ADMIN_REDIS_PASSWORD.get_secret_value() != "None"
                else None
            ),
        }

    admin = CRUDAdmin(
        session=_get_db_session,  # type: ignore[arg-type]
        SECRET_KEY=settings.SECRET_KEY.get_secret_value(),
        mount_path=settings.admin.CRUD_ADMIN_MOUNT_PATH,
        session_backend=session_backend,
        redis_config=redis_config,
        allowed_ips=settings.admin.CRUD_ADMIN_ALLOWED_IPS_LIST if settings.admin.CRUD_ADMIN_ALLOWED_IPS_LIST else None,
        allowed_networks=settings.admin.CRUD_ADMIN_ALLOWED_NETWORKS_LIST
        if settings.admin.CRUD_ADMIN_ALLOWED_NETWORKS_LIST
        else None,
        max_sessions_per_user=settings.admin.CRUD_ADMIN_MAX_SESSIONS,
        session_timeout_minutes=settings.admin.CRUD_ADMIN_SESSION_TIMEOUT,
        secure_cookies=settings.admin.SESSION_SECURE_COOKIES,
        enforce_https=settings.env.ENVIRONMENT == EnvironmentOption.PRODUCTION,
        track_events=settings.admin.CRUD_ADMIN_TRACK_EVENTS,
        track_sessions_in_db=settings.admin.CRUD_ADMIN_TRACK_SESSIONS,
        initial_admin=(
            {
                "username": settings.first_user.ADMIN_USERNAME,
                # BUG FIX: get_secret_value() is called here explicitly so we can also check
                # that the secret is non-empty. SecretStr("") is truthy, so the outer
                # `and settings.first_user.ADMIN_PASSWORD` guard would pass even for empty passwords.
                "password": settings.first_user.ADMIN_PASSWORD.get_secret_value(),
            }
            if settings.first_user.ADMIN_USERNAME
            and settings.first_user.ADMIN_PASSWORD
            and settings.first_user.ADMIN_PASSWORD.get_secret_value()  # BUG FIX: reject empty string
            else None
        ),
    )

    register_admin_views(admin)

    return admin
