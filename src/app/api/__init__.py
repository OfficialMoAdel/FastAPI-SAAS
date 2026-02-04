from fastapi import APIRouter

from ..api.v1 import router as v1_router
from .monitoring import router as monitoring_router

router = APIRouter(prefix="/api")
router.include_router(v1_router)
router.include_router(monitoring_router, tags=["monitoring"])
