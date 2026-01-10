"""Auth module - authentication and authorization.

This module provides authentication services including:
- Login/logout functionality
- Token refresh
- Token blacklisting
- Authentication audit logging

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.auth.service import AuthService, get_auth_service
    from src.app.modules.auth.router import router as auth_router
    from src.app.modules.auth.schemas import Token, TokenData
    from src.app.modules.auth.audit import auth_audit
"""

# Lazy imports - do not eagerly import router to avoid circular imports
# The router is imported directly where needed (api/v1/__init__.py)

__all__ = [
    "auth_router",
    "AuthService",
    "get_auth_service",
    "auth_audit",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "auth_router":
        from .router import router as auth_router

        return auth_router
    if name == "AuthService":
        from .service import AuthService

        return AuthService
    if name == "get_auth_service":
        from .service import get_auth_service

        return get_auth_service
    if name == "auth_audit":
        from .audit import auth_audit

        return auth_audit
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
