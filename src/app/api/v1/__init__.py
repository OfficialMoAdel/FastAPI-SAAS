"""v1 API composition root (P2: grouped aggregators).

The former flat 35-router include list lives in three group files:
``core.py`` (everything except billing/admin), ``billing.py`` (billing +
API-compat sub-routers), and ``admin.py`` (superuser-gated mounts).
Include order across the three groups preserves the historical sequence,
so OpenAPI paths are unchanged; edit the group files, not this composer.
"""

from fastapi import APIRouter

from src.app.api.v1.admin_group import router as admin_group_router
from src.app.api.v1.billing import router as billing_group_router
from src.app.api.v1.core import router as core_group_router

router = APIRouter(prefix="/v1")
router.include_router(core_group_router)
router.include_router(billing_group_router)
router.include_router(admin_group_router)


def get_v1_router() -> APIRouter:
    """Get the v1 API router (module-level singleton, kept for backward compatibility)."""
    return router
