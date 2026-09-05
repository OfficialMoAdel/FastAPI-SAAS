"""Storage unified router.

Standardized layout combining storage/routers/ per PR-11 / Finding #15.
Aggregates the files + settings sub-routers; use ``router`` for both or
import ``files_router`` / ``settings_router`` directly.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.app.modules.storage.routers.files import router as files_router
from src.app.modules.storage.routers.settings import router as settings_router

router = APIRouter()
router.include_router(files_router)
router.include_router(settings_router)

storage_settings_router = settings_router

__all__ = [
    "files_router",
    "router",
    "settings_router",
    "storage_settings_router",
]
