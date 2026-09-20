"""v1 admin router group (P2 aggregator).

Both mounts are superuser-gated and keep their historical ``/admin``
prefix and dependency wiring.
"""

from fastapi import APIRouter, Depends

from src.app.api.deps import get_current_superuser
from src.app.api.v1.admin.circuit_breakers import router as circuit_breakers_router
from src.app.api.v1.admin.jobs import router as jobs_router

router = APIRouter()
# Admin endpoints - PROTECTED with superuser authentication
router.include_router(
    circuit_breakers_router,
    prefix="/admin",
    dependencies=[Depends(get_current_superuser)],
)
router.include_router(
    jobs_router,
    prefix="/admin",
    dependencies=[Depends(get_current_superuser)],
)
