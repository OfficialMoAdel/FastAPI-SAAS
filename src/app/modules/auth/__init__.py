"""Auth module - authentication and authorization.

This module provides authentication services including:
- Login/logout functionality
- Token refresh
- Token blacklisting

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.auth.service import auth_service
    from src.app.modules.auth.router import router as auth_router
    from src.app.modules.auth.schemas import Token, TokenData
"""

# Lazy imports - do not eagerly import router to avoid circular imports
# The router is imported directly where needed (api/v1/__init__.py)

__all__ = [
    "auth_router",
    "auth_service",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "auth_router":
        from .router import router as auth_router

        return auth_router
    if name == "auth_service":
        from .service import auth_service

        return auth_service
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
