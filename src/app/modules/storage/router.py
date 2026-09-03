"""Storage routers.

Exports:
- files_router: File operations (/files)
- settings_router: Storage settings (/storage)
- router: Unified router combining both
"""

from __future__ import annotations

from fastapi import APIRouter

from src.app.modules.storage.files_router import router as files_router
from src.app.modules.storage.settings_router import router as settings_router

# Unified storage router
router = APIRouter()
router.include_router(files_router)
router.include_router(settings_router)

storage_settings_router = settings_router

__all__ = [
    "router",
    "files_router",
    "settings_router",
    "storage_settings_router",
]
