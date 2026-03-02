"""SSO (Single Sign-On) authentication module.

Provides OAuth2 authentication via external identity providers.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.sso.models import UserSSOConnection
    from src.app.modules.sso.service import SSOService
    from src.app.modules.sso.router import router as sso_router
"""

__all__ = [
    "UserSSOConnection",
    "SSOService",
    "router",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "router":
        from src.app.modules.sso.router import router

        return router
    if name == "SSOService":
        from src.app.modules.sso.service import SSOService

        return SSOService
    if name == "UserSSOConnection":
        from src.app.modules.sso.models import UserSSOConnection

        return UserSSOConnection
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
