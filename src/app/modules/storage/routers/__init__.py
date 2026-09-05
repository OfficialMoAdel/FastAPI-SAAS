"""Storage sub-routers package.

Standardized naming per PR-11 / Finding #15.
"""

from src.app.modules.storage.routers.files import router as files_router
from src.app.modules.storage.routers.settings import router as settings_router

storage_settings_router = settings_router

__all__ = [
    "files_router",
    "settings_router",
    "storage_settings_router",
]
