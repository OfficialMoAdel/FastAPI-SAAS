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


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "users_router":
        from .router import router as users_router

        return users_router
    if name == "get_user_service":
        from .service import get_user_service

        return get_user_service
    if name == "crud_users":
        from .crud import crud_users

        return crud_users
    if name == "User":
        from .models import User

        return User
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
