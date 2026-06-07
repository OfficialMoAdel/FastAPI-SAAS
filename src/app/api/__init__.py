from fastapi import APIRouter

from src.app.api.monitoring import router as monitoring_router

# Lazy load v1 router to avoid circular dependency
# from src.app.api.v1 import router as v1_router

router = APIRouter(prefix="/api")
# router.include_router(v1_router)
router.include_router(monitoring_router, tags=["monitoring"])


_cached_router = None


def get_router() -> APIRouter:
    """Get the main API router with v1 routes included.

    Lazy loading v1 router avoids circular imports with dependencies/security.
    """
    global _cached_router
    if _cached_router is not None:
        return _cached_router

    from src.app.api.v1 import router as v1_router

    main_router = APIRouter(prefix="/api")
    main_router.include_router(monitoring_router, tags=["monitoring"])
    main_router.include_router(v1_router)

    _cached_router = main_router
    return main_router
