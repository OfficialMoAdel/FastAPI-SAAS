from fastapi import APIRouter

from src.app.api.monitoring import router as monitoring_router

router = APIRouter(prefix="/api")
router.include_router(monitoring_router, tags=["monitoring"])


def get_router() -> APIRouter:
    """Get the main API router with v1 routes included.

    v1 is imported lazily: feature routers (e.g. users.router) import
    ``src.app.api.deps``, which initializes this package — a top-level
    v1 import here would circle back into the partially initialized
    router module. get_v1_router() itself is cycle-free (verified).
    """
    from src.app.api.v1 import get_v1_router

    main_router = APIRouter(prefix="/api")
    main_router.include_router(monitoring_router, tags=["monitoring"])
    main_router.include_router(get_v1_router())
    return main_router
