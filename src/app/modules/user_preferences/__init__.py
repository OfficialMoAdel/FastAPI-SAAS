"""User Preferences module.

Provides user preference management (timezone, language, currency, etc.).

Components are imported lazily to avoid circular imports.
Direct imports:
    from src.app.modules.user_preferences.models import UserPreferences
    from src.app.modules.user_preferences.service import UserPreferencesService
    from src.app.modules.user_preferences.router import router as user_preferences_router
"""

__all__ = [
    "UserPreferences",
    "UserPreferencesService",
    "router",
]


def __getattr__(name: str):
    """Lazy import to avoid circular imports."""
    if name == "router":
        from .router import router

        return router
    if name == "UserPreferencesService":
        from .service import UserPreferencesService

        return UserPreferencesService
    if name == "UserPreferences":
        from .models import UserPreferences

        return UserPreferences
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
