"""SSO Settings administration module.

Provides tenant-level SSO provider configuration management.

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.sso_settings.models import SSOSettings
    from src.app.modules.sso_settings.service import sso_settings_service
    from src.app.modules.sso_settings.router import router as sso_settings_router
"""

__all__ = [
    "SSOSettings",
    "sso_settings_service",
    "router",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "router":
        from src.app.modules.sso_settings.router import router

        return router
    if name == "sso_settings_service":
        from src.app.modules.sso_settings.service import sso_settings_service

        return sso_settings_service
    if name == "SSOSettings":
        from src.app.modules.sso_settings.models import SSOSettings

        return SSOSettings
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
