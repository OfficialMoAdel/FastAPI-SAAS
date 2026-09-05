"""Users module - user account management.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.users.service import get_user_service
    from src.app.modules.users.router import router as users_router
    from src.app.modules.users.crud import crud_users
"""

__all__ = [
    "User",
    "crud_users",
    "get_user_service",
    "users_router",
]

# Register user domain security lookups with core.security (DIP seam / PR-12)
from typing import Any

from src.app.core.security.jwt_service import (
    register_token_version_lookup,
    register_user_tenant_lookup,
)


async def _users_token_version_lookup(username_or_email: str) -> int | None:
    from src.app.core.db.database import local_session
    from src.app.modules.users.crud import crud_users

    async with local_session() as session:
        if "@" in username_or_email:
            user = await crud_users.get(session, email=username_or_email)
        else:
            user = await crud_users.get(session, username=username_or_email)
    if not user:
        return None
    return user.get("token_version")


async def _users_tenant_lookup(username_or_email: str, db: Any) -> Any | None:
    from sqlalchemy import select

    from src.app.modules.users.models import User

    stmt = (
        select(User.tenant_id).where((User.username == username_or_email) | (User.email == username_or_email)).limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar()


def register_users_security_lookups() -> None:
    """Explicitly wire user-domain security lookups (idempotent).

    Prefer calling this from application lifespan so wiring order is
    deterministic; the import-time call below is kept for back-compat.
    """
    register_token_version_lookup(_users_token_version_lookup)
    register_user_tenant_lookup(_users_tenant_lookup)


register_users_security_lookups()


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "users_router":
        from src.app.modules.users.router import router as users_router

        return users_router
    if name == "get_user_service":
        from src.app.modules.users.service import get_user_service

        return get_user_service
    if name == "crud_users":
        from src.app.modules.users.crud import crud_users

        return crud_users
    if name == "User":
        from src.app.modules.users.models import User

        return User
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
