"""User Preferences module.

Provides user preference management (timezone, language, currency, etc.).

Exports:
    - UserPreferences: SQLAlchemy model for user preferences
    - UserPreferencesService: Service layer for preferences operations
    - router: FastAPI router with preferences endpoints
"""

from src.app.modules.user_preferences.router import router

__all__ = ["router"]
